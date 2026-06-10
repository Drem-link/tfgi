# Red OS join Domain (Windows Server 2003)

Скрипт для автоматического ввода Red OS в домен Windows Server 2003 и создания сетевой папки с инструкцией.

## Особенности

- Поддержка старых протоколов (RC4, NT1)
- Автоматическая настройка Kerberos, Samba, SSSD
- Создание сетевой папки с инструкцией
- Проверка всех этапов

## Использование

```bash
# Скачать скрипт
curl -O https://raw.githubusercontent.com/yourusername/repo/main/join-domain.sh

# Сделать исполняемым
chmod +x join-domain.sh

# Запустить от root
sudo ./join-domain.sh
