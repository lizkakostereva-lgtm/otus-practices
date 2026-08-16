# Домашнее задание — Переобучение модели с MLflow + Airflow + PySpark

Система периодического переобучения модели антифрода: Spark-кластер (Yandex Data Proc)
создаётся и удаляется DAG'ом, метрики и артефакты (обученная модель) сохраняются в **MLflow**
и **S3 (Object Storage)**, метаданные MLflow — в **PostgreSQL на отдельной ВМ**, MLflow —
на **второй ВМ** (установка обеих через **Ansible**), оркестрация — **Yandex Cloud Managed
Service for Apache Airflow**. Лучшая модель по метрике AUC автоматически переводится в стадию
**Production** в Model Registry. Все шаги объединены в **Makefile** (`make help`).

---

## Архитектура

```
GitHub Repo (hw_06)
  ├── airflow/dags/fraud_train_dag.py   — DAG: очистка -> обучение -> promote -> destroy
  ├── airflow/scripts/fraud_cleaning.py — PySpark job: S3 raw -> Parquet (чистые данные)
  ├── airflow/scripts/fraud_train.py    — PySpark job: обучение + MLflow tracking
  ├── airflow/make_connection_extra.py  — генератор Extra JSON для подключения yc-dataproc
  ├── ansible/                          — установка Postgres и MLflow на ВМ
  ├── Makefile                          — единые команды для всех шагов
  └── .github/workflows/deploy-dags.yml — CI/CD: DAG+скрипты в S3
        ↓ aws s3 sync (make sync)
S3 (airflow-dags-bucket-ek)  ← читает Managed Airflow (code_sync)
S3 (spark-bucket-ek)         ← данные очистки + PySpark скрипты + экспорт метрик CSV
S3 (mlflow-bucket-ek)        ← артефакты MLflow (обученные модели)
        ↓
Managed Airflow (Airflow 3.1)
  └── DAG fraud_retrain_pipeline
        ├── DataprocCreateClusterOperator → временный Spark-кластер
        ├── DataprocCreatePysparkJobOperator (cleaning)  — S3 raw -> clean Parquet
        ├── DataprocCreatePysparkJobOperator (training)  — clean Parquet -> MLflow
        ├── PythonOperator promote_best_model            — REST API MLflow
        └── DataprocDeleteClusterOperator                — удаление кластера
        ↓
postgres-vm (PostgreSQL, Ansible)  ← метаданные MLflow (SQL)
mlflow-vm   (MLflow, Ansible)      ← Tracking Server :5000, пишет артефакты
        ↓ boto3 (S3 endpoint)
S3 (mlflow-bucket-ek/artifacts)    ← модель; метрики -> spark-bucket-ek/mlflow_metrics
```

- **postgres-vm** — ВМ с PostgreSQL (метаданные MLflow). Ставится Ansible
  (`ansible/deploy_postgres.yml`): apt postgresql, пользователь `mlflow`, БД `mlflow`,
  `listen_addresses='*'`, `pg_hba` для доступа из подсети.
- **mlflow-vm** — ВМ с MLflow Tracking Server. Ставится Ansible
  (`ansible/deploy_mlflow.yml`): venv + `mlflow` + systemd-сервис на порту 5000.
- **Артефакты** — S3 `mlflow-bucket-ek` через `--default-artifact-root s3://.../artifacts`.
- **Spark-кластер** — создаётся DAG'ом на каждый запуск и удаляется после (экономия ресурсов).

## Схема DAG `fraud_retrain_pipeline`

```
setup_connections → create_cluster → submit_cleaning_job → submit_training_job
                                                            → promote_best_model → destroy_cluster
                                                                    (trigger_rule=ALL_DONE)
```

Расписание: `0 5 * * 1` (еженедельно в понедельник 05:00, по умолчанию). Для проверки
можно запустить вручную кнопкой **Trigger DAG**.

`promote_best_model` работает через REST API MLflow (без пакета `mlflow` на стороне Airflow):
ищет в эксперименте `fraud_detection` run с максимальным `best_auc`, находит соответствующую
версию registered model `fraud-detector` и переводит её в стадию **Production**
(архивируя прежние версии). Так новая модель попадает в «боевую» только если она лучше предыдущих.

---

## Makefile — все команды в одном месте

Все шаги ниже дублируются целями Makefile (запуск из каталога `hw_06`). `make help` — список.

```bash
make init            # terraform init
make apply           # terraform apply (создание ВМ, Airflow, бакетов)
make nat             # подключить NAT route table к подсети (обязательно после apply)
make outputs         # IP ВМ, ключи, URI MLflow
make ansible-prep    # создать hosts.ini и vars.yml из примеров
make provision       # Ansible: PostgreSQL, затем MLflow
make conn-extra      # Extra JSON для подключения yc-dataproc в Airflow
make sync            # загрузка DAG и скриптов в S3 (требует AWS-ключи в env)
make ui              # SSH-туннель к MLflow UI -> http://localhost:5000
make health          # проверка MLflow /health на ВМ
make destroy         # terraform destroy (удаление всего)
```

