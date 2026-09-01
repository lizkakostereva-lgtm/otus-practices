# hw_07 — Валидация модели + A/B тест с метриками в MLflow + PySpark

Периодический ретрейн модели детекции мошеннических транзакций с **offline A/B-валидацией**
кандидата против текущей Production-модели. Инфраструктура — Yandex Cloud (Terraform + Ansible),
запуск — Managed Airflow, артефакты — S3 (Object Storage), метрики — MLflow.

## Общая схема

```
S3 (source .txt) ──▶ Data Proc (PySpark) ──▶ S3 (clean Parquet)
    ▼                                        ▼
  cleaning                                training (candidate)
                                              │
                                              ▼  MLflow Registry (fraud-detector)
                                          A/B test (candidate vs champion)
                                              │
                                 ┌────────────┴────────────┐
                                 ▼                         ▼
                      apply_ab_decision          destroy_cluster (всегда)
                                 │
                                 ▼  (если значимо) candidate → Production
```

Один **временный** кластер Data Proc на каждую сборку: create → cleaning → train → AB-test →
apply decision → destroy.

## Структура

```
hw_07/
├── terraform/                 # инфраструктура YC (Airflow, MLflow VM, Postgres VM, S3, SG, SA)
├── ansible/                   # провижининг MLflow VM (deploy_mlflow.yml) и Postgres VM
├── airflow/
│   ├── dags/fraud_ab_validation_dag.py   # периодический DAG (еженедельно, пн 05:00)
│   ├── scripts/fraud_cleaning.py         # очистка CSV → Parquet (PySpark)
│   ├── scripts/fraud_train.py            # обучение кандидата (LR+RF), регистрация в MLflow
│   ├── scripts/fraud_ab_test.py          # A/B-валидация кандидата vs champion (ядро)
│   └── make_connection_extra.py          # генератор Extra JSON для conn yc-dataproc
├── Makefile                   # оркестрация всех шагов
├── load_model.py              # пример загрузки Production-модели из MLflow Registry
├── otus-validation-main/      # пример стратегии A/B (папка, приходящая в задании)
└── .github/workflows/deploy-dags.yml  # CI: DAG + скрипты → S3 по push
```

## Стратегия A/B-валидации (fraud_ab_test.py)

Обе модели — **champion** (current Production в Registry) и **candidate** (свежий run) —
применяются к **одному** holdout-срезу (парный дизайн). По bootstrap-репликам holdout:

- выборочные распределения метрик для каждой модели;
- доверительные интервалы (95 %);
- p-value (t-тест Стьюдента) и размер эффекта **Cohen's d**;
- решение о выкате: значимое улучшение **F1** (p < alpha И d >= threshold) +
  PR-AUC не деградирует.

Фичи, обучение и предсказания — в PySpark на кластере; bootstrap-агрегация — на драйвере
(numpy/pandas) — стандартный приём, удовлетворяет требованию «метрики с PySpark».

## Подготовка окружения

- `yc` авторизован (`yc config list`), `terraform`, `aws` CLI, `ansible-playbook`.
- Переменные окружения для aws (ключи S3-сервисного аккаунта):
  ```bash
  export AWS_ACCESS_KEY_ID=<key_id>
  export AWS_SECRET_ACCESS_KEY=<secret>
  ```

## Сборка и запуск

```bash
make init        # terraform init
make apply       # terraform apply (создаёт ресурсы YC)
make nat         # привязать NAT route table к подсети (обязательно для Data Proc)
make outputs     # IP ВМ, ключи, URI MLflow
make ansible-prep # создать hosts.ini и vars.yml
# заполнить ansible/hosts.ini и ansible/vars.yml (IP, пароли, ключи mlflow-sa)
make provision   # ansible: deploy_postgres.yml → deploy_mlflow.yml
make conn-extra  # Extra JSON для подключения yc-dataproc
make sync        # загрузить DAG и скрипты в S3
```

### Настройка Airflow (UI)

1. **Admin → Variables** (обязательные):
   | Переменная | Описание |
   |---|---|
   | `YC_FOLDER_ID` | folder id |
   | `YC_SUBNET_ID` | подсеть для Data Proc |
   | `YC_SSH_PUBLIC_KEY` | публичный ключ для Data Proc ssh |
   | `DP_SA_ID` | сервисный аккаунт Data Proc |
   | `DP_SA_JSON` | ключ SA (JSON) для Data Proc |
   | `DP_SECURITY_GROUP_ID` | security group Data Proc |
   | `MLFLOW_TRACKING_URI` | URI MLflow-сервера |
   | `MLFLOW_AWS_ACCESS_KEY_ID` / `MLFLOW_AWS_SECRET_ACCESS_KEY` | ключи для артефактов в S3 |
   | `YC_SAMPLE_FRACTION` | доля данных (напр. `0.1`) |
   | `AB_BOOTSTRAP_ITERATIONS` | bootstrap-реплик (напр. `200`) |
   | `AB_ALPHA`, `AB_EFFECT_THRESHOLD` | параметры решения |

2. **Admin → Connections**: создать `yc-dataproc` (тип Yandex Cloud, Extra JSON из
   `make conn-extra`).
3. Включить DAG `fraud_ab_validation_pipeline`, запустить вручную.

## Проверка результатов

```bash
make ui        # SSH-туннель → http://localhost:5000 (MLflow UI)
make health    # curl /health на mlflow-vm
```

В MLflow:
- эксперимент `fraud_detection` — тренировочные метрики кандидата;
- эксперимент `fraud_ab_testing` — метрики A/B: `ab_f1_difference`, `ab_*_p_value`,
  `ab_*_effect_size`, `ab_*_ci_*`, `should_deploy`, решение в Registry;
- Registered model `fraud-detector` — версии (Production = champion, переходы после решения).

## Уничтожение

```bash
make destroy    # terraform destroy — полное удаление ресурсов
```
