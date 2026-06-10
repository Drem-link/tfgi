===========================================
ИНСТРУКЦИЯ: Ввод Red OS в домен Windows
===========================================

Автор: ZABBIX
Дата: 09.06.2026
Домен: tfi.ru
Контроллер: 192.168.1.2 (dc0.tfi.ru)

===========================================
1. НАСТРОЙКА СЕТИ И DNS
===========================================

# Прописать DNS контроллера домена
nmcli con mod "Имя_подключения" ipv4.dns 192.168.1.2
nmcli con up "Имя_подключения"

# Или в /etc/resolv.conf:
echo "nameserver 192.168.1.2" >> /etc/resolv.conf

# Добавить контроллер в /etc/hosts
echo "192.168.1.2 dc0.tfi.ru tfi.ru" >> /etc/hosts

===========================================
2. УСТАНОВКА ПАКЕТОВ
===========================================

sudo dnf install realmd sssd oddjob oddjob-mkhomedir adcli samba-common

===========================================
3. НАСТРОЙКА КЕРБЕРОС (ДЛЯ СТАРЫХ WINDOWS)
===========================================

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


===========================================
4. НАСТРОЙКА КРИПТОПОЛИТИКИ
===========================================

sudo update-crypto-policies --set DEFAULT:AD-SUPPORT

===========================================
5. НАСТРОЙКА SAMBA
===========================================

sudo tee /etc/samba/smb.conf << 'EOF'
[global]
    workgroup = TFI
    realm = TFI.RU
    security = ADS
    server role = member server
    client min protocol = NT1
    server min protocol = NT1
    kerberos method = secrets and keytab


===========================================
6. ВВОД В ДОМЕН
===========================================

# Вариант А (через realm)
sudo realm join --user=geolcom2 tfi.ru

# Вариант Б (через net ads)
sudo net ads join -U geolcom2 -S 192.168.1.2

===========================================
7. СОЗДАНИЕ KEYTAB (ЕСЛИ НЕ СОЗДАЛСЯ)
===========================================

sudo kinit geolcom2@TFI.RU
sudo ktutil

# В интерактивном режиме:
# add_entry -password -p geolcom2@TFI.RU -k 1 -e rc4-hmac
# (ввести пароль)
# wkt /etc/krb5.keytab
# quit

sudo chmod 600 /etc/krb5.keytab

===========================================
8. НАСТРОЙКА SSSD
===========================================

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
sudo chmod 600 /etc/sssd/sssd.conf
sudo systemctl restart sssd
sudo systemctl enable sssd

===========================================
9. ПРОВЕРКА
===========================================

realm list
getent passwd geolcom2@tfi.ru
sudo su - geolcom2@tfi.ru

===========================================
10. ЧАСТЫЕ ОШИБКИ И РЕШЕНИЯ
===========================================

Ошибка: "KDC has no support for encryption type"
Решение: Включить rc4-hmac в krb5.conf + update-crypto-policies

Ошибка: "Preauthentication failed"
Решение: Проверить пароль, сбросить его в AD

Ошибка: "keytab not found"
Решение: Создать keytab вручную через ktutil

Ошибка: "GSSAPI Error: Message stream modified"
Решение: Прописать контроллер в /etc/hosts и krb5_server в sssd.conf

===========================================
ГОТОВО! МАШИНА В ДОМЕНЕ
===========================================