## Предварительные требования

1. **Yandex Cloud** аккаунт с биллингом, правами владельца/редактора в каталоге.
2. **Yandex Cloud CLI** (`yc`) — `yc init`.
3. **Terraform** ≥ 1.5.
4. **Ansible** ≥ 2.14 (для установки MLflow и PostgreSQL на ВМ).
5. **AWS CLI** для загрузки DAG/скриптов в Object Storage.
6. **make** (в macOS встроен).
7. Существующие **VPC** и **подсеть** (дефолтные `default` / `default-ru-central1-a`).
8. Если остались ресурсы из прошлых ДЗ (бакеты `spark-bucket-ek`, `airflow-dags-bucket-ek`) —
   удалить их или переопределить имена в `terraform.tfvars`.

## Шаг 1. Конфигурация Terraform

```bash
cp terraform/terraform.tfvars.example terraform/terraform.tfvars
```

Заполнить `terraform/terraform.tfvars`:

| Переменная | Описание |
|---|---|
| `cloud_id`, `folder_id` | ID облака и каталога (`yc config list`) |
| `network_id`, `subnet_id` | ID сети и подсети (`yc vpc network list`, `yc vpc subnet list`) |
| `token` | OAuth-токен или ключ SA |
| `ssh_public_key` | Публичный SSH-ключ |
| `airflow_admin_password` | Пароль админа Airflow (мин. 8 символов, буквы + цифры) |
| `mlflow_pg_password` | Пароль пользователя `mlflow` в PostgreSQL (тот же, что в ansible/vars.yml) |
| `mlflow_bucket_name` | Бакет артефактов MLflow (по умолч. `mlflow-bucket-ek`) |

## Шаг 2. Развёртывание инфраструктуры

```bash
make init
make apply
make nat
make outputs
```

Terraform создаст:

| Ресурс | Имя | Назначение |
|---|---|---|
| `postgres-vm` | ВМ (Ubuntu 22.04) | PostgreSQL — метаданные MLflow (ставит Ansible) |
| `mlflow-vm` | ВМ (Ubuntu 22.04) | MLflow Tracking Server на порту 5000 (ставит Ansible) |
| `mlflow-sa` + статический ключ | Сервисный аккаунт | Запись артефактов в S3 |
| `mlflow-bucket-ek` | S3 Bucket | Артефакты MLflow (модели) |
| `mlflow-sg`, `postgres-sg` | Security Groups | Доступ к MLflow :5000 и Postgres :5432 из подсети + SSH |
| Managed Airflow, `spark-bucket-ek`, `airflow-dags-bucket-ek` | — | Оркестрация и хранилища |

`make nat` привязывает route table NAT к подсети — без этого ВМ и Data Proc не имеют
доступа в интернет. Проверка: `yc vpc subnet get <subnet-id>` — должен появиться `route_table_id`.

`make outputs` выведет (пригодятся далее):

| Output | Зачем |
|---|---|
| `mlflow_vm_public_ip` | hosts.ini, группа `[mlflow]` |
| `postgres_vm_public_ip` | hosts.ini, группа `[postgres]` |
| `postgres_vm_internal_ip` | ansible `pg_host` |
| `mlflow_tracking_uri` | Airflow variable `MLFLOW_TRACKING_URI` |
| `mlflow_sa_access_key_id` / `mlflow_sa_secret_key` | ansible `mlflow_aws_key` / `mlflow_aws_secret` |
| `airflow_webui_url` | вход в Airflow UI |

## Шаг 3. Установка PostgreSQL и MLflow (Ansible)

```bash
make ansible-prep
# -> создаются ansible/hosts.ini и ansible/vars.yml (уже в .gitignore)
```

1. В `ansible/hosts.ini` подставить публичные IP ВМ (`make outputs`):
   `postgres_vm_public_ip` → `[postgres]`, `mlflow_vm_public_ip` → `[mlflow]`; в
   `[all:vars]` — путь к приватному SSH-ключу.
2. В `ansible/vars.yml` подставить:
   - `pg_host` — `postgres_vm_internal_ip`;
   - `mlflow_aws_key` / `mlflow_aws_secret` — статический ключ `mlflow-sa`;
   - `pg_password` — тот же пароль, что в `terraform.tfvars`;
   - `artifact_root` — `s3://<mlflow_bucket_name>/artifacts`.
3. Запустить (сначала PostgreSQL, потом MLflow):

