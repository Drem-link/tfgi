#!/bin/bash
# ============================================================
# Скрипт: Ввод Red OS в домен Windows Server 2003
# Автор: Опытным путём
# Домен: tfi.ru
# Контроллер: 192.168.1.2
# ============================================================

set -e  # Остановка при ошибке

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Функция для вывода
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Проверка прав root
if [ "$EUID" -ne 0 ]; then
    log_error "Пожалуйста, запустите скрипт с правами root (sudo)"
    exit 1
fi

# ============================================================
# КОНФИГУРАЦИЯ (ИЗМЕНИТЕ ПОД СЕБЯ)
# ============================================================
DOMAIN="tfi.ru"
DOMAIN_NETBIOS="TFI"
DOMAIN_REALM="TFI.RU"
DC_IP="192.168.1.2"
DC_HOSTNAME="dc0.tfi.ru"
ADMIN_USER="geolcom2"
HOSTNAME="zabbix"
SHARE_PATH="/srv/share/domain-instruction"
SHARE_NAME="domain-instruction"

# ============================================================
# 1. НАСТРОЙКА СЕТИ И HOSTNAME
# ============================================================
log_info "Настройка hostname и /etc/hosts..."

hostnamectl set-hostname "$HOSTNAME"

if ! grep -q "$DC_IP $DC_HOSTNAME" /etc/hosts; then
    echo "$DC_IP $DC_HOSTNAME $DOMAIN" >> /etc/hosts
    log_info "Добавлена запись в /etc/hosts"
fi

# ============================================================
# 2. УСТАНОВКА ПАКЕТОВ
# ============================================================
log_info "Установка необходимых пакетов..."

dnf install -y realmd sssd oddjob oddjob-mkhomedir adcli samba-common samba-client

# ============================================================
# 3. НАСТРОЙКА KRB5.CONF
# ============================================================
log_info "Настройка Kerberos (/etc/krb5.conf)..."

cat > /etc/krb5.conf << EOF
[libdefaults]
    default_realm = $DOMAIN_REALM
    dns_lookup_realm = false
    dns_lookup_kdc = false
    rdns = false
    allow_weak_crypto = true
    default_tgs_enctypes = rc4-hmac
    default_tkt_enctypes = rc4-hmac
    permitted_enctypes = rc4-hmac

[realms]
    $DOMAIN_REALM = {
        kdc = $DC_IP
        admin_server = $DC_IP
    }

[domain_realm]
    .$DOMAIN = $DOMAIN_REALM
    $DOMAIN = $DOMAIN_REALM
EOF

# ============================================================
# 4. НАСТРОЙКА КРИПТОПОЛИТИКИ
# ============================================================
log_info "Настройка криптополитики..."

update-crypto-policies --set DEFAULT:AD-SUPPORT

# ============================================================
# 5. НАСТРОЙКА SAMBA
# ============================================================
log_info "Настройка Samba (/etc/samba/smb.conf)..."

cat > /etc/samba/smb.conf << EOF
[global]
    workgroup = $DOMAIN_NETBIOS
    realm = $DOMAIN_REALM
    security = ADS
    server role = member server
    client min protocol = NT1
    server min protocol = NT1
    kerberos method = secrets and keytab
EOF

# ============================================================
# 6. ВВОД В ДОМЕН
# ============================================================
log_info "Ввод в домен $DOMAIN..."

if ! realm list | grep -q "$DOMAIN"; then
    log_warn "Введите пароль для пользователя $ADMIN_USER@$DOMAIN_REALM"
    realm join --user="$ADMIN_USER" "$DOMAIN"
    
    if [ $? -eq 0 ]; then
        log_info "Успешно введён в домен!"
    else
        log_error "Ошибка ввода в домен"
        exit 1
    fi
else
    log_info "Машина уже в домене"
fi

# ============================================================
# 7. СОЗДАНИЕ KEYTAB (ЕСЛИ НУЖНО)
# ============================================================
if [ ! -f /etc/krb5.keytab ] || [ ! -s /etc/krb5.keytab ]; then
    log_info "Создание /etc/krb5.keytab..."
    
    echo "add_entry -password -p $ADMIN_USER@$DOMAIN_REALM -k 1 -e rc4-hmac" > /tmp/ktutil.cmd
    echo "wkt /etc/krb5.keytab" >> /tmp/ktutil.cmd
    echo "quit" >> /tmp/ktutil.cmd
    
    kinit "$ADMIN_USER@$DOMAIN_REALM" < /dev/null 2>/dev/null || true
    ktutil < /tmp/ktutil.cmd
    
    rm -f /tmp/ktutil.cmd
    chmod 600 /etc/krb5.keytab
    log_info "Keytab создан"
fi

# ============================================================
# 8. НАСТРОЙКА SSSD
# ============================================================
log_info "Настройка SSSD (/etc/sssd/sssd.conf)..."

cat > /etc/sssd/sssd.conf << EOF
[sssd]
domains = $DOMAIN
config_file_version = 2
services = nss, pam

[domain/$DOMAIN]
default_shell = /bin/bash
krb5_store_password_if_offline = True
cache_credentials = True
krb5_realm = $DOMAIN_REALM
krb5_server = $DC_IP
id_provider = ad
auth_provider = ad
access_provider = ad
chpass_provider = ad
ldap_id_mapping = True
use_fully_qualified_names = True
fallback_homedir = /home/%u@%d
EOF

chmod 600 /etc/sssd/sssd.conf

# ============================================================
# 9. ЗАПУСК СЛУЖБ
# ============================================================
log_info "Запуск служб..."

