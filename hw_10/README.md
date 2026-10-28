# hw_10 — автомасштабирование, мониторинг, алертинг, Airflow + MLflow и Kafka-атака

1. **Автомасштабирование 4 → 6 реплик** под нагрузкой (HPA по CPU).
2. **Мониторинг кластера и модели** — `kube-prometheus-stack` + Grafana,
   дашборды для кластера и для качества модели.
3. **Алерт для сисадмина**: срабатывает, когда работают **6 реплик** И хотя бы
   один под держит **> 80% загрузки в течение 5 минут**.
4. **Airflow в кластере** (не managed), который забирает DAG'и из **внешнего
   git-репозитория** через `git-sync` и логирует метрики обучения в **MLflow**.
5. **Kafka-атака**: продюсер наращивает интенсивность событий, консьюмер
   бьёт ими по API, пока не сработает алерт.

```
                              ┌───────────────────────── namespace: url-fraud ─────────────────────────┐
                              │                                                                         │
  Kafka attack Job            │   ┌───────────────┐   HPA  min4/max6   ┌────────────────────────────┐   │
  ┌──────────┐  produce       │   │ kafka:9092    │   cpu target 60%   │  Deployment url-fraud-api  │   │
  │ producer │ ─────────────┐ │   │ (KRaft, 1 бр.)│ ◄───────────────── │  ▪ 4..6 реплик             │   │
  └──────────┘              │ │   └───────────────┘                    │  ▪ /metrics (Prometheus)   │   │
                            ▼ │                                        │  ▪ Service NodePort :30080 │   │
                     ┌────────────┐  consume → /api/v1/predict/batch   └─────────────┬──────────────┘   │
                     │ topic urls │ ────────────────────────────────────────────────►│                  │
                     └────────────┘                                                │ scrape           │
                                                                                   ▼                  │
  Airflow (in-cluster, K8sExecutor, git-sync из git)      ┌──────────────────────────────────────┐    │
  ┌───────────────────────────────────────────┐           │ kube-prometheus-stack (namespace:    │    │
  │ DAG url_fraud_retrain  (cron 0 3 * * *)   │           │ monitoring)                          │    │
  │   retrain ──► promote                     │           │  ▪ Prometheus + ServiceMonitor       │    │
  │   (mlops image, sklearn + MLflow)         │──────────►│  ▪ Alertmanager → e-mail сисадмину   │    │
  └───────────────────────────────────────────┘           │  ▪ Grafana (NodePort :30300)          │    │
              │ log metrics/model                          └──────────────────────────────────────┘    │
              ▼                                                                                         │
  ┌───────────────────────────────────────────┐   artifacts   ┌──────────────────────────────┐          │
  │ MLflow (Deployment + Postgres + S3)        │ ────────────► │ Object Storage (S3-совместимый)│         │
  │ NodePort :30500                            │               └──────────────────────────────┘          │
  └───────────────────────────────────────────┘                                                          │
                              └─────────────────────────────────────────────────────────────────────────┘
```

---

## Содержание

