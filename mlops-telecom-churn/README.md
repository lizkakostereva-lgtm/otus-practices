# MLOps-конвейер для прогнозирования оттока клиентов телекоммуникационной компании

Дипломный проект по курсу MLOps. Фокус — автоматизация всего пути от сырых данных до переобучения и деплоя модели в Kubernetes, а не максимизация метрик.

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
  5. **Push в Yandex Container Registry** — опционально, если заданы `YC_SA_KEY` (secret) и `YC_REGISTRY_ID` (variable)
  6. **Deploy в k3s** — опционально, `workflow_dispatch` с `deploy=true` и секретом `KUBECONFIG_B64`
- **Триггеры**: push в любую ветку (изменения в `mlops-telecom-churn/**`), pull request, ручной запуск.
- **Ручной запуск**: Actions → `MLOps Telecom Churn CI/CD` → Run workflow (можно включить/выключить push образа и деплой).

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


## 8. Мониторинг метрик и нагрузки и оповещение

- **Метрики приложения**: FastAPI экспортирует метрики в формате **Prometheus** (`/metrics`) через `prometheus-fastapi-instrumentator`. Доступны HTTP-запросы (`http_requests_total`), latency (`http_request_duration_seconds`) и статус-коды.
- **Сбор метрик**: **Prometheus Operator** (kube-prometheus-stack, ставится Helm-ом в `monitoring/kube-prometheus-stack-values.yaml`). Приложение подключено через **ServiceMonitor** (`k8s/servicemonitor.yaml`).
- **Визуализация**: **Grafana** (`monitoring/kube-prometheus-stack-values.yaml`) — стандартные дашборды Kubernetes + метрики API.
- **Алертинг**: **Alertmanager** + правила `k8s/monitoring/prometheusrule-churn.yaml` (`HighErrorRate` — доля 5xx > 5% за 5 минут, `HighLatencyP95` — p95 > 1 c).
- **Нагрузка**: `make load-test` / `hey -n 2000 -c 20 http://<k3s_ip>:30080/health` — HPA добавляет поды, видно в Grafana и `kubectl get hpa`.

## 9. Все этапы формируются скриптами

Весь путь автоматизирован скриптами/Terraform/Ansible:
- `terraform/` — эталонная **полная** инфраструктура (VPC + NAT, managed Kubernetes на 3 ноды, отдельные VM под Postgres/MLflow/Airflow, S3, registry) — без учёта квот, в текущем облаке не применяется.
- `terraform-quota/` — **реально развёрнутый** стенд под учебные квоты: 2 VM в разных зонах, S3, registry, статические IP.
- `ansible-quota/` — провижининг стенда: Docker/MLflow/Airflow, сборка и push образа, запуск пайплайна, k3s, манифесты, kube-prometheus-stack, TCP-прокси для UI.
- `Makefile`, `scripts/`, `src/`, `docker/`, `k8s/`, `.github/workflows/` — локальный запуск, код пайплайна, образы, манифесты, CI/CD.

## 10. Развёрнутый стенд (Yandex Cloud, учебные квоты)

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


## Структура репозитория

```text
mlops-telecom-churn/
├── app/                      # FastAPI инференс (/health, /predict, /metrics)
├── data/                     # raw/processed (не коммитятся)
├── docker/
│   ├── api/Dockerfile        # образ API (используется в CI и в кластере)
│   ├── airflow/Dockerfile    # образ Airflow с ML-зависимостями
│   ├── mlflow/Dockerfile
│   └── postgres/init.sql
├── k8s/                      # namespace, deployment, service, hpa, cronjob,
│                             # servicemonitor, prometheusrule
├── monitoring/               # kube-prometheus-stack values + примеры конфигов
├── scripts/                  # download_data.py, promote_model.py
├── src/                      # data/features/train/evaluate
├── tests/                    # pytest
├── terraform/                # эталонная полная инфраструктура (не применяется)
├── terraform-quota/          # реально развёрнутый стенд под квоты
├── ansible-quota/            # провижининг стенда (site.yml + render.py)
├── docker-compose.yml        # локально: MinIO + MLflow + Postgres
├── docker-compose.yc.yml     # на VM: Postgres + MLflow + Airflow
└── Makefile                  # единая точка входа
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
