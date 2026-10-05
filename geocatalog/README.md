# Геологический каталог — MVP

Внутренний прототип каталога геологических материалов: поиск по текстовым метаданным, региону и году; отображение геологических объектов (точки, полигоны и линии) на карте; просмотр связанных документов при выборе объекта.

## Локальный запуск

Требуются Docker Compose и свободный localhost-порт 8080. Каталог `data/postgres` содержит постоянные локальные данные и исключён из Git.

```bash
cd geocatalog
printf 'POSTGRES_PASSWORD=%s\n' "$(openssl rand -hex 24)" > .env
docker compose up --build -d
```

Откройте <http://127.0.0.1:8080>. В пустую базу при первом запуске автоматически добавляются два явно помеченных синтетических объекта и демонстрационные документы. Проверка здоровья API: <http://127.0.0.1:8080/healthz>.

## Модель данных и API

- `features`: геометрия PostGIS в EPSG:4326, имя и тип объекта.
- `documents`: библиографические метаданные, регион, год, тема, архивный шифр и текстовое описание.
- `feature_documents`: связь many-to-many между объектами на карте и материалами.
- `GET /api/features?q=&region=&year_from=&year_to=&bbox=` возвращает GeoJSON FeatureCollection с подходящими документами.
- `GET /api/features/{id}` возвращает объект и все связанные документы.

Для раннего прототипа данные можно добавлять через SQL с `ST_GeomFromGeoJSON`, например через доступ к контейнеру БД. У API намеренно нет публичного интерфейса записи/загрузки файлов.

## Ограничения перед реальным развёртыванием

- Приложение пока не имеет аутентификации и авторизации: Compose публикует порт только на loopback. Не выставляйте его в LAN/интернет до интеграции с аутентификацией и TLS.
- Карта и её CSS/JS пока загружаются с публичных OpenStreetMap и unpkg CDN. Запросы тайлов идут от браузера к внешнему серверу и раскрывают просматриваемый район. Не используйте реальные чувствительные координаты, пока не подключены согласованный внутренний tile-сервер и локальные копии библиотек карты.
- Не загружайте закрытые документы в прототип. Поле `archive_reference` — только ссылка/шифр, не содержимое файла.
- В Kubernetes нужны отдельные PVC/том для базы, Secret для пароля, NetworkPolicy, внутренний доступ, TLS и резервное копирование/проверка восстановления.
- Схема SQL запускается автоматически только при первом создании пустого каталога Postgres. Для последующих изменений используйте миграции, не удаляйте `data/postgres`.

## Kubernetes (однонодовый прототип)

Манифесты в `k8s/` рассчитаны на текущую ноду `zabbix` и отдельный каталог `/var/lib/containers/k8s-app-data/postgis`. PV имеет `Retain`, но `20Gi` в hostPath — декларативная ёмкость, не дисковая квота. Сервис базы и веб-сервис имеют тип `ClusterIP`; никаких NodePort/Ingress манифесты не создают.

Перед запуском проверьте доступную память ноды и доставьте каталог `geocatalog/` на сервер. На сервере от root подготовьте каталог данных и его владельца (образ PostGIS работает как UID/GID 999):

```bash
install -d -o 999 -g 999 -m 700 /var/lib/containers/k8s-app-data/postgis
restorecon -Rv /var/lib/containers/k8s-app-data/postgis
kubectl apply -f k8s/namespace.yaml
kubectl -n geocatalog create secret generic geocatalog-db \
  --from-literal=PGPASSWORD="$(openssl rand -hex 32)"
kubectl -n geocatalog create configmap geocatalog-schema \
  --from-file=001_schema.sql=sql/001_schema.sql \
  --from-file=002_demo_data.sql=sql/002_demo_data.sql
kubectl apply -f k8s/postgis.yaml
```

Образ собирается GitHub Actions после push в ветку `feature/geological-map-mvp`; сборка и тесты не требуют Podman на сервере. Дождитесь успешного workflow **Geological catalog image**. Если GitHub создал GHCR package как private, переключите package visibility на public перед pull на сервере (в репозитории нет секретных данных, но приложение всё равно пока без аутентификации).

```bash
ctr -n k8s.io images pull ghcr.io/drem-link/tfgi-geocatalog:feature-geological-map-mvp
```

Дождитесь успешного pull образа, затем примените веб-приложение:

```bash
kubectl apply -f k8s/app.yaml
kubectl get pvc,pods -n geocatalog -w
```

Проверьте завершение PostGIS и доступность API перед использованием:

```bash
kubectl rollout status -n geocatalog deployment/geocatalog-postgis --timeout=180s
kubectl rollout status -n geocatalog deployment/geocatalog --timeout=180s
kubectl -n geocatalog port-forward --address 127.0.0.1 svc/geocatalog 8080:8080
```

В другом терминале проверьте `http://127.0.0.1:8080/healthz` и `http://127.0.0.1:8080/api/features`. Локальный `kubectl port-forward` доступен только на самом сервере; до авторизации и TLS не публикуйте приложение в LAN или Интернет.

Не удаляйте PVC/PV или каталог данных для «переустановки»: PV настроен с `Retain`, но удаление каталога уничтожит базу. Demo SQL запускается только при первой инициализации пустого Postgres data directory. Для последующих изменений используйте миграции, не запускайте init SQL повторно вручную без проверки. Для реальной эксплуатации нужны авторизация, TLS, резервные копии и проверка восстановления.

## Проверки

```bash
python -m pip install -r requirements.txt pytest
pytest -q
```
