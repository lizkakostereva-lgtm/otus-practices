# MLOps-конвейер для прогнозирования оттока клиентов телекоммуникационной компании

Дипломный проект по курсу MLOps. Фокус — автоматизация всего пути от сырых данных до переобучения и деплоя модели в Kubernetes, а не максимизация метрик.

В репозитории **два варианта инфраструктуры**:

| Вариант | Что это | Состояние |
|---------|---------|-----------|
| **Полный** (`terraform/`, `ansible/`, `spark/`) | Эталонная конфигураация «как если бы квот не было»: managed Kubernetes (3 зоны), Yandex Data Processing (Spark), отдельные VM под Postgres / MLflow / Airflow, S3, Container Registry. Собирается теми же командами, проверен `terraform validate`. | справочник (не развёрнут — см. «Отклонения от плана») |
| **Тестовый** (`terraform-quota/`, `ansible-quota/`) | Развёрнутый стенд под учебные квоты: 2 VM (docker compose: Postgres + MLflow + Airflow; k3s: API + HPA + CronJob + мониторинг), S3, Container Registry, CI/CD GitHub Actions. | **развёрнут и работает** |

ML-код (`src/`, `app/`, `docker/`, `k8s/`, тесты) один и тот же для обоих вариантов.

## 1. Постановка ML задачи

**Бизнес-контекст:** отток клиентов (customer churn) — одна из ключевых проблем телеком-операторов. Привлечение нового клиента обычно дороже удержания существующего, поэтому прогноз вероятности оттока позволяет заранее таргетировать клиентов акциями по удержанию.

**ML-задача:** бинарная классификация — предсказать вероятность ухода клиента (`Churn = Yes/No`).

