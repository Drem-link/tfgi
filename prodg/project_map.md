# Карта проекта — Kubernetes в геологической библиотеке

> Обновляется по ходу работы. Присылай этот файл в начале новой сессии, чтобы не пересказывать историю заново.

## Контекст
Сисадмин крупной геологической библиотеки, цель — за год дорасти до DevOps через практику на реальной инфраструктуре.

## Инфраструктура — текущее состояние

**Сервер:** `zabbix`, RED OS 8, IP `192.168.1.241` (внешний IP библиотеки через MikroTik: `94.247.57.137`)

**Диски:**
- `/dev/sda7` (30G) — корень системы, периодически забивается (следить через `df -h`, чистить `journalctl --vacuum`)
- `/dev/sdb1` (1.8T) — смонтирован на `/var/lib/containers`, туда переехало хранилище containerd

**Kubernetes:** v1.36.3, single-node (control-plane = worker, taint снят через `kubectl taint nodes zabbix node-role.kubernetes.io/control-plane:NoSchedule-` — **периодически возвращается после ребута/переинициализации, проверять при новых проблемах со scheduling**)

**CNI:** Flannel (Calico был установлен по ошибке параллельно, конфликтовал, удалён)

**Container registry:** `registry.k8s.io` недоступен напрямую (провайдер режет маршруты) — обходим через Docker Hub (`docker.io`) + `ctr images tag` для перетегирования на нужное имя, либо через зеркала в `/etc/containerd/certs.d/registry.k8s.io/hosts.toml`

## Развёрнутые сервисы

| Сервис | Namespace | Доступ | Статус |
|---|---|---|---|
| Wiki.js + PostgreSQL | `wiki` | `http://192.168.1.241:30080` | Работает |
| Kubernetes Dashboard | `kubernetes-dashboard` | `https://192.168.1.241:31706` (NodePort может меняться, проверять `kubectl get svc -n kubernetes-dashboard`) | Работает, нужен токен через `kubectl create token dashboard-admin -n kubernetes-dashboard` |
| kube-prometheus-stack (Prometheus+Grafana+Alertmanager) | `monitoring` | Grafana: `:30300` (admin/grafana_admin_2026), Prometheus: `:30900` | Переустановлен с лимитами retention (3d / 2GB), чтобы не забивал диск повторно |
| Zabbix (уже был на сервере до наших работ) | вне кластера, локально на хосте | — | Мониторит Windows-серверы библиотеки |

## Сеть

**WireGuard VPN** (сервер → дом):
- Сервер: `10.8.0.1/24`, порт `51820/udp`, конфиг `/etc/wireguard/wg0.conf`
- MikroTik: NAT-проброс `94.247.57.137:51820 → 192.168.1.241:51820` (уже настроен)
- Домашний клиент: `10.8.0.2/24` — настройка была в процессе, уточнить статус

**SSH из дома** — обсуждали, но не подтверждено выполнение:
- План: ключи вместо пароля, порт 2222, fail2ban, проброс на MikroTik
- **Проверить, сделано ли это фактически**

**Lens** — подключён с домашнего/рабочего Windows-компьютера напрямую к кластеру через `kubeconfig` (порт 6443 открыт в firewalld)

## Известные грабли (чтобы не наступать снова)

1. **Taint на control-plane возвращается** после определённых событий — если поды виснут в `Pending` с `untolerated taint`, снимать заново
2. **Диск `/` (sda7) забивается** — Prometheus без лимитов ретеншена и логи journald/containerd — главные виновники. Мониторить `df -h`
3. **Пуллинг образов с `registry.k8s.io` не работает** напрямую — использовать Docker Hub + `ctr images tag`
4. **Не удалять `.old`/`.veryold` бэкап-директории сразу** — если там могли остаться данные несвязанных с k8s подов (был инцидент с потерей контейнеров podman: Grafana/Portainer/Bento/pdf-сервис)
5. **Массовое накопление "мёртвых" подов** (Error/Evicted/ContainerStatusUnknown) — следить за `terminated-pod-gc-threshold` в kube-controller-manager, чистить через `--field-selector=status.phase=Failed`

## Годовой план (сжато)

**Q1** — основы: первый Deployment ✅ (Wiki.js), PV/PVC ✅, ConfigMap/Secret (частично, Secret есть), мониторинг ✅ (Grafana/Prometheus), второй worker-node (в процессе — WireGuard/домашний ПК)

**Q2** — CI/CD: Gitea/GitLab, свои Docker-образы, пайплайн сборки, Terraform/Ansible, Velero-бэкапы

**Q3** — Production-практики: свои Helm charts, Namespaces+RBAC, Ingress+TLS (cert-manager), HPA, Loki-логи

**Q4** — Архитектура: HA control-plane (тестово), GitOps (ArgoCD/FluxCD), флагманский проект

**Отдельный трек — связь между филиалами:** WireGuard-меш, мультикластер (Cilium Cluster Mesh/Submariner), распределённое хранилище (MinIO), поиск (Elasticsearch/OpenSearch + Tika для OCR), геопривязанный каталог (PostGIS)

## Текущая задача (в работе)

Подключить Grafana к Zabbix как источник данных, построить дашборд заполненности дисков на Windows-серверах, которые уже мониторит Zabbix.

---
*Последнее обновление: сессия с восстановлением мониторинга после переустановки + подключение Zabbix datasource*