```bash
make provision
```

Проверка: `make health` → должен вернуть `{"status":"OK"}`.

## Шаг 4. Загрузка DAG и скриптов в S3

Нужны статические ключи сервисного аккаунта с доступом к Object Storage
(например, `dataproc-sa`):

```bash
yc iam access-key create --service-account-name dataproc-sa --description "airflow-deploy"
# в выводе: key_id = AWS_ACCESS_KEY_ID, secret = AWS_SECRET_ACCESS_KEY
```

```bash
export AWS_ACCESS_KEY_ID=<key_id>
export AWS_SECRET_ACCESS_KEY=<secret>
export AWS_ENDPOINT_URL=https://storage.yandexcloud.net

make sync
```

`make sync` = `make sync-dags` (DAG → `airflow-dags-bucket-ek`) + `make sync-scripts`
(скрипты → `spark-bucket-ek/scripts`). Альтернатива — пуш в `main`: GitHub Actions
синхронизирует файлы автоматически.

## Шаг 5. Настройка Airflow

### 5.1. Переменные (Admin → Variables)

| Variable | Описание | Как получить |
|---|---|---|
| `YC_FOLDER_ID` | ID каталога | `yc config list` |
| `YC_ZONE` | Зона (по умолч. `ru-central1-a`) | — |
| `YC_SUBNET_ID` | ID подсети | `yc vpc subnet list` |
| `YC_S3_BUCKET` | Бакет Data Proc (по умолч. `spark-bucket-ek`) | — |
| `YC_SSH_PUBLIC_KEY` | Публичный SSH-ключ | тот же, что в tfvars |
| `DP_SA_ID` | ID `dataproc-sa` | `make outputs` → `service_account_id` |
| `DP_SECURITY_GROUP_ID` | ID security group | `make outputs` → `security_group_id` |
| `DP_SA_JSON` | Authorized key JSON `airflow-sa` | см. ниже |
| `MLFLOW_TRACKING_URI` | URI Tracking Server | `make outputs` → `mlflow_tracking_uri` |
| `MLFLOW_S3_ENDPOINT_URL` | S3 endpoint | `https://storage.yandexcloud.net` |
| `MLFLOW_AWS_ACCESS_KEY_ID` | Статический ключ `mlflow-sa` | `make outputs` → `mlflow_sa_access_key_id` |
| `MLFLOW_AWS_SECRET_ACCESS_KEY` | Секрет `mlflow-sa` | `make outputs` → `mlflow_sa_secret_key` |
| `MLFLOW_EXPERIMENT` (опц.) | Эксперимент MLflow | `fraud_detection` |
| `MLFLOW_MODEL_NAME` (опц.) | Registered model | `fraud-detector` |
| `YC_INPUT_S3` (опц.) | Сырые данные | `s3a://otus-mlops-source-data/*.txt` |
| `YC_CLEAN_OUTPUT_S3` (опц.) | Чистые данные | `s3a://<spark-bucket>/fraud_clean_parquet` |

**Формирование `DP_SA_JSON`** — это authorized key сервисного аккаунта `airflow-sa`
(используется операторами Data Proc для аутентификации в API Yandex Cloud). Собрать его
из outputs Terraform (`airflow_sa_auth_key_id`, `airflow_sa_auth_key_public`,
`airflow_sa_auth_key_private`, `airflow_service_account_id`):

```json
{
  "id": "<airflow_sa_auth_key_id>",
  "service_account_id": "<airflow_service_account_id>",
  "created_at": "2026-08-01T00:00:00Z",
  "key_algorithm": "RSA_2048",
  "public_key": "<airflow_sa_auth_key_public>",
  "private_key": "<airflow_sa_auth_key_private>"
}
```

Вставить целиком в значение переменной `DP_SA_JSON`.

### 5.2. Подключение `yc-dataproc` (Admin → Connections)

Это подключение типа `yandexcloud` нужно операторам Data Proc
(`DataprocCreateClusterOperator`, `DataprocCreatePysparkJobOperator`,
`DataprocDeleteClusterOperator`): через него DAG аутентифицируется в API Yandex Cloud
и передаёт SSH-ключ на ноды создаваемого кластера. Создаётся **один раз**, до первого
запуска DAG.

**Как создать:**

1. Откройте веб-интерфейс Airflow: `make outputs` → `airflow_webui_url`
   (логин `admin`, пароль из `terraform.tfvars`).
2. Перейдите в **Admin → Connections** и нажмите кнопку **+** («Add a new record»).
3. Заполните поля:
   - **Connection Id**: `yc-dataproc` (имя должно совпадать с `YC_SA_CONN_ID` в DAG);
   - **Connection Type**: `yandexcloud`;
   - **Extra**: вставьте JSON из `make conn-extra` (см. ниже).
