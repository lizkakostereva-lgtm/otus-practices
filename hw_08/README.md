# hw_08 — Online ML-инференс: Kafka + Spark Structured Streaming + MLflow Model Registry

Непрерывный мониторинг/инференс модели детекции мошеннических транзакций на **потоке
данных**: исторические транзакции переигрываются в **Apache Kafka** с управляемой
скоростью, **Spark Structured Streaming** прогоняет их через champion-модель из
**MLflow Model Registry** и пишет предсказания обратно в Kafka. Дополнительно выполняется
**нагрузочный тест**: ищем «точку перегиба» — при каком TPS consumer начинает отставать.

Инфраструктура MLflow / PostgreSQL / Managed Airflow / S3 **переиспользуется из hw_07**
(Yandex Cloud, Terraform + Ansible). Новое в hw_08 — **Managed Kafka** (Terraform) +
DAG/скрипты потокового инференса + замер производительности.

## Общая схема

```
S3 (clean Parquet / source .txt) ──▶ Data Proc: kafka_producer.py (spark-submit)
                                          │   имитация real-time, целевой TPS
                                          ▼
                              Kafka topic "fraud-transactions"
                                          │
            ┌─────────────────────────────┴─────────────────────────────┐
            ▼                                                           ▼
   Data Proc: fraud_streaming_inference.py                   (метрики в S3/MLflow)
   Spark Structured Streaming                                 measure_performance
   model = mlflow.spark.load_model(models:/fraud-detector/Production)
            │
            ▼  transform + to_json
   Kafka topic "fraud-predictions"
            │
            ▼  destroy_cluster (всегда)
```

- Хаос-продюсер и стример запускаются **параллельно** на одном временном Data Proc-кластере.
- Kafka (Managed Kafka) создаётся Terraform-ом **один раз** и живёт постоянно; Data Proc-кластер
  создаётся и удаляется DAG-ом в каждом запуске (не платим в простое).

## Структура

```
hw_08/
├── terraform/                  # инфраструктура YC (Airflow, MLflow VM, Postgres VM,
│   │                           #  S3, SG, SA + NEW: Managed Kafka + topics + user)
│   ├── kafka.tf                # yandex_mdb_kafka_cluster (fraud-kafka), topics, user
│   ├── security_group.tf       # кafka_sg: 9091/9092 для подсети
│   ├── variables.tf / outputs.tf / terraform.tfvars.example
├── ansible/                    # README: провижининг перенесён из hw_07
├── airflow/
│   ├── dags/fraud_streaming_dag.py        # DAG: create → producer||streaming → measure → destroy
│   ├── scripts/kafka_producer.py          # переигрывает историю в Kafka с целевой TPS
│   ├── scripts/fraud_streaming_inference.py # Structured Streaming: Kafka→MLflow model→Kafka
├── Makefile                    # оркестрация (init→apply→...→sync + perf-test)
└── README.md
```

### airflow/scripts — два PySpark-джоба

**`kafka_producer.py`** — имитация реального времени.
(или исходные `*.txt`), инженерит признаки теми же оконными агрегатами, что при обучении
(`avg_customer_amount`, `customer_tx_count`, `avg_terminal_amount`, `terminal_tx_count`,
`customer_amount_ratio`), и через `confluent-kafka Producer` шлёт JSON-сообщения в топик
`fraud-transactions` с паузой `1/TPS` между отправками. Ключ сообщения — `transaction_id`
(проверка дубликатов), value — JSON.

**`fraud_streaming_inference.py`** — online-инференс. `readStream` Kafka →
`from_json(MESSAGE_SCHEMA)` → `mlflow.spark.load_model(models:/fraud-detector/Production)`
→ `foreachBatch`, пакетом через модель → `vector_to_array(probability)[1]` →
`to_json` → writeStream в топик `fraud-predictions`. Завершается по `--duration-seconds`.

*Оценка качества прямо в потоке (вариант A):* продюсер кладёт в сообщение и
**эталонную метку `tx_fraud`** (модель её на вход не берёт — она в `FEATURE_COLS`
отсутствует). Стример в каждом батче сверяет `prediction` с `tx_fraud`, копит
матрицу ошибок (TP/FP/TN/FN) и логирует в CSV и MLflow `fraud_streaming`
метрики качества: `accuracy`, `precision`, `recall`, `f1`. Вместе с метриками
быстродействия (`consumed`, `avg/max_batch_processing_ms`, `input/processed_rows_per_second`)
это даёт полную картину «качество И скорость» за один прогон
(`consumer_<run>.csv` в S3).

## Подготовка окружения

