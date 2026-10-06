# Геологический каталог

Внутренний MVP геологической библиотеки: текстовый поиск по метаданным, геометрии PostGIS, вращаемый orthographic 3D-глобус с координатной сеткой, маркерами и контурами, связанные документы и управляемые администратором учётные записи.

## Модель и интерфейс

- `features`: геометрия PostGIS в EPSG:4326, тип и метаданные объекта.
- `documents`: название, фондовый номер, регион, год, тема, описание и архивная ссылка.
- `feature_documents`: связь many-to-many между объектами на глобусе и документами.
- `GET /api/features?q=&region=&year_from=&year_to=` возвращает отфильтрованный GeoJSON FeatureCollection.
- `GET /api/features/{id}` возвращает геометрию и связанные документы.
- При нажатии на маркер глобус плавно приближает выбранный объект и показывает координаты с связанными документами. Список документов объекта также выделяется в каталоге.
- Глобус рисуется локально Canvas 2D: материки и обобщённые границы стран, вращение мышью/касанием, масштаб колёсиком, центрирование по выбранному объекту. Географическая подложка — локальная копия Natural Earth 1:110m Admin 0, общедоступные данные Public Domain; внешние картографические тайлы/CDN не используются.
- Страны и береговые линии схематичны и не подходят для кадастровой, юридической или геодезической точности. На глобусе также показываются координатная сетка и геологические объекты из каталога.
- Администратор загружает документы через «Файлы» → «Добавить»: можно создать точку/площадь или выбрать существующий объект и указать метаданные документа. К одному документу прикрепляется до 10 файлов за раз.
- Разрешены PDF, JPG/JPEG, PNG, TIFF, DOCX, XLSX, PPTX, TXT и CSV; допустимые расширения проверяются вместе с сигнатурой/структурой содержимого. Лимит — 50 MiB на файл и 100 MiB на одну загрузку. Старые форматы Office и исполняемые файлы не принимаются.
- Файлы доступны для скачивания всем вошедшим пользователям; создание и удаление файлов доступны только администраторам. Они хранятся отдельно от PostGIS в `/uploads`, под случайными именами, а в базе остаются метаданные. Синтетические seed-записи начинаются с `[DEMO]`; это не архивные данные.

## Вход и HTTPS

Kubernetes pod слушает только HTTPS. NodePort `30808` обслуживает TLS на `https://192.168.1.241:30808`; сертификат подписан локальным CA. Перед вводом пароля установите `ca.crt` в доверенные корневые CA устройства и убедитесь, что браузер не предупреждает о сертификате. Не обходите TLS-предупреждение: Secure cookie и пароль предназначены только для HTTPS.

Авторизация: Argon2id password hashes, signed session cookie (8 часов, HttpOnly/Secure/SameSite=Strict) и ограничение неудачных попыток. Учётная запись из Kubernetes Secret создаётся в PostGIS при первом старте приложения, если такого логина ещё нет. Администратор управляет отдельными учётками в разделе «Пользователи»: роль `viewer` даёт поиск, просмотр и скачивание файлов, `admin` дополнительно управляет учётными записями и файлами. Администратор может менять роли, отключать учётки и сбрасывать пароли; саморегистрации нет, пароль пользователя нельзя просмотреть. Нельзя отключить или понизить последнего активного администратора. Не публикуйте NodePort через WAN/MikroTik и не используйте демо-секреты/пароли.

### Kubernetes deployment (RED OS single-node)

Обновите ветку и дождитесь успешного workflow `Geological catalog image`. На сервере из каталога `geocatalog/`:

```bash
umask 077
install -d -m 700 /root/geocatalog-tls
cd /root/geocatalog-tls

# Создать локальный CA и сертификат сервера с IP SAN.
openssl genrsa -out ca.key 3072
openssl req -x509 -new -key ca.key -sha256 -days 3650 \
  -subj "/CN=Geological Catalog Local CA" -out ca.crt
openssl req -new -newkey rsa:3072 -nodes \
  -keyout tls.key -out tls.csr -subj "/CN=192.168.1.241"
printf 'subjectAltName=IP:192.168.1.241\nextendedKeyUsage=serverAuth\nkeyUsage=digitalSignature,keyEncipherment\n' > tls.ext
openssl x509 -req -in tls.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out tls.crt -days 365 -sha256 -extfile tls.ext

kubectl -n geocatalog create secret tls geocatalog-tls \
  --cert=tls.crt --key=tls.key --dry-run=client -o yaml | kubectl apply -f -
```

Создайте файлы учётной записи интерактивно, не вводя пароль аргументом команды. Запустите CLI из локального venv с зависимостями `requirements.txt`:

```bash
python -m venv /tmp/geocatalog-admin-venv
/tmp/geocatalog-admin-venv/bin/pip install -r requirements.txt
/tmp/geocatalog-admin-venv/bin/python -m app.auth_cli --output-dir /root/geocatalog-auth
kubectl -n geocatalog create secret generic geocatalog-auth \
  --from-file=/root/geocatalog-auth/AUTH_USERNAME \
  --from-file=/root/geocatalog-auth/AUTH_PASSWORD_HASH \
  --from-file=/root/geocatalog-auth/SESSION_SECRET \
  --dry-run=client -o yaml | kubectl apply -f -
rm /root/geocatalog-auth/AUTH_USERNAME /root/geocatalog-auth/AUTH_PASSWORD_HASH /root/geocatalog-auth/SESSION_SECRET
```