4. Нажмите **Save**.

**Генерация Extra:**

```bash
make conn-extra
```

Скрипт (`airflow/make_connection_extra.py`) сам выгружает из Terraform outputs
authorized key аккаунта `airflow-sa`, берёт ваш публичный SSH-ключ
(`~/.ssh/id_ed25519.pub` по умолчанию, или `--ssh-public-key <файл>`) и печатает
готовый JSON с корректным экранированием — его нужно вставить в поле **Extra** целиком:

```json
{
  "public_ssh_key": "<ваш публичный SSH-ключ>",
  "service_account_json": "{\"id\": \"...\", \"service_account_id\": \"...\", ...}"
}
```

- `public_ssh_key` — публичный SSH-ключ (тот же, что в `terraform.tfvars`), добавляется
  на ноды Data Proc при создании кластера;
- `service_account_json` — JSON-строка с authorized key `airflow-sa` для аутентификации
  операторов Data Proc в API Yandex Cloud.

> Если после `terraform apply` сервисные ключи пересоздались, снова выполните
> `make conn-extra` и обновите Extra и переменную `DP_SA_JSON`.

## Шаг 6. Активация и проверка DAG

1. **DAGs** → `fraud_retrain_pipeline` (подхватится из S3 через ~1–2 мин после `make sync`).
2. Включить DAG (Toggle On) и нажать **Trigger DAG** для ручного теста.

Один прогон: создание кластера (~10–15 мин) → очистка (~15 мин) → обучение (~15 мин)
→ promote → удаление кластера (~5 мин).

После первого успешного запуска проверить в **MLflow** (`make ui`):
- эксперимент `fraud_detection` с runs (params `lr_*`, `rf_*`; metrics `lr_auc`, `rf_auc`, `best_auc`, `precision`, `recall`, `f1`);
- registered model `fraud-detector` со стадией **Production** у лучшей версии;
- артефакт `best_model` в S3: `s3://mlflow-bucket-ek/artifacts/.../artifacts/best_model`.

Дополнительно сами метрики каждого run дублируются в Object Storage (CSV с колонками
`metric`, `value`, `run_id`) — `s3://spark-bucket-ek/mlflow_metrics/`, чтобы метрики
лежали и в S3, и в метаданных MLflow (PostgreSQL на postgres-vm).

## Доступ к MLflow UI

```bash
make ui      # SSH-туннель, затем открыть http://localhost:5000
make health  # проверка сервиса (SSH на mlflow-vm + curl /health)
```

Порт 5000 открыт только для подсети (Data Proc и Airflow), из интернета — только через туннель.

## Завершение работы и удаление ресурсов (дополнительное задание)

Spark-кластер удаляется DAG'ом после каждого прогона (`destroy_cluster`, `ALL_DONE`).
Для полного удаления всей инфраструктуры и прекращения оплаты:

```bash
make destroy
yc vpc subnet update <subnet-id> --route-table-id ""
```

## Структура репозитория

```
hw_06/
├── Makefile                         # единые команды (make help)
├── airflow/
│   ├── dags/
│   │   └── fraud_train_dag.py      # DAG: очистка -> обучение -> promote -> destroy
│   ├── scripts/
│   │   ├── fraud_cleaning.py       # PySpark job: S3 raw -> clean Parquet
│   │   └── fraud_train.py          # PySpark job: обучение + MLflow (метрики, модель в S3)
│   └── make_connection_extra.py    # генератор Extra JSON для подключения yc-dataproc
├── ansible/
│   ├── deploy_postgres.yml         # установка PostgreSQL на postgres-vm
│   ├── deploy_mlflow.yml           # установка MLflow на mlflow-vm
│   ├── templates/mlflow.service.j2 # systemd-юнит MLflow (PG + S3)
│   ├── hosts.ini.example           # IP ВМ (скопировать в hosts.ini)
│   └── vars.yml.example            # пароли/ключи (скопировать в vars.yml)
├── terraform/
│   ├── airflow.tf                  # Managed Airflow + airflow-sa
│   ├── bucket.tf                   # S3 бакеты (spark, dags, mlflow artifacts)
│   ├── dataproc.tf                 # пояснения (кластер создаёт DAG)
│   ├── mlflow.tf                   # MLflow ВМ + mlflow-sa + static key
│   ├── network.tf                  # NAT gateway, route table
│   ├── postgres.tf                 # ВМ PostgreSQL
│   ├── security_group.tf           # spark-sg + mlflow-sg + postgres-sg
│   ├── service_account.tf          # dataproc-sa + роли
│   ├── variables.tf, outputs.tf, providers.tf, versions.tf
│   └── terraform.tfvars.example
├── .github/workflows/deploy-dags.yml
└── README.md
```