**Датасет:** [Telco Customer Churn (Kaggle)](https://www.kaggle.com/datasets/blastchar/telco-customer-churn)

**Цель проекта:** построить автоматизированный MLOps-конвейер, демонстрирующий процессы сбора, предобработки, валидации, обучения, доставки, мониторинга и переобучения модели.

**Метрики для иллюстрации:** `ROC-AUC`, `F1-score`, `Accuracy`.

## 2. Конвейер предобработки данных. Хранение данных

- **Исходные данные** скачиваются в `data/raw/` (скрипт `scripts/download_data.py`).
- **Предобработка** реализована в `src/data/preprocess.py` (обработка `TotalCharges`, кодирование целевой переменной `Churn`, базовая чистка).
- **Хранение**: данные загружаются/хранятся в **S3-совместимом хранилище**. Локально используется **MinIO** (`docker-compose.yml`), в общем случае — любой S3 (Yandex/Object Storage и др.).
- **Воспроизводимость**: пайплайн описан через `dvc.yaml`/`Makefile` (`make preprocess`, `make dvc-repro`).
- **Конфигурация**: пути, параметры сплита вынесены в `params.yaml`.

## 3. Создание модели и фиче-инжиниринг

- **Фиче-инжиниринг**: `src/features/build_features.py`. Числовые признаки масштабируются (`StandardScaler`), категориальные — one-hot кодируются (`OneHotEncoder`).
- **Модель**: `LogisticRegression` (sklearn). Выбрана намеренно простой для демонстрации автоматизации процесса (в соответствии с рекомендациями курса).
- **Пайплайн**: `sklearn.Pipeline` (`preprocessor + classifier`) для единообразного препроцессинга при обучении и инференсе.
- **Артефакты**: `models/pipeline.joblib`, `models/preprocessor.joblib`, `models/model.joblib`.

## 4. Обучение модели. Логирование метрик

- **Трекинг**: **MLflow** (`docker-compose.yml` поднимает MLflow + Postgres). URI задаётся через `MLFLOW_TRACKING_URI`.
- **Логирование**: в каждом запуске `src/train/train.py` логирует `params`, `metrics` (`roc_auc`, `f1`, `accuracy`), артефакты и саму модель (`mlflow.sklearn.log_model`).
- **Реестр моделей**: используется **MLflow Model Registry** (`telecom-churn-model`). Скрипт `scripts/promote_model.py` реализует логику выбора и продвижения лучшей модели.
- **Оценка**: `src/evaluate/evaluate.py` выводит метрики и `classification_report`.

## 5. CI/CD конвейер – создание Docker-образа из модели

- **Workflow**: `.github/workflows/mlops-telecom-churn-ci-cd.yml` в корне репозитория (GitHub читает только корневой каталог `.github/workflows`, поэтому файл вынесен туда).
- **Этапы**:
  1. **Lint** (`ruff check app src tests scripts`)
  2. **Test** (`pytest -q`)
  3. **Build** Docker-образа (`docker/build-push-action`, multi-stage `docker/api/Dockerfile`)
  4. **Push** в **GitHub Container Registry (GHCR)** через встроенный `GITHUB_TOKEN` — не требует никаких секретов
  5. **Push в Yandex Container Registry** — если заданы `YC_SA_KEY` (secret) и `YC_REGISTRY_ID` (variable); без них — fallback в GHCR, пайплайн остаётся зелёным
  6. **Deploy в k3s** — автоматически после push (`set image` + `rollout status` + smoke-тест `/health`); пока не заданы `KUBECONFIG_B64` и `YC_REGISTRY_ID`, шаг пропускается с notice, не ломая пайплайн
- **Триггеры**: push в любую ветку (изменения в `mlops-telecom-churn/**`) и pull request.

## 6. Деплой модели в k8s. Масштабирование под нагрузку

- **Манифесты** в `k8s/`: `namespace.yaml`, `configmap.yaml`, `deployment.yaml`, `service.yaml`, `hpa.yaml`, `cronjob-retrain.yaml`, `servicemonitor.yaml`, `monitoring/prometheusrule-churn.yaml`, `secret-example.yaml`.
- **Сервис**: `Service` типа `NodePort` (`:30080`) для доступа к API.
- **Масштабирование**: **HorizontalPodAutoscaler (HPA)** (`k8s/hpa.yaml`). Настроен autoscaling по CPU: `minReplicas: 1`, `maxReplicas: 4`, `averageUtilization: 60`. При росте нагрузки количество подов увеличивается автоматически (в k3s metrics-server встроен).
- **Healthchecks**: `readinessProbe`/`livenessProbe` на `/health`.
- **Образ в кластере**: `cr.yandex/<registry_id>/mlops-telecom-churn-api:latest`, pull через `imagePullSecret yc-cr` (создаётся Ansible из ключа сервисного аккаунта).

## 7. Переобучение модели по расписанию и/или деградации метрик качества. Раскатка лучшей модели

- **Переобучение по расписанию**: **Kubernetes CronJob** `k8s/cronjob-retrain.yaml`. Расписание `0 3 * * 0` (еженедельно, воскресенье 03:00). Задача последовательно выполняет: `preprocess -> train -> evaluate -> promote`.
- **Переобучение по деградации**: алерт `HighErrorRate`/`HighLatencyP95` (см. §8) сигнализирует о проблемах; ручной/внеплановый запуск переобучения — `kubectl -n mlops-churn create job --from=cronjob/churn-retrain retrain-manual`.
- **Порог/правило промоута**: в `params.yaml` (`promote.metric_name: roc_auc`, `promote.min_improvement: 0.001`). `scripts/promote_model.py` сравнивает метрику новой модели с текущей **Production**-версией в MLflow Model Registry. Если улучшение >= `min_improvement` — новая версия переводится в `Production`, старая архивируется.
- **Раскатка**: API-образ универсальный, модель в него не вшита. При старте под (`app/predictor.py`) сначала ищет локальные `models/*.joblib`, а если их нет — скачивает **Production**-версию из MLflow Model Registry (`models:/telecom-churn-model/Production`). Поэтому «промоут в Registry -> рестарт пода» = раскатка новой модели без пересборки образа:
  ```bash
  kubectl -n mlops-churn rollout restart deployment/churn-api
  ```
- **Airflow**: альтернативный планировщик (`airflow/dags/churn_retrain_dag.py`) с теми же шагами, запускается вручную/по расписанию из UI.
- **Spark (полный вариант)**: `airflow/dags/churn_spark_dataproc_dag.py` каждый запуск поднимает временный Spark-кластер в Yandex Data Processing, исполняет `spark/preprocess_churn.py` и `spark/train_churn.py`, регистрирует модель в MLflow Model Registry и удаляет кластер (`DataprocCreateClusterOperator -> CreatePysparkJobOperator -> DeleteClusterOperator`, `trigger_rule=ALL_DONE`). Трешхолд промоута тот же — `scripts/promote_model.py`.

## 8. Мониторинг метрик и нагрузки и оповещение

- **Метрики приложения**: FastAPI экспортирует метрики в формате **Prometheus** (`/metrics`) через `prometheus-fastapi-instrumentator`. Доступны HTTP-запросы (`http_requests_total`), latency (`http_request_duration_seconds`) и статус-коды.
- **Сбор метрик**: **Prometheus Operator** (kube-prometheus-stack, ставится Helm-ом в `monitoring/kube-prometheus-stack-values.yaml`). Приложение подключено через **ServiceMonitor** (`k8s/servicemonitor.yaml`).
- **Визуализация**: **Grafana** (`monitoring/kube-prometheus-stack-values.yaml`) — стандартные дашборды Kubernetes + метрики API.
- **Алертинг**: **Alertmanager** + правила `k8s/monitoring/prometheusrule-churn.yaml` (`HighErrorRate` — доля 5xx > 5% за 5 минут, `HighLatencyP95` — p95 > 1 c).
- **Нагрузка**: `make load-test` / `hey -n 2000 -c 20 http://<k3s_ip>:30080/health` — HPA добавляет поды, видно в Grafana и `kubectl get hpa`.

## 9. Все этапы формируются скриптами

Весь путь автоматизирован скриптами/Terraform/Ansible:
- **Полный вариант**: `terraform/` (VPC + NAT, managed Kubernetes на 3 зоны, VM Postgres/MLflow/Airflow, Dataproc SG + SA, S3, registry), `ansible/` (`deploy_postgres.yml`, `deploy_mlflow.yml`, `deploy_airflow.yml`, `deploy_k8s.yml`), `spark/` (PySpark-джобы) и DAG `airflow/dags/churn_spark_dataproc_dag.py`.
- **Тестовый вариант (развёрнут)**: `terraform-quota/` — 2 VM в разных зонах, S3, registry, статические IP; `ansible-quota/` (`render.py` + `site.yml`) — провижининг стенда: Docker/MLflow/Airflow, сборка и push образа, запуск пайплайна, k3s, манифесты, kube-prometheus-stack, TCP-прокси для UI.
- `Makefile`, `scripts/`, `src/`, `docker/`, `k8s/`, `.github/workflows/` — локальный запуск, код пайплайна, образы, манифесты, CI/CD.

## 10. Тестовый вариант — развёрнутый стенд (Yandex Cloud, учебные квоты)

Так как квоты триального аккаунта — 2 vCPU / 16 ГБ RAM / 20 ГБ HDD на зону, стенд разложен на две VM в разных зонах:

| VM | Зона | Ресурсы | Что запущено |
|----|------|---------|--------------|
| `mlops-churn-services` | ru-central1-d | 2 vCPU / 6 ГБ / 20 ГБ HDD | PostgreSQL, MLflow, Airflow (docker compose) |
| `mlops-churn-k3s` | ru-central1-a | 2 vCPU / 12 ГБ / 20 ГБ HDD | k3s, API + HPA + CronJob, kube-prometheus-stack, TCP-прокси на MLflow/Airflow |

**Адреса** (все — через публичный IP k3s-VM, который доступен из любой сети):

| Сервис | URL | Логин |
|--------|-----|-------|
| MLflow | http://51.250.77.18:5000 | — |
| Airflow | http://51.250.77.18:8080 | `admin` / `admin` |
| API (Swagger) | http://51.250.77.18:30080/docs | — |
| Grafana | http://51.250.77.18:30300 | `admin` / `admin` |
| Prometheus | http://51.250.77.18:30900 | — |
| Alertmanager | http://51.250.77.18:30903 | — |

```bash
# Проверка API
curl -s http://51.250.77.18:30080/health
curl -s http://51.250.77.18:30080/predict -H 'Content-Type: application/json' -d '{
  "gender": "Female", "SeniorCitizen": 0, "Partner": "Yes", "Dependents": "No",
  "tenure": 1, "PhoneService": "No", "MultipleLines": "No phone service",
  "InternetService": "DSL", "OnlineSecurity": "No", "OnlineBackup": "Yes",
  "DeviceProtection": "No", "TechSupport": "No", "StreamingTV": "No",
  "StreamingMovies": "No", "Contract": "Month-to-month", "PaperlessBilling": "Yes",
  "PaymentMethod": "Electronic check", "MonthlyCharges": 29.85, "TotalCharges": 29.85
}'
```

**Развёртывание с нуля:**

```bash
cd mlops-telecom-churn
terraform -chdir=terraform-quota init && terraform -chdir=terraform-quota apply
python3 ansible-quota/render.py            # inventory + group_vars из terraform outputs
cd ansible-quota && ansible-playbook site.yml
```

`site.yml` делает всё сам: загружает датасет в Object Storage, ставит Docker, собирает и пушит образ API в `cr.yandex`, поднимает Postgres+MLflow+Airflow, прогоняет пайплайн (preprocess -> train -> evaluate -> promote), ставит k3s, применяет манифесты, ставит kube-prometheus-stack и забирает kubeconfig в `~/.kube/mlops-k3s.yaml`.

Код на VM копируется по SSH из рабочей копии на ноутбуке (tar + `unarchive`), потому что с сервисной VM не открывается `github.com`. CI при этом работает через GitHub Actions и GHCR, как обычно.

> Быстрый локальный аналог без облака: `make up-local && make local-pipeline && make api`.

**Ручные шаги (только они):**
1. `terraform-quota/terraform.tfvars` — токен, folder_id, SSH-ключ (файл в `.gitignore`).
2. В GitHub (Settings -> Secrets and variables -> Actions), только если нужен push в YC CR и деплой из CI:
   - secret `YC_SA_KEY` — JSON авторизованного ключа (`terraform -chdir=terraform-quota output -raw sa_key_json`);
   - variable `YC_REGISTRY_ID` — `terraform -chdir=terraform-quota output -raw registry_id`;
   - secret `KUBECONFIG_B64` — `base64 -i ~/.kube/mlops-k3s.yaml`.
3. Ключ `~/.ssh/mlops_diploma` нужен для Ansible (создан заранее, публичная часть зашита в tfvars).

> Публичный IP сервисной VM (`158.160.144.156`) не маршрутизируется из некоторых клиентских сетей, поэтому MLflow и Airflow опубликованы через TCP-прокси (socat) на k3s-VM: порты 5000/8080. Внутри кластера поды ходят в MLflow по приватному адресу `10.130.0.3:5000`.

### Что показать на скриншотах

1. **Данные в S3**: консоль Yandex Object Storage, бакеты `mlops-churn-quota-{data,dags,mlflow}-ek-20251102`.
2. **MLflow**: эксперимент `telecom-churn` с запусками, метрики `roc_auc/f1/accuracy`, артефакты; вкладка Models -> `telecom-churn-model`, версия в стадии `Production`.
3. **Airflow**: DAG `churn_retrain_pipeline`, успешный запуск (4 задачи зелёные).
4. **CI/CD**: GitHub Actions run `MLOps Telecom Churn CI/CD` (lint/test/build/push зелёные).
5. **API**: Swagger `/docs`, `GET /health` (`model_loaded: true`), `POST /predict` с примером.
6. **HPA**: `kubectl get hpa -n mlops-churn` до/после `hey`, плюс график в Grafana.
7. **Мониторинг**: Prometheus targets (ServiceMonitor `churn-api`), Grafana-дашборд, Alertmanager (алерт при падении readiness/5xx).
8. **CronJob**: `kubectl get cronjob,jobs -n mlops-churn`, ручной Job из CronJob.
9. **Терраформ**: `terraform -chdir=terraform-quota output` (IP, registry, бакеты).


## 11. Полный вариант инфраструктуры (эталон, без учёта квот)

Полный вариант показывает, как проект разворачивался бы на «взрослом» облаке. Конфигурация собрана и проверена (`terraform validate`), но не применялась: суммарные запросы (managed K8s на 3 зоны + Dataproc + 3 VM) превышают квоты триального аккаунта (см. «Отклонения от плана»). Все файлы в репозитории и запускаются теми же командами, что и квотный вариант: `terraform apply -> ansible-playbook -> применить манифесты`.

### Схема

```text
                        ┌──────────────────────────────────────────────────────┐
                        │  VPC mlops-churn-net (NAT-gateway, subnet-роутинг)   │
                        │  зоны ru-central1-a/b/d                              │
                        └─────────────────┬────────────────────────────────────┘
      ┌───────────────────────────────────┼──────────────────────────────────────┐
      │                                   │                                      │
┌─────▼──────────────┐      ┌─────────────▼──────────────┐        ┌───────────────▼───────┐
│ Managed Kubernetes │      │ Подсеть services           │        │ Yandex Data Processing│
│ региональный master│      │ 10.150.10.0/24             │        │ (Spark) — временный   │
│ + node group 3×2vCPU│     │ postgres VM (5432)         │        │ кластер на каждый     │
│ churn-api deployment│     │ mlflow VM (5000, S3-артеф.)│        │ запуск DAG'а          │
│ + HPA + ServiceMonitor│   │ airflow VM (8080, DAG'и)   │◄───────│ create → pyspark →    │
│ + CronJob retrain   │     └─────────────┬──────────────┘        │ delete (ALL_DONE)     │
│ + kube-prometheus-stack│                │                       └───────────┬───────────┘
└─────┬──────────────┘                    │                spark job читает   │
      │ imagePullSecret yc-cr             │                raw/из S3, пишет   ▼
      │                                   │                parquet + модель ┌───────────────────┐
      ▼                                   ▼                                 │ Object Storage S3 │
┌──────────────────┐          ┌──────────────────────┐                      │ data / dags / mlflow│
│ Container Registry│         │ GitHub Actions CI/CD │──── push образа ────▶│ (3 бакета)         │
│ cr.yandex         │ ◀───────│ lint→test→build→push │                      └───────────────────┘
└──────────────────┘          └──────────────────────┘
```

### Состав компонентов

| Компонент | Файлы | Роль |
|-----------|-------|------|
| Managed Kubernetes (региональный, 3 зоны, NAT, SG) | `terraform/kubernetes.tf`, `terraform/network.tf` | «нормальный» куб для API, HPA, CronJob |
| Postgres VM (system PostgreSQL, роли mlflow + airflow) | `terraform/vms_services.tf`, `ansible/deploy_postgres.yml` | backend store MLflow + Метаданные Airflow |
| MLflow VM (systemd unit, S3-артефакты, порт 5000) | `terraform/vms_services.tf`, `ansible/deploy_mlflow.yml` | tracking server + Model Registry |
| Airflow VM (docker compose: webserver+scheduler, LocalExecutor) | `terraform/vms_services.tf`, `ansible/deploy_airflow.yml`, `ansible/templates/docker-compose.airflow.j2` | планировщик переобучения; соединение `yc-dataproc` для операторов |
| Yandex Data Processing (Spark) | `terraform/dataproc.tf` (SG + SA + ключ), `spark/*.py`, DAG `churn_spark_retrain` | масштабируемая предобработка/обучение на Spark — кластер создаётся и удаляется на каждый запуск DAG'а (не платим в простое) |
| S3 (3 бакета: data/dags/mlflow) | `terraform/storage.tf` | «данные останутся на облачном ресурсе» |
| Container Registry + CI SA (push/pull/k8s.editor) | `terraform/registry.tf`, `terraform/service_accounts.tf` | образы для CI/CD и кластера |
| Деплой манифестов в managed K8s | `ansible/deploy_k8s.yml` | namespace/secrets/configmap/apply + smoke-тест |

### Как развернуть полный вариант

```bash
# 0) terraform/terraform.tfvars: cloud_id, folder_id, token, ssh_public_key,
#    bucket-имена, airflow_admin_password (см. переменные в variables.tf)
cd mlops-telecom-churn
terraform -chdir=terraform init && terraform -chdir=terraform apply

# 1) Провижининг сервисных VM (ansible/vars.yml и hosts.ini - из tf output)
cp ansible/vars.yml.example ansible/vars.yml && cp ansible/hosts.ini.example ansible/hosts.ini
make provision-mlflow            # postgres VM + mlflow VM
make provision-airflow           # airflow VM: build образа, compose, соединение yc-dataproc

# 2) Загрузить DAG'и и Spark-скрипты в S3
BUCKET_DAGS=$(terraform -chdir=terraform output -raw bucket_dags) make sync-dag-assets

# 3) Kubeconfig и деплой в managed Kubernetes
yc managed-kubernetes cluster get-credentials --name mlops-churn-cluster --external
TF_REGISTRY_ID=$(terraform -chdir=terraform output -raw registry_id) \
TF_SA_KEY_JSON=$(terraform -chdir=terraform output -raw ci_sa_key_json) \
TF_S3_ACCESS_KEY=$(terraform -chdir=terraform output -raw s3_access_key) \
TF_S3_SECRET_KEY=$(terraform -chdir=terraform output -raw s3_secret_key) \
TF_BUCKET_DATA=$(terraform -chdir=terraform output -raw bucket_data) \
TF_MLFLOW_URI=http://$(terraform -chdir=terraform output -raw mlflow_public_ip):5000 \
make deploy-k8s
make monitoring-install   # kube-prometheus-stack

# 4) Переобучение на Spark: в Airflow задать Variables из terraform output
#    (YC_FOLDER_ID, YC_SUBNET_ID, YC_S3_BUCKET, DP_SA_ID, DP_SA_JSON,
#    DP_SECURITY_GROUP_ID, MLFLOW_TRACKING_URI, MLFLOW_AWS_ACCESS_KEY_ID,
#    MLFLOW_AWS_SECRET_ACCESS_KEY) и включить DAG churn_spark_retrain,
#    либо запустить вручную в UI.
```

Проверка Dataproc-ключа/соединения: `terraform -chdir=terraform output -raw dataproc_sa_key_json` — это значение подставляется как `--conn-extra` в соединение `yc-dataproc` внутри Airflow. `subnet_id` — `terraform output services_subnet_id`, `security_group_id` — `output dataproc_security_group_id`.

### Что даёт Spark в полном варианте

Датасет Telco мал, поэтому Spark здесь — демонстрация **масштабируемого конвейера**, а не выигрыша в метриках: чистка и фиче-инжиниринг вынесены в PySpark-джобы (`pyspark.ml`: StringIndexer/OneHotEncoder/VectorAssembler + LogisticRegression), модель логируется в тот же MLflow (`mlflow.spark.log_model`) и регистрируется в тот же Model Registry, так что трешхолд промоута и раскатка в k8s работают без изменений.

## 12. Что планировалось и что получилось (отклонения от плана)

| Планировалось на этапе выбора проекта | Получилось | Причина отклонения |
|---|---|---|
| Managed Kubernetes (региональный, 3 ноды, 3 зоны) | k3s на отдельной VM — API/HPA/CronJob/ServiceMonitor работают так же | Квоты триального аккаунта: 2 vCPU / 16 ГБ RAM / 20 ГБ HDD на зону — managed K8s целиком не разместить |
| Отдельные VM: Postgres, MLflow, Airflow | 3 сервиса в docker compose на одной «сервисной» VM, k8s — на второй VM | Та же причина; ansible-quota разворачивает стек одной командой |
| Spark на Yandex Data Processing (Airflow-операторы, кластер на запуск) | Готов полный набор (terraform/dataproc.tf + spark/ + DAG churn_spark_retrain), не выполнялся из-за квот | Dataproc-кластер даже минимальный (1 master + 1 datanode, s3-c2-m8) не проходит по квоте vCPU |
| Managed Airflow (Yandex Cloud) | Self-hosted Airflow (docker compose, LocalExecutor) | Managed Airflow даёт quota-независимые ресурсы, но требует managed-фич (S3 sync DAG'ов); на самостоятельном Airflow DAG'и те же |
| MLflow на отдельной VM | MLflow в compose с Postgres backend и S3-артефактами | Аналогично квотам |

Вся ML-часть, CI/CD, деплой/масштабирование в k8s, мониторинг и переобучение — без отклонений; инфраструктура сведена к 2 VM, S3, Registry и GitHub Actions CI/CD, что соответствует «минимально достаточной» конфигурации из рекомендаций курса.

## Структура репозитория


```text
mlops-telecom-churn/
├── app/                      # FastAPI инференс (/health, /predict, /metrics)
├── airflow/dags/
│   ├── churn_retrain_dag.py        # DAG тестового варианта (sklearn-шаги)
│   └── churn_spark_dataproc_dag.py # DAG полного варианта (Spark на Data Proc)
├── spark/                    # PySpark-джобы полного варианта (preprocess/train)
├── data/                     # raw/processed (не коммитятся)
├── docker/
│   ├── api/Dockerfile        # образ API (используется в CI и в кластере)
│   ├── airflow/Dockerfile    # образ Airflow с ML-зависимостями + yandex provider
│   ├── mlflow/Dockerfile
│   └── postgres/init.sql
├── k8s/                      # namespace, deployment, service, hpa, cronjob,
│                             # servicemonitor, prometheusrule
├── monitoring/               # kube-prometheus-stack values + примеры конфигов
├── scripts/                  # download_data.py, promote_model.py
├── src/                      # data/features/train/evaluate
├── tests/                    # pytest
├── terraform/                # ПОЛНЫЙ вариант: managed K8s, VM Postgres/MLflow/
│                             # Airflow, Dataproc SG/SA, S3, registry
├── ansible/                  # ПОЛНЫЙ вариант: deploy_postgres/mlflow/airflow/k8s
├── terraform-quota/          # ТЕСТОВЫЙ вариант (развёрнут): 2 VM, S3, registry
├── ansible-quota/            # ТЕСТОВЫЙ вариант: site.yml (services+pipeline+k3s)
├── docker-compose.yml        # локально: MinIO + MLflow + Postgres
├── docker-compose.yc.yml     # на сервисной VM: Postgres + MLflow + Airflow
└── Makefile                  # единая точка входа (make help)
```


## Быстрый старт (локально)

```bash
# 1) Установка зависимостей
make setup

# 2) Скачать датасет Telco Churn с Kaggle в data/raw/WA_Fn-UseC_-Telco-Customer-Churn.csv
make init-data

# 3) Поднять локальный стек (MinIO, MLflow)
make up-local

# 4) Прогнать полный пайплайн
make local-pipeline

# 5) Запустить API
make api

# Swagger: http://localhost:8000/docs, Metrics: http\://localhost:8000/metrics
```

## Деплой в Kubernetes (Kind/Minikube)

```bash
# Сборка образа
make build

# Деплой
make k8s-deploy

# Проверка HPA и подов
kubectl get pods -n mlops-churn
kubectl get hpa -n mlops-churn

# Нагрузочный тест для демонстрации масштабирования
hey -n 1000 -c 10 http://localhost:30080/health
```

<!-- standalone deploy trigger: 2026-11-03T06:49:38Z -->
