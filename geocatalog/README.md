# Геологический каталог

Внутренний MVP геологической библиотеки: текстовый поиск по метаданным, геометрии PostGIS, вращаемый orthographic 3D-глобус с координатной сеткой, маркерами и контурами, связанные документы и вход одного администратора.

## Модель и интерфейс

- `features`: геометрия PostGIS в EPSG:4326, тип и метаданные объекта.
- `documents`: название, фондовый номер, регион, год, тема, описание и архивная ссылка.
- `feature_documents`: связь many-to-many между объектами на глобусе и документами.
- `GET /api/features?q=&region=&year_from=&year_to=` возвращает отфильтрованный GeoJSON FeatureCollection.
- `GET /api/features/{id}` возвращает геометрию и связанные документы.
- Глобус рисуется локально Canvas 2D: вращение мышью/касанием, масштаб колёсиком, центрирование по выбранному объекту. Нет внешних картографических тайлов/CDN и нет запросов координат сторонним tile-серверам.
- Нет endpoint’ов редактирования или загрузки файлов. Синтетические seed-записи начинаются с `[DEMO]`; это не архивные данные.

## Вход и HTTPS

Kubernetes pod слушает только HTTPS. NodePort `30808` обслуживает TLS на `https://192.168.1.241:30808`; сертификат подписан локальным CA. Перед вводом пароля установите `ca.crt` в доверенные корневые CA устройства и убедитесь, что браузер не предупреждает о сертификате. Не обходите TLS-предупреждение: Secure cookie и пароль предназначены только для HTTPS.

Авторизация MVP: одна администраторская учётка из Kubernetes Secret; саморегистрации нет. Argon2id password hash, signed session cookie (8 часов, HttpOnly/Secure/SameSite=Strict), ограничение неудачных попыток и защита Origin для login/logout. Не публикуйте NodePort через WAN/MikroTik и не используйте демо-секреты/пароли.

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

`auth_cli` спросит username и дважды пароль; пароль не выводится. `ca.key` и `tls.key` должны оставаться закрытыми (root-only); раздавайте клиентам только публичный `ca.crt` доверенным каналом. Установите CA на клиентский компьютер, затем проверьте/откройте `https://192.168.1.241:30808`.

Образ собирается GitHub Actions. На сервере из checkout нужной ветки:

```bash
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
- Schema/demo SQL запускаются автоматически только при первом создании пустого каталога PostgreSQL; последующие изменения делайте миграциями.
- Пока не настроены проверенные бэкапы, восстановление, аудит и управление несколькими пользователями — не загружайте закрытые документы/координаты.

## Проверки

```bash
pip install -r requirements-dev.txt
pytest -q
node --check app/static/app.js
```