1. [Что требуется по заданию и где это лежит](#что-требуется-по-заданию-и-где-это-лежит)
2. [Структура репозитория](#структура-репозитория)
3. [Быстрый старт](#быстрый-старт)
4. [Автомасштабирование 4 → 6](#автомасштабирование-4--6)
5. [Мониторинг: Prometheus и Grafana](#мониторинг-prometheus-и-grafana)
6. [Алерт сисадмину](#алерт-сисадмину)
7. [Airflow + git-sync + MLflow](#airflow--git-sync--mlflow)
8. [Kafka-атака](#kafka-атака)
9. [Проверка: 4 → 6 → алерт](#проверка-4--6--алерт)
10. [CI/CD](#cicd)
11. [Стоимость и удаление ресурсов](#стоимость-и-удаление-ресурсов)

---

## Что требуется по заданию и где это лежит

| Требование | Реализация | Файлы |
|---|---|---|
| Развёртывание 4 → 6 реплик | HPA `minReplicas: 4`, `maxReplicas: 6`, CPU target 60% | `k8s/20-deployment.yaml`, `k8s/50-hpa.yaml` |
| Мониторинг кластера и модели | `kube-prometheus-stack` + ServiceMonitor + 2 дашборда Grafana | `monitoring/kube-prometheus-stack-values.yaml`, `k8s/70-servicemonitor.yaml`, `monitoring/grafana/dashboards/*.json` |
| Алерт сисадмину (6 реплик + >80% 5 мин) | PrometheusRule `UrlFraudScalingSaturated` → Alertmanager (e-mail) | `monitoring/alerts/url-fraud-alerts.yaml`, `monitoring/alertmanager/alertmanager-config.yaml` |
| Airflow в кластере, DAG'и из внешнего git | Helm `apache-airflow` + `KubernetesExecutor` + `dags.gitSync` | `airflow/values.yaml`, `airflow/dags/fraud_retrain_dag.py` |
| Метрики обучения в MLflow | `mlops/retrain.py` логирует params/metrics/model и регистрирует версию | `mlops/`, `mlflow/` |
| «Атака» на кластер через Kafka | Kafka (KRaft) + Job `url-fraud-attack` (producer + consumer) | `kafka/` |

---

## Структура репозитория

```
hw_10/
├── app/                       # FastAPI-сервис (из hw_09) + метрики Prometheus
│   ├── main.py                #   эндпоинты + определения метрик (счётчики/гистограммы)
│   ├── predictor.py           #   обёртка над sklearn
│   ├── schemas.py             #   pydantic-модели запроса/ответа
│   └── train.py / config.py   #   обучение (переиспользуется mlops/retrain.py)
├── models/                    # model.joblib + metadata.json (коммитятся намеренно)
├── k8s/                       # манифесты рабочей нагрузки (kustomize)
│   ├── 00-namespace.yaml      #   namespace url-fraud (+PSA restricted)
│   ├── 10-configmap.yaml
│   ├── 20-deployment.yaml     #   4 реплики, requests cpu 100m / mem 256Mi
│   ├── 30-service.yaml        #   NodePort :30080
│   ├── 50-hpa.yaml            #   ★ автомасштабирование 4 → 6
│   ├── 60-pdb.yaml
│   └── 70-servicemonitor.yaml #   ★ скрейп /metrics
├── monitoring/                # ★
│   ├── kube-prometheus-stack-values.yaml
│   ├── alerts/url-fraud-alerts.yaml          # ★ PrometheusRule
│   ├── alertmanager/alertmanager-config.yaml #   AlertmanagerConfig (e-mail)
│   ├── alertmanager/alertmanager-secret.example.yaml
│   └── grafana/dashboards/{url-fraud-model,url-fraud-cluster}.json
├── mlflow/                    # ★ MLflow-сервер (Deployment + Postgres + S3)
│   ├── Dockerfile
│   └── k8s/                   #   00-secret.example / 10-postgres / 20-mlflow
├── mlops/                     # ★ обучение и продвижение модели
│   ├── retrain.py             #   fit + MLflow log/register + quality gate
│   ├── promote.py             #   alias 'champion' на новой версии
│   └── Dockerfile
├── airflow/                   # ★ Airflow в кластере
│   ├── values.yaml            #   KubernetesExecutor + git-sync
│   ├── gitsync-secret.example.yaml
│   └── dags/fraud_retrain_dag.py   # DAG url_fraud_retrain
├── kafka/                     # ★ Kafka и генератор атаки
│   ├── k8s/10-kafka.yaml      #   bitnami/kafka 3.9 KRaft, 1 брокер
│   ├── k8s/20-attack.yaml     #   Job: producer + consumer
│   └── app/{common,producer,consumer}.py
├── terraform/                 # инфраструктура YC: кластер, registry, buckets
│   ├── kubernetes.tf network.tf   # (scp из hw_09, размеры узлов под hw_10)
│   ├── storage.tf             # ★ бакеты артефактов MLflow + Kafka
│   ├── kafka.tf               # ★ Managed Kafka (опционально, по флагу)
│   └── service_account.tf     # ★ SA MLflow + статический ключ S3
├── scripts/                   # deploy_k8s / deploy_monitoring / deploy_airflow ...
├── tests/                     # hermetic-тесты API
├── Makefile                   # все цели: build/deploy/attack/scale-watch
├── Dockerfile                 # образ API
└── docker-compose.yml         # локальный запуск API
```

`★` — то, что появилось именно в `hw_10`.

---

## Быстрый старт

> Требуется: `terraform ≥ 1.5`, `kubectl`, `helm ≥ 3.13`, `docker`, `yc` CLI,
> авторизованный профиль Yandex Cloud и VPN (если стенд за периметром).

### 0. Переменные и инструменты

```bash
cd hw_10
cp terraform/terraform.tfvars.example terraform/terraform.tfvars
$EDITOR terraform/terraform.tfvars        # folder_id, cloud_id, bucket-имена, зоны
make install                              # .venv + зависимости (для локальных проверок)
```

### 1. Инфраструктура (кластер, registry, бакеты)

```bash
make tf-init
make tf-plan           # посмотреть, что будет создано
make tf-apply          # 3-узловый Managed K8s + registry + S3-бакеты
export YC_REGISTRY_ID="$(cd terraform && terraform output -raw registry_id)"
```

### 2. Образы в Yandex Container Registry

```bash
make images IMAGE_TAG=1.0.0   # собирает и пушит 4 образа: api, retrain, attack, mlflow
```

### 3. Kubeconfig

```bash
make kubeconfig
export KUBECONFIG="$HOME/.kube/config-url-fraud-cluster"
kubectl get nodes
```

### 4. Рабочая нагрузка, мониторинг, MLflow, Airflow

```bash
make deploy                # API + Service + HPA + PDB + ServiceMonitor
make deploy-monitoring     # kube-prometheus-stack + алерты + дашборды
make deploy-mlflow         # MLflow: Postgres + S3-артефакты (NodePort :30500)
make deploy-airflow        # Airflow в namespace url-fraud, git-sync DAG'ов
```

`deploy_monitoring.sh` перед установкой проверяет, что создан секрет с паролем
SMTP (`alertmanager-smtp`), и печатает команду, если его нет:

```bash
kubectl -n url-fraud create secret generic alertmanager-smtp \
  --from-literal=password='<smtp-password>'
```

### 5. Адреса

| Сервис | Адрес |
|---|---|
| API | `http://<node-ip>:30080` (см. `make status`) |
| Grafana | `http://<node-ip>:30300` (admin / `url-fraud-grafana`) |
| Airflow UI | `http://<node-ip>:30090` (первый вход — `admin` / пароль из values) |
| MLflow | `http://<node-ip>:30500` |
| Prometheus / Alertmanager | `make port-forward` → `localhost:9090` / `:9093` |

---

## Автомасштабирование 4 → 6

`k8s/50-hpa.yaml`:

```yaml
minReplicas: 4
maxReplicas: 6
metrics:
  - type: Resource
    resource: { name: cpu, target: { type: Utilization, averageUtilization: 60 } }
```

Почему target **60%**, а не привычные 70%: по заданию алерт должен срабатывать,
когда **6 реплик уже работают** и поды при этом остаются горячими. Более низкий
таргет приводит к «потолку» (6) раньше, и состояние «6 реплик + высокая
загрузка» реально наблюдаемо. Метрика HPA — это `cpu_usage / cpu_request`
(request = 100m в `20-deployment.yaml`), ровно та же формула, что и в правиле
алерта.

Дополнительно:

* `scaleUp.stabilizationWindowSeconds: 0` — быстрая реакция на атаку;
* `scaleDown.stabilizationWindowSeconds: 300` — без дёрганья после всплеска;
* в Deployment **нет CPU-лимита** (только request) — чтобы не получить
  троттлинг на инференсе.

Смотреть в динамике:

```bash
make scale-watch           # kubectl get hpa,pods -w + загрузка
```

---

## Мониторинг: Prometheus и Grafana

`monitoring/kube-prometheus-stack-values.yaml`:

* `kube-prometheus-stack` даёт кластерные метрики (kube-state-metrics, cAdvisor,
  node-exporter);
* селекторы `serviceMonitorSelector` / `ruleSelector` **обнулены** (`{}`), чтобы
  Prometheus подхватывал `ServiceMonitor` и `PrometheusRule` из namespace
  `url-fraud`, а не только из своего;
* `alertmanagerConfigSelector` / `alertmanagerConfigNamespaceSelector` тоже
  обнулены — чтобы работал `AlertmanagerConfig` из `url-fraud`;
* Grafana отдаётся NodePort `30300`; дашборды подхватываются сайдкаром по метке
  `grafana_dashboard: "1"` (их навешивает `deploy_monitoring.sh`).

**Метрики модели** отдаёт само приложение (`app/main.py`) на `/metrics`:

| Метрика | Тип | Смысл |
|---|---|---|
| `url_fraud_requests_total{method,path,status}` | counter | RPS/ошибки по эндпоинтам |
| `url_fraud_request_duration_seconds{path}` | histogram | латентность (p50/p95/p99) |
| `url_fraud_inflight_requests` | gauge | одновременно обрабатываемых запросов |
| `url_fraud_predictions_total{result}` | counter | доля `bad`/`good` (дрейф) |
| `url_fraud_batches_total` | counter | batch-запросы |
| `url_fraud_model_info{version,threshold}` | gauge | версия задеплоенной модели |

Дашборды:

* **`url-fraud-cluster`** — реплики vs HPA, CPU usage/request по подам, RPS,
  латентность, ошибки, состояние алертов.
* **`url-fraud-model`** — доля предсказаний `bad`, распределение вероятностей,
  версия/threshold модели, drift-сигнал.

---

## Алерт сисадмину

Головное правило — `UrlFraudScalingSaturated` (`monitoring/alerts/url-fraud-alerts.yaml`).
Формулировка задания — «6 реплик **и** на каком-то поде > 80% нагрузки дольше
5 минут» — реализована буквально:

```promql
(
  max(kube_deployment_status_replicas_available{namespace="url-fraud",deployment="url-fraud-api"}) >= 6
  or
  max(kube_horizontalpodautoscaler_status_current_replicas{namespace="url-fraud",horizontalpodautoscaler="url-fraud-api"}) >= 6
)
and on()
(
  max by (pod) (
    sum by (pod) (rate(container_cpu_usage_seconds_total{namespace="url-fraud",container="api"}[5m]))
    /
    sum by (pod) (kube_pod_container_resource_requests{namespace="url-fraud",container="api",resource="cpu"})
  ) > 0.8
)
for: 5m
```

* «загрузка» = `капутилизация CPU / CPU request` — то же, что считает HPA;
  `1.0 == 100%`;
* `and on()` склеивает обе части без меток, поэтому результат — по одному ряду
  на каждый горячий под, но только пока верна часть «6 реплик»;
* `for: 5m` требует, чтобы конъюнкция держалась непрерывно 5 минут.

Вспомогательные правила: `UrlFraudPodHighCpu` (warning, тот же сигнал без
условия о числе реплик), `UrlFraudAtMaxReplicas` (info, потолок достигнут),
`UrlFraudNoAvailableReplicas` (critical, ноль доступных реплик),
`UrlFraudPredictionDrift` (warning, >50% «bad» — повод переобучить).

**Доставка**: `AlertmanagerConfig` (`monitoring/alertmanager/alertmanager-config.yaml`)
шлёт `critical` на e-mail сисадмина (`sysadmin@example.com` через
`smarthost smtp.example.com:587`). Пароль берётся из секрета `alertmanager-smtp`
(ключ `password`). Для демо можно раскомментировать `webhook` (например, на
`httpbin`), чтобы видеть срабатывания без SMTP.

---

## Airflow + git-sync + MLflow

Airflow ставится **в кластер** (Helm-чарт `apache-airflow`) в namespace
`url-fraud`, поэтому RBAC чарта сразу покрывает пода-задачи:

```bash
make deploy-airflow
```

Ключевое в `airflow/values.yaml`:

* `executor: KubernetesExecutor`, `redis.enabled: false`, `workers.replicas: 0` —
  один эфемерный под на задачу, без Celery/Redis;
* DAG'и **не в образе**: `dags.gitSync` клонирует репозиторий и каждые 30с
  подтягивает `hw_10/airflow/dags` (subPath). Пуш в git = новый DAG;
* `config.kubernetes_executor.namespace: url-fraud` — задачи только в этом
  namespace;
* `extraEnv` проставляет `MLFLOW_TRACKING_URI` и имена эксперимента/модели.

DAG `url_fraud_retrain` (`airflow/dags/fraud_retrain_dag.py`), cron `0 3 * * *`:

```
start → retrain (KubernetesPodOperator, mlops.retrain) → promote (mlops.promote) → end
```

* `retrain` запускает `python -m mlops.retrain` из образа `url-fraud-retrain`,
  качает CSV, обучает ту же модель, что отдаёт API (`app.train`), логирует
  params/metrics/model в MLflow и **регистрирует** версию. Если F1/AUC ниже
  порога (`--min-f1`) — задача падает, и продвижения не будет.
* `promote` (только если `retrain_promote=true`) переводит alias `champion`
  в MLflow на новую версию.

Настройки переопределяются Airflow **Variables** (`url_fraud_retrain_image`,
`mlflow_tracking_uri`, `retrain_promote`, …) с фолбэком на переменные окружения.

**MLflow** (`mlflow/k8s/`): Deployment + Postgres (backend store) + артефакты в
S3-совместимом Object Storage (`--serve-artifacts`, бакет из terraform). Ключ
доступа — секрет из terraform output (`mlflow_s3_access_key_id/_secret_key`).

---

## Kafka-атака

```bash
make deploy-kafka      # bitnami/kafka 3.9 (KRaft, 1 брокер) + Service kafka:9092
make attack            # Job url-fraud-attack: producer + consumer
```

`kafka/app/producer.py` линейно наращивает интенсивность от `START_TPS` до
`MAX_TPS` за `RAMP_SECONDS`, публикуя URL в топик `urls`.
`kafka/app/consumer.py` читает батчами (`BATCH_SIZE`) и с `CONCURRENCY`
потоками бьёт по `POST /api/v1/predict/batch`, создавая нагрузку на CPU.

Параметры (ConfigMap `attack-config` / переменные Make):

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `START_TPS` | 20 | стартовая интенсивность |
| `MAX_TPS` | 400 | максимальная интенсивность |
| `RAMP_SECONDS` | 300 | разгон (5 мин) |
| `DURATION` | 900 | общая длительность (15 мин) |
| `BATCH_SIZE` | 16 | URL в одном запросе |
| `CONCURRENCY` | 32 | параллельных запросов консьюмера |

Чтобы ускорить демонстрацию: `make attack RAMP_SECONDS=60 MAX_TPS=300 DURATION=420`.

---

## Проверка: 4 → 6 → алерт

```bash
# окно 1
make scale-watch                # смотрим hpa/pods и загрузку

# окно 2
make attack RAMP_SECONDS=60 MAX_TPS=300 DURATION=600
```

Ожидаемая картина:

1. RPS/CPU растут → HPA поднимает реплики **4 → 5 → 6**.
2. Загрузка на подах > 80% держится ≥ 5 мин.
3. Prometheus переводит `UrlFraudScalingSaturated` в `firing`:
   `make port-forward` → `http://localhost:9090/alerts`.
4. Alertmanager отправляет письмо сисадмину (`http://localhost:9093`).

Быстрые проверки вручную:

```bash
kubectl -n url-fraud get hpa url-fraud-api            # TARGETS .../60%
kubectl -n url-fraud get deploy url-fraud-api         # REPLICAS 6/6
kubectl -n url-fraud get --raw \
  "/apis/monitoring.coreos.com/v1/namespaces/url-fraud/prometheusrules/url-fraud-alerts" >/dev/null && echo "rule ok"
```

После завершения атаки HPA плавно опустится обратно к 4
(`scaleDown.stabilizationWindowSeconds: 300`).

---

## CI/CD

Workflow `.github/workflows/hw_10-ci-cd.yml` (по образцу `hw_09`):

1. **lint** — `ruff check` + `ruff format --check`.
2. **test** — `pytest` с coverage + `python -m app.train --min-f1 0.5 --min-auc 0.85`
   (quality gate на маленькой выборке).
3. **validate** — парсит все YAML, `kubectl kustomize` для `k8s/`, `mlflow/k8s/`,
   `kafka/k8s/`, `compileall` для Python, `helm template` для `airflow/values.yaml`.
4. **build** (matrix) — собирает 4 образа без пуша.
5. **push** (matrix) — пушит образы в Yandex Container Registry (main / теги / manual).
6. **deploy** — применяет манифесты, ставит мониторинг, smoke-тест (по тегу или
   вручную).

Секрет `YC_SA_KEY` (JSON-ключ сервисного аккаунта) и переменные
`YC_REGISTRY_ID`, `YC_FOLDER_ID`, `KUBE_CONTEXT` — в
Settings → Secrets and variables → Actions. Сконфигурировать из CLI:
`make gh-setup`.

---

## Стоимость и удаление ресурсов

```bash
# удалить рабочую нагрузку и доп. стеки
helm -n url-fraud uninstall airflow
kubectl -n url-fraud delete -k kafka/k8s --ignore-not-found
kubectl -n url-fraud delete -k mlflow/k8s --ignore-not-found
helm -n monitoring uninstall kube-prometheus-stack
make undeploy

# полностью снести инфраструктуру (кластер, registry, бакеты)
make tf-destroy
```

> Managed Kafka (`terraform/kafka.tf`) по умолчанию **выключен**
> (`enable_managed_kafka = false`); в кластере используется лёгкий
> одноброкерный Kafka из `kafka/k8s/10-kafka.yaml`, чтобы не платить за
> managed-кластер в демо.