systemctl restart sssd
systemctl enable sssd

systemctl restart smb nmb
systemctl enable smb nmb

# ============================================================
# 10. СОЗДАНИЕ СЕТЕВОЙ ПАПКИ
# ============================================================
log_info "Создание сетевой папки $SHARE_PATH..."

mkdir -p "$SHARE_PATH"

# Создание файла с инструкцией
cat > "$SHARE_PATH/Ввод_Red_OS_в_домен.txt" << 'EOF'
===========================================
ИНСТРУКЦИЯ: Ввод Red OS в домен Windows
===========================================

Домен: tfi.ru
Контроллер: 192.168.1.2 (dc0.tfi.ru)

--- 1. НАСТРОЙКА СЕТИ ---
echo "192.168.1.2 dc0.tfi.ru tfi.ru" >> /etc/hosts

--- 2. УСТАНОВКА ПАКЕТОВ ---
sudo dnf install realmd sssd oddjob oddjob-mkhomedir adcli samba-common

--- 3. КОНФИГ KRB5.CONF ---
sudo tee /etc/krb5.conf << 'EOF'
[libdefaults]
    default_realm = TFI.RU
    dns_lookup_realm = false
    dns_lookup_kdc = false
    rdns = false
    allow_weak_crypto = true
    default_tgs_enctypes = rc4-hmac
    default_tkt_enctypes = rc4-hmac
    permitted_enctypes = rc4-hmac
[realms]
    TFI.RU = {
        kdc = 192.168.1.2
        admin_server = 192.168.1.2
    }
[domain_realm]
    .tfi.ru = TFI.RU
    tfi.ru = TFI.RU
EOF

--- 4. КРИПТОПОЛИТИКА ---
sudo update-crypto-policies --set DEFAULT:AD-SUPPORT

--- 5. КОНФИГ SAMBA ---
sudo tee /etc/samba/smb.conf << 'EOF'
[global]
    workgroup = TFI
    realm = TFI.RU
    security = ADS
    server role = member server
    client min protocol = NT1
    server min protocol = NT1
    kerberos method = secrets and keytab
EOF

--- 6. ВВОД В ДОМЕН ---
sudo realm join --user=geolcom2 tfi.ru

--- 7. ПРОВЕРКА ---
realm list

--- 8. СОЗДАНИЕ KEYTAB (ЕСЛИ НУЖНО) ---
sudo kinit geolcom2@TFI.RU
sudo ktutil
# add_entry -password -p geolcom2@TFI.RU -k 1 -e rc4-hmac
# (ввести пароль)
# wkt /etc/krb5.keytab
# quit
sudo chmod 600 /etc/krb5.keytab

--- 9. КОНФИГ SSSD ---
sudo tee /etc/sssd/sssd.conf << 'EOF'
[sssd]
domains = tfi.ru
config_file_version = 2
services = nss, pam
[domain/tfi.ru]
default_shell = /bin/bash
krb5_store_password_if_offline = True
cache_credentials = True
krb5_realm = TFI.RU
krb5_server = 192.168.1.2
id_provider = ad
auth_provider = ad
access_provider = ad
ldap_id_mapping = True
use_fully_qualified_names = True
fallback_homedir = /home/%u@%d
EOF
sudo chmod 600 /etc/sssd/sssd.conf
sudo systemctl restart sssd
sudo systemctl enable sssd

--- 10. ПРОВЕРКА ---
getent passwd geolcom2@tfi.ru
sudo su - geolcom2@tfi.ru

===========================================
ГОТОВО!
===========================================
EOF

chmod -R 755 "$SHARE_PATH"

# Добавление шары в Samba
cat >> /etc/samba/smb.conf << EOF

[$SHARE_NAME]
    path = $SHARE_PATH
    browseable = yes
    read only = yes
    guest ok = yes
    public = yes
    writable = no
    create mask = 0755
    directory mask = 0755
    force user = nobody
    force group = nobody
EOF

# Отключение SELinux (временная мера)
if command -v getenforce &> /dev/null; then
    if [ "$(getenforce)" != "Disabled" ]; then
        setenforce 0
        log_warn "SELinux временно отключён"
    fi
fi

systemctl restart smb

# ============================================================
# 11. ПРОВЕРКА
# ============================================================
log_info "Проверка результатов..."

echo ""
echo "=========================================="
echo "РЕЗУЛЬТАТЫ ПРОВЕРКИ"
echo "=========================================="

echo -n "Статус домена: "
if realm list | grep -q "$DOMAIN"; then
    echo -e "${GREEN}В ДОМЕНЕ${NC}"
else
    echo -e "${RED}НЕ В ДОМЕНЕ${NC}"
fi

echo -n "Статус SSSD: "
if systemctl is-active --quiet sssd; then
    echo -e "${GREEN}РАБОТАЕТ${NC}"
else
    echo -e "${RED}НЕ РАБОТАЕТ${NC}"
fi

echo -n "Статус Samba: "
if systemctl is-active --quiet smb; then
    echo -e "${GREEN}РАБОТАЕТ${NC}"
else
    echo -e "${RED}НЕ РАБОТАЕТ${NC}"
fi

echo ""

log_info "Скрипт выполнен!"
echo ""
echo "Сетевая папка доступна по адресу: \\\\$(hostname -I | awk '{print $1}')\\$SHARE_NAME"
echo "Доступ из Windows: \\\\$(hostname -I | awk '{print $1}')\\$SHARE_NAME"
echo ""

# ============================================================
# КОНЕЦ
# ============================================================