`auth_cli` спросит username и дважды пароль длиной от 8 символов; пароль не выводится. При первом старте приложение переносит эту учётку в таблицу `geocatalog_users` с ролью администратора. После этого создание коллег выполняется через ссылку «Пользователи» в интерфейсе; Kubernetes Secret служит только для начального администратора и подписи сессий. `ca.key` и `tls.key` должны оставаться закрытыми (root-only); раздавайте клиентам только публичный `ca.crt` доверенным каналом. Установите CA на клиентский компьютер, затем проверьте/откройте `https://192.168.1.241:30808`.

Образ собирается GitHub Actions. На сервере из checkout нужной ветки:

```bash
install -d -o 10001 -g 10001 -m 0770 /var/lib/containers/k8s-app-data/geocatalog-uploads
# При SELinux Enforcing настройте для этого каталога постоянную метку,
# разрешённую контейнерной политикой (обычно container_file_t), и проверьте её до выкладки.
semanage fcontext -a -t container_file_t '/var/lib/containers/k8s-app-data/geocatalog-uploads(/.*)?'
restorecon -Rv /var/lib/containers/k8s-app-data/geocatalog-uploads

ctr -n k8s.io images pull ghcr.io/drem-link/tfgi-geocatalog:feature-geological-map-mvp
kubectl apply -f k8s/app.yaml
kubectl rollout status deployment/geocatalog -n geocatalog --timeout=180s
kubectl get pods,svc -n geocatalog -o wide
```

### Локальная разработка

Для Compose нужны `POSTGRES_PASSWORD`, `AUTH_USERNAME`, `AUTH_PASSWORD_HASH`, `SESSION_SECRET` в неотслеживаемом `.env`, а сертификат и ключ — `tls/tls.crt` и `tls/tls.key`. Сгенерируйте hash/session через `app.auth_cli`, серверный TLS-сертификат — локальным тестовым CA с SAN `IP:127.0.0.1,DNS:localhost`. Затем `docker compose up --build`; интерфейс будет на `https://127.0.0.1:8443`. Доверие test CA можно установить только на тестовом клиенте.

Пример создания `.env` после генерации файлов учётки (username — латиница/цифры/`._@-`); сначала убедитесь, что `.env` ещё не существует:

```bash
python -m app.auth_cli --output-dir .secrets
umask 077
test ! -e .env
printf "POSTGRES_PASSWORD='%s'\nAUTH_USERNAME='%s'\nAUTH_PASSWORD_HASH='%s'\nSESSION_SECRET='%s'\n" \
  "$(openssl rand -hex 32)" \
  "$(cat .secrets/AUTH_USERNAME)" \
  "$(cat .secrets/AUTH_PASSWORD_HASH)" \
  "$(cat .secrets/SESSION_SECRET)" > .env
chmod 600 .env
```

### Эксплуатационные ограничения

- Сертификат сервера истекает через год; продлите его и обновите Secret до истечения.
- Ротация `SESSION_SECRET` отзывает все выданные cookie. Смена пароля: обновите Argon2 hash в Secret и перезапустите Deployment.
- PV использует hostPath `/var/lib/containers/k8s-app-data/postgis`, `Retain`; `20Gi` — декларативная ёмкость, не quota. Никогда не удаляйте PVC/PV или каталог базы для переустановки.
- Загруженные файлы хранятся в отдельном hostPath `/var/lib/containers/k8s-app-data/geocatalog-uploads` через PVC `geocatalog-uploads` с политикой `Retain`. До применения `k8s/app.yaml` создайте этот каталог с владельцем UID/GID `10001` и настройте/проверьте постоянную SELinux-метку для контейнера на RED OS. `100Gi` в PV — запрос/объявленная ёмкость, не файловая quota; контролируйте свободное место хоста. Не удаляйте uploads PV/PVC или hostPath при обновлении приложения.
- Schema/demo SQL запускаются автоматически только при первом создании пустого каталога PostgreSQL; последующие изменения делайте миграциями.
- Резервируйте и проверяйте восстановление обеих частей вместе: PostGIS (включая `document_files`) и uploads PVC. Согласованная резервная копия должна сохранять соответствие записей файлов на диске и метаданных в БД. Пока не настроены проверенные бэкапы, восстановление и аудит — не загружайте закрытые документы/координаты.

## Проверки

```bash
pip install -r requirements-dev.txt
pytest -q
node --check app/static/app.js
node --check app/static/catalog-admin.js
```

### Демо-точки у Магадана

Миграция `sql/003_magadan_demo_points.sql` добавляет две повторно применяемые синтетические точки у Магадана и отдельный демонстрационный документ для каждой. После обновления checkout на сервере примените её к уже существующей базе так (пароль БД подставляется внутри pod и не выводится):

```bash
cd /root/tfgi-geocatalog/geocatalog
kubectl exec -i -n geocatalog deployment/geocatalog-postgis -- \
  sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" psql -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' \
  < sql/003_magadan_demo_points.sql
```

Координаты выбраны около Магадана только для демонстрации; записи не обозначают реальные геологические объекты или документы.
