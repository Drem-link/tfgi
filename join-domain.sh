#!/bin/bash

# Выход при любой ошибке
set -e

echo "=== Шаг 1: Настройка криптополитики LEGACY ==="
update-crypto-policies --set LEGACY

echo "=== Шаг 2: Настройка /etc/krb5.conf ==="
cat << 'EOF' > /etc/krb5.conf
[libdefaults]
    default_realm = TFI.RU
    dns_lookup_realm = false
    dns_lookup_kdc = true
    rdns = false
    ticket_lifetime = 24h
    forwardable = true
    allow_weak_crypto = true
    default_tgt_enctypes = rc4-hmac
    default_tkt_enctypes = rc4-hmac
    permitted_enctypes = rc4-hmac
EOF

echo "=== Шаг 3: Настройка /etc/samba/smb.conf ==="
CURRENT_HOSTNAME=$(hostname -s | tr 'a-z' 'A-Z')

cat << EOF > /etc/samba/smb.conf
[global]
    workgroup = TFI
    security = ads
    realm = TFI.RU
    netbios name = $CURRENT_HOSTNAME

    # Жизненно важно для Windows Server 2003
    client min protocol = NT1
    client max protocol = NT1
    client lanman auth = yes
    ntlm auth = yes
    allow trusted domains = no

    # Отключение обязательной подписи пакетов и шифрования канала
    client signing = no
    client ipc signing = no
    client schannel = no
    server schannel = no
    reject md5 servers = no

    # Настройки авторизации пользователей
    idmap config * : backend = tdb
    idmap config * : range = 3000-7999
    idmap config TFI : backend = rid
    idmap config TFI : range = 10000-999999

    winbind enum users = yes
    winbind enum groups = yes
    winbind use default domain = yes
    winbind refresh tickets = yes

[domain-instruction]
    path = /srv/share/domain-instruction
    browseable = yes
    read only = no
    guest ok = yes
    public = yes
    writable = yes
    create mask = 0777
    directory mask = 0777
    force user = nobody
    force group = nobody
EOF

echo "=== Шаг 4: Подготовка системных служб авторизации ==="
# Останавливаем sssd, так как он мешает winbind
systemctl stop sssd || true
systemctl disable sssd || true

# Включаем профиль winbind
authselect select winbind with-mkhomedir --force

# Снимаем маску с winbind и активируем службы
systemctl unmask winbind
systemctl enable --now winbind
systemctl enable --now oddjobd.service
systemctl enable --now smb

echo "=== Шаг 5: Получение билета Kerberos ==="
echo "Введите пароль администратора домена Geolcom2:"
kdestroy || true
kinit Geolcom2

echo "=== Шаг 6: Ввод в домен через RPC ==="
net rpc join -U Geolcom2

echo "=== Шаг 7: Финальный перезапуск служб ==="
systemctl restart smb
systemctl restart winbind

echo "=== ВСЁ ГОТОВО! Проверяем пользователей домена... ==="
sleep 2
wbinfo -u
