#!/usr/bin/env bash
set -Eeuo pipefail

DOMAIN="${DOMAIN:-tfi.ru}"
WORKGROUP="${WORKGROUP:-TFI}"
DC="${DC:-dc0.tfi.ru}"
SAMBA_CONF=/etc/samba/smb.conf
PAM_CONF=/etc/pam.d/system-auth
NSS_CONF=/etc/nsswitch.conf
HOST_SHORT="$(hostname -s)"
HOST_NETBIOS="${HOST_SHORT^^}"

if [[ ! "$DOMAIN" =~ ^[A-Za-z0-9.-]+$ ||
      ! "$WORKGROUP" =~ ^[A-Za-z0-9_-]{1,15}$ ||
      ! "$DC" =~ ^[A-Za-z0-9.-]+$ ]]; then
  echo "DOMAIN, WORKGROUP, or DC contains unsupported characters." >&2
  exit 1
fi

if (( EUID != 0 )); then
  echo "Run this script in a terminal with: sudo $0" >&2
  exit 1
fi

for command in net winbindd wbinfo testparm python3 systemctl getent id hostname; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "Required command not found: $command (install the Samba packages first)" >&2
    exit 1
  fi
done

if [[ ! -f "$PAM_CONF" || ! -f "$NSS_CONF" ]]; then
  echo "Expected Arch PAM/NSS files are missing; no changes made." >&2
  exit 1
fi

if (( ${#HOST_NETBIOS} > 15 )); then
  echo "Computer name '$HOST_NETBIOS' exceeds the 15-character NetBIOS limit." >&2
  exit 1
fi

read -r -p "Domain account allowed to join computers (e.g. geolcom2): " DOMAIN_USER
if [[ -z "$DOMAIN_USER" || "$DOMAIN_USER" == *\\* || "$DOMAIN_USER" == *@* ]]; then
  echo "Enter a short domain username, without a domain prefix." >&2
  exit 1
fi

cat <<EOF

This configures Winbind domain login on Omarchy/Arch for:
  Domain:    $DOMAIN ($WORKGROUP)
  DC:        $DC
  Computer:  $HOST_NETBIOS

Compatibility with old domain controllers requires weak settings:
SMB1 is enabled for Samba clients, and Samba Netlogon checks are weakened.
Samba warns that these settings reduce protection against CVE-2020-1472
and CVE-2022-38023. Do not use this on an untrusted network.

The script backs up existing Samba, PAM, and NSS configs. It never stores
the domain password; Samba will prompt for it in this terminal.
EOF
read -r -p "Continue with these security tradeoffs? Type YES: " answer
if [[ "$answer" != YES ]]; then
  echo "Cancelled; no changes made."
  exit 0
fi

timestamp="$(date +%Y%m%d-%H%M%S)"
backup_file() {
  local file="$1"
  if [[ -e "$file" ]]; then
    cp -a -- "$file" "$file.before-domain-setup-$timestamp"
    echo "Backup: $file.before-domain-setup-$timestamp"
  fi
}

install -d -m 0755 /etc/samba
backup_file "$SAMBA_CONF"
backup_file "$PAM_CONF"
backup_file "$NSS_CONF"

tmp_conf="$(mktemp /etc/samba/smb.conf.XXXXXX)"
trap 'rm -f -- "${tmp_conf:-}"' EXIT
cat >"$tmp_conf" <<EOF
[global]
    workgroup = $WORKGROUP
    realm = ${DOMAIN^^}
    security = ADS
    server role = member server
    netbios name = $HOST_NETBIOS

    client min protocol = NT1
    client schannel = no
    require strong key = no
    reject md5 servers = no

    idmap config * : backend = tdb
    idmap config * : range = 3000-7999
    idmap config $WORKGROUP : backend = rid
    idmap config $WORKGROUP : range = 10000-999999

    winbind use default domain = yes
    winbind enum users = no
    winbind enum groups = no
    template homedir = /home/%U
    template shell = /bin/bash
EOF
chmod 0644 "$tmp_conf"
mv -f -- "$tmp_conf" "$SAMBA_CONF"
tmp_conf=

testparm -s "$SAMBA_CONF" >/dev/null

if ! net -s "$SAMBA_CONF" ads testjoin >/dev/null 2>&1; then
  echo "Joining $DOMAIN through $DC; enter the domain password at the prompt."
  net -s "$SAMBA_CONF" -S "$DC" rpc join -U "$DOMAIN_USER" MEMBER
fi

net -s "$SAMBA_CONF" ads testjoin
systemctl enable --now winbind.service
wbinfo -t --verbose

python3 - "$PAM_CONF" "$NSS_CONF" <<'PY'
from pathlib import Path
import re
import sys

pam_path, nss_path = map(Path, sys.argv[1:])
pam = pam_path.read_text()
nss = nss_path.read_text()

if "pam_winbind.so" not in pam:
    unix_auth = re.compile(r"(?m)^auth\s+\[success=1 default=bad\]\s+pam_unix\.so\b")
    pam, count = unix_auth.subn(
        "auth       [success=2 default=ignore]  pam_winbind.so      try_first_pass\n\\g<0>",
        pam,
        count=1,
    )
    if count != 1:
        raise SystemExit("Could not safely locate the Arch pam_unix auth rule.")

    unix_account = re.compile(r"(?m)^account\s+required\s+pam_unix\.so\b")
    pam, count = unix_account.subn(
        "account    [success=1 default=ignore]  pam_winbind.so\n\\g<0>",
        pam,
        count=1,
    )
    if count != 1:
        raise SystemExit("Could not safely locate the Arch pam_unix account rule.")

if "pam_mkhomedir.so" not in pam:
    limits = re.compile(r"(?m)^session\s+required\s+pam_limits\.so\b")
    pam, count = limits.subn(
        "session    required                    pam_mkhomedir.so    skel=/etc/skel umask=0077\n\\g<0>",
        pam,
        count=1,
    )
    if count != 1:
        raise SystemExit("Could not safely locate the Arch pam_limits session rule.")

for database in ("passwd", "group"):
    line = re.compile(rf"(?m)^({database}:)\s+files(\s+\[[^]]+\])?(.*)$")
    match = line.search(nss)
    if not match:
        if re.search(rf"(?m)^{database}:.*\bwinbind\b", nss):
            continue
        raise SystemExit(f"Could not safely locate the NSS {database} line.")
    if re.search(r"\bwinbind\b", match.group(0)):
        continue
    replacement = f"{match.group(1)} files{match.group(2) or ''} winbind{match.group(3)}"
    nss = line.sub(lambda _: replacement, nss, count=1)

pam_path.write_text(pam)
nss_path.write_text(nss)
PY

if ! getent passwd "$DOMAIN_USER" >/dev/null; then
  echo "Winbind is running, but NSS cannot resolve '$DOMAIN_USER'." >&2
  exit 1
fi

echo
echo "Domain login is configured. Verify with:"
echo "  id '$DOMAIN_USER'"
echo "  getent passwd '$DOMAIN_USER'"
echo "Then test a separate TTY login (Ctrl+Alt+F3) before logging out."
echo "A domain user's home directory is created on first successful login."
