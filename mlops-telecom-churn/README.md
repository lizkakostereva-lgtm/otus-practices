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

- **Workflow**: `.github/workflows/ci.yml`
- **Этапы**:
  1. **Checkout** кода
  2. **Setup Python** + установка зависимостей
  3. **Lint** (`ruff`) и **Test** (`pytest`)
  4. **Build & Push** Docker-образа в **GitHub Container Registry (GHCR)** через `docker/build-push-action`
- **Dockerfile**: `docker/api/Dockerfile` (multi-stage, `non-root` пользователь, `HEALTHCHECK`).

## 6. Деплой модели в k8s. Масштабирование под нагрузку

- **Манифесты** в `k8s/`: `namespace.yaml`, `configmap.yaml`, `deployment.yaml`, `service.yaml`, `hpa.yaml`, `servicemonitor.yaml`, `secret-example.yaml`.
- **Сервис**: `Service` типа `NodePort` (`:30080`) для доступа к API.
- **Масштабирование**: **HorizontalPodAutoscaler (HPA)** (`k8s/hpa.yaml`). Настроен autoscaling по CPU: `minReplicas: 1`, `maxReplicas: 4`, `targetCPUUtilizationPercentage: 60`. При росте нагрузки количество подов увеличивается автоматически.
- **Healthchecks**: `readinessProbe`/`livenessProbe` на `/health`.

## 7. Переобучение модели по расписанию и/или деградации метрик качества. Раскатка лучшей модели

- **Переобучение по расписанию**: **Kubernetes CronJob** `k8s/cronjob-retrain.yaml`. Расписание `0 3 * * 0` (еженедельно, воскресенье 03:00). Задача CronJob последовательно выполняет: `preprocess -> train -> evaluate -> promote`.
- **Порог/правило промоута**: в `params.yaml` (`promote.metric_name: roc_auc`, `promote.min_improvement: 0.001`). `scripts/promote_model.py` сравнивает метрику новой модели с текущей **Production**-версией в MLflow Model Registry. Если улучшение >= `min_improvement` — новая версия переводится в `Production`, старая архивируется.
- **Раскатка**: после перевода модели в `Production` её можно подтянуть в API (через загрузку из MLflow/артефактов). В рамках проекта показан принцип «обучили -> сравнили -> промоутели».

## 8. Мониторинг метрик и нагрузки и оповещение

- **Метрики приложения**: FastAPI экспортирует метрики в формате **Prometheus** (`/metrics`) через `prometheus-fastapi-instrumentator`. Доступны HTTP-запросы, latency, статус-коды.
- **Сбор метрик**: **Prometheus** (конфиги в `monitoring/prometheus/`). Для k8s используется **ServiceMonitor** (`k8s/servicemonitor.yaml`) в связке с Prometheus Operator (подход из hw_10).
- **Визуализация**: **Grafana** (заготовки дашбордов в `monitoring/grafana/dashboards/`).
- **Алертинг**: **Alertmanager** (`monitoring/alertmanager/alertmanager.yml.example`). Пример правила в `monitoring/prometheus/rules/alerts.yml` — алерт при росте доли 5xx-ошибок.
- **Нагрузка**: проверка масштабирования через нагрузочный тест (`make load-test`, примеры `hey`/`k6`).

## 9. Все этапы формируются скриптами

Весь путь автоматизирован скриптами/Makefile:
- `Makefile` — единая точка входа (`setup`, `data`, `preprocess`, `train`, `api`, `build`, `up-local`, `k8s-deploy`, ...)
- `scripts/` — вспомогательные скрипты (`download_data.py`, `promote_model.py`)
- `src/` — модульный код пайплайна
- `docker-compose.yml`, `Dockerfile`, k8s-манифесты, CI-workflow

## Структура репозитория

```text
mlops-telecom-churn/
├── app/                    # FastAPI инференс (/health, /predict, /metrics)
├── data/                   # raw/processed (не коммитятся)
├── docker/api/             # Dockerfile для API
├── k8s/                    # манифесты Kubernetes (deploy, svc, hpa, cronjob, servicemonitor)
├── monitoring/             # Prometheus/Grafana/Alertmanager конфиги
├── pipelines/              # DVC пайплайн
├── scripts/                # утилиты (promote, download)
├── src/                    # data/features/train/evaluate/utils
├── tests/                  # pytest тесты
├── docker-compose.yml      # MinIO + MLflow + Postgres
├── Makefile                # оркординация шаг
``` (сокращено)

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