- `yc` авторизован (`yc config list`); `terraform`, `aws` CLI, `ansible-playbook`.
- Переменные окружения для aws (ключи S3-сервисного аккаунта):
  ```bash
  export AWS_ACCESS_KEY_ID=<key_id>
  export AWS_SECRET_ACCESS_KEY=<secret>
  ```

## Сборка и запуск

```bash
make init         # terraform init
make apply        # terraform apply (создаёт ВМ, S3, SA, SG и Managed Kafka + topics)
make nat          # привязать NAT route table к подсети (обязательно для Data Proc)
make outputs      # IP ВМ, ключи, URI MLflow, kafka_bootstrap_servers
make ansible-prep # hosts.ini/vars.yml уже заполнены
make provision    # только если MLflow/Postgres ещё не пронижены
make conn-extra   # Extra JSON для подключения yc-dataproc
make sync         # загрузить DAG и скрипты в S3 (DAG→airflow-dags-bucket, scripts→spark-bucket)
```

### Настройка Airflow (UI)

1. **Admin → Variables** — обязательные:
   | Переменная | Описание |
   |---|---|
   | `YC_FOLDER_ID`, `YC_SUBNET_ID`, `YC_SSH_PUBLIC_KEY` | инфраструктура Data Proc |
   | `DP_SA_ID`, `DP_SA_JSON`, `DP_SECURITY_GROUP_ID` | сервисный аккаунт/SG Data Proc |
   | `MLFLOW_TRACKING_URI` | URI MLflow-сервера |
   | `MLFLOW_AWS_ACCESS_KEY_ID` / `MLFLOW_AWS_SECRET_ACCESS_KEY` | ключи для артефактов S3 |
   | `KAFKA_BOOTSTRAP_SERVERS` | bootstrap-серверы Kafka (из `make outputs`, порт 9091) |
   | `KAFKA_PASSWORD` | пароль Kafka-пользователя `fraud-user` |
   | `KAFKA_INPUT_TOPIC` / `KAFKA_OUTPUT_TOPIC` | топики (`fraud-transactions` / `fraud-predictions`) |
   | `STREAMING_TPS` | целевой TPS продюсера (напр. `50`) |
   | `STREAMING_MESSAGES` | сколько сообщений отправить (напр. `100000`) |
   | `STREAMING_DURATION_SECONDS` | сколько секунд работает стример (напр. `300`) |
   | `YC_SAMPLE_FRACTION` | доля сэмпла перед агрегацией (напр. `0.05`) |

2. **Admin → Connections**: создать `yc-dataproc` (тип Yandex Cloud, Extra JSON из
   `make conn-extra`).
3. Включить DAG **`fraud_streaming_pipeline`** (Schedule = None), запустить вручную.

## Замер «точки перегиба» (tipping point consumer lag)

Стример и продюсер работают параллельно; продюсер шлёт фиксированное число сообщений с
целевой `STREAMING_TPS`. Прогоняем DAG при увеличении TPS и смотрим, когда консьюмер
перестаёт успевать:

```bash
make perf-test    # печатает пошаговую инструкцию и список метрик
```

1. Задать `STREAMING_TPS = 50` и запустить DAG.
2. Повторить с `STREAMING_TPS = 100, 200, 400`.
3. Сравнить метрики:
   - эксперимент **`fraud_streaming`** в MLflow (`make ui`): `consumed`,
     `input_rows_per_second` vs `processed_rows_per_second`, `avg_batch_processing_ms`,
     а также качество модели на потоке: `accuracy`, `precision`, `recall`, `f1`;
   - CSV `s3://spark-bucket-ek/kafka_test_metrics/{producer,consumer}_<run>.csv`
     (`produced` vs `consumed`, плюс `tp/fp/tn/fn` и `accuracy/f1` у consumer).
4. **Точка перегиба**: `consumed < produced` (появляется лаг) + рост
   `avg_batch_processing_ms`. Задача ДЗ — показать это значение (обычно 200–400 TPS на
   конфигурации s3-c2-m8/s3-c4-m16).
5. **Качество модели** при этом не должно меняться с ростом TPS (acc/f1 стабильны) —
   оно зависит только от champion `models:/fraud-detector/Production`, а не от скорости.
   Если качество проседает на высоком TPS — это признак lossy-нагрузки (дропаются/дублируются сообщения).

## Проверка результатов

```bash
make ui        # SSH-туннель → http://localhost:5000 (MLflow UI)
make health    # curl /health на mlflow-vm
```

В Kafka можно посмотреть смещения/число сообщений в топике через `yc kafka topic get`.

## Уничтожение

```bash
make destroy    # terraform destroy — удалит и Managed Kafka
```