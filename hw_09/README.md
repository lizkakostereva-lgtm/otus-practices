# hw_09 — REST API классификатора вредоносных URL

ML-сервис production-уровня: классификатор на scikit-learn за REST API на
FastAPI, упакованный в контейнер, выложенный в GHCR, задеплоенный в
3-узловый Managed Kubernetes в Yandex Cloud и доступный из интернета.

Модель отвечает на один вопрос: **вредоносный ли этот URL?**

```
$ curl -X POST http://<node-ip>:30080/api/v1/predict \
       -H 'Content-Type: application/json' \
       -d '{"url":"upstreams.info/wp-admin/includes/inst.exe"}'

{
  "prediction": {
    "url": "http://upstreams.info/wp-admin/includes/inst.exe",
    "label": "bad",
    "is_fraud": true,
    "probability": 0.9085,
    "threshold": 0.55,
    "model_version": "1.0.0"
  },
  "request_id": "0f3c9d1e-..."
}
```

---

## Содержание

1. [Что внутри](#что-внутри)
2. [Быстрый старт](#быстрый-старт)
3. [Справочник API](#справочник-api)
4. [Конфигурация](#конфигурация)
5. [Модель и метрики](#модель-и-метрики)
6. [Тесты](#тесты)
7. [Docker](#docker)
8. [CI/CD](#cicd)
9. [Деплой в Yandex Cloud](#деплой-в-yandex-cloud)
10. [Эксплуатация](#эксплуатация)
11. [Диагностика](#диагностика)
12. [Проектные решения](#проектные-решения)

---

## Что внутри

```
hw_09/
├── app/
│   ├── __init__.py
│   ├── config.py          # pydantic-settings, все env-переменные в одном месте
│   ├── main.py            # приложение FastAPI, роуты, middleware, lifespan
│   ├── predictor.py       # ленивый singleton с моделью + метаданные
│   ├── schemas.py         # контракт запрос/ответ + валидация URL
│   └── train.py           # скрипт обучения, пишет модель и метаданные
├── models/
│   ├── model.joblib       # обученный пайплайн (намеренно в git)
│   └── metadata.json      # параметры, метрики, threshold, sha256 артефакта
├── tests/
│   ├── conftest.py
│   ├── test_config.py
│   ├── test_main.py
│   ├── test_predictor.py
│   ├── test_schemas.py
│   ├── test_train.py
│   └── test_acceptance_live.py   # пропускаются, если не задан API_BASE_URL
├── k8s/
│   ├── 00-namespace.yaml
│   ├── 10-configmap.yaml
│   ├── 20-deployment.yaml
│   ├── 30-service-nodeport.yaml
│   ├── 40-ingress.yaml          # опционально: Load Balancer вместо NodePort
│   ├── 50-hpa.yaml
│   ├── 60-pdb.yaml
│   └── kustomization.yaml
├── terraform/
│   ├── versions.tf        # фиксация версии провайдера
│   ├── providers.tf
│   ├── variables.tf
│   ├── locals.tf
│   ├── network.tf         # VPC, общий egress NAT, 3+3 подсети, SG
│   ├── service_account.tf # сервис-аккаунты control plane и нод
│   ├── kubernetes.tf      # Managed Kubernetes + группа из 3 нод
│   ├── outputs.tf
│   └── terraform.tfvars.example
├── scripts/
│   ├── get_kubeconfig.sh
│   ├── create_image_pull_secret.sh
│   ├── deploy_k8s.sh
│   └── smoke_test.sh      # 24 сквозные проверки живого инстанса
├── .github/workflows/     # (в корне репозитория) hw_09-ci-cd.yml
├── Dockerfile             # multi-stage, не root, с healthcheck
├── docker-compose.yml     # стек только для локалки
├── Makefile               # все команды из этого документа
├── pyproject.toml         # настройки pytest + ruff
├── requirements.txt       # зафиксированные runtime-зависимости
└── requirements-dev.txt   # pytest, coverage, httpx, ruff
```

---

## Быстрый старт

Требования: Python 3.12, Docker (для контейнерного пути), CLI `yc` и
Terraform 1.5+ (только для облачного пути).

```bash
cd hw_09

# 1. Virtualenv с зафиксированными зависимостями
make install

# 2. Запустить API на http://127.0.0.1:8000
make run
```

Артефакт модели лежит в git, поэтому для первого запуска ничего не надо ни
обучать, ни скачивать. Во втором терминале:

```bash
curl http://127.0.0.1:8000/health

curl -X POST http://127.0.0.1:8000/api/v1/predict \
  -H 'Content-Type: application/json' \
  -d '{"url":"upstreams.info/wp-admin/includes/inst.exe"}'

# Открыть интерактивную документацию
open http://127.0.0.1:8000/docs
```

Эквивалентная ручная настройка, если Make не нужен:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

### Переобучение

Датасет **не** коммитится (22 МБ). Скачайте один раз, затем обучите:

```bash
make download-data          # -> data/urls.csv (в .gitignore)
make train                  # перезапишет models/model.joblib + metadata.json
make retrain-check          # то же, но падает при F1 < 0.70 или AUC < 0.90
```

Обучение на 25% выборки занимает ~8 с на 2 ядрах и полностью детерминировано
(`random_state=42`), поэтому метрики ниже воспроизводятся точно.

---

## Справочник API

| Метод | Путь                       | Назначение                                    |
| ------ | -------------------------- | --------------------------------------------- |
| GET    | `/`                        | Баннер сервиса и индекс эндпоинтов            |
| GET    | `/health`                  | Health JSON, включая `model_loaded`            |
| GET    | `/healthz`                 | Liveness, 200 как только процесс поднялся     |
| GET    | `/readyz`                  | Readiness, **503 пока модель не загружена**    |
| POST   | `/api/v1/predict`          | Классифицировать один URL                      |
| POST   | `/api/v1/predict/batch`    | Классифицировать до 100 URL                    |
| GET    | `/api/v1/model/info`       | Версия модели, параметры, метрики, threshold   |
| GET    | `/metrics`                 | Метрики Prometheus                             |
| GET    | `/docs`, `/redoc`          | Swagger UI / ReDoc                             |
| GET    | `/openapi.json`            | Схема OpenAPI                                  |

Каждый ответ содержит заголовок `X-Request-ID`; тот же id возвращается в поле
`request_id` ответов предсказания.

### POST /api/v1/predict

Запрос:

```jsonc
{
  "url": "upstreams.info/wp-admin/includes/inst.exe",  // обязательно
  "threshold": 0.55                                    // опционально, 0.0–1.0
}
```

Ответ:

```jsonc
{
  "prediction": {
    "url": "http://upstreams.info/wp-admin/includes/inst.exe",
    "label": "bad",          // сырой класс модели
    "is_fraud": true,        // probability >= threshold
    "probability": 0.9085,
    "threshold": 0.55,
    "model_version": "1.0.0"
  },
  "request_id": "0f3c9d1e-6f2a-4a1e-9c8a-2b1f6f0d9a11"
}
```

**Нормализация URL.** В обучающей выборке лежат голые доменные имена, поэтому
отсутствие схемы — не ошибка: `docs.python.org` превращается в
`http://docs.python.org`. Схемы кроме `http`/`https` отклоняются с 422, как и
пустой хост, хост с пробелами и всё, что длиннее `MAX_URL_LENGTH`.
`user:pass@` отбрасывается до скоринга, потому что классификатор смотрит
только на authority и путь.

### POST /api/v1/predict/batch

```bash
curl -X POST http://127.0.0.1:8000/api/v1/predict/batch \
  -H 'Content-Type: application/json' \
  -d '{"urls":["upstreams.info/wp-admin/includes/inst.exe","github.com/faizann24"]}'
```

```jsonc
{
  "predictions": [ /* по объекту на вход, та же форма что выше */ ],
  "count": 2,
  "request_id": "…"
}
```

Батч ограничен `MAX_BATCH_SIZE` (100) записями. Весь батч отклоняется, если
хоть один URL невалиден, поэтому в ответе никогда не смешиваются успехи и
ошибки.

### Ошибки

Ответы с кодом не 2xx имеют единое тело:

```jsonc
{
  "error": "validation_error",
  "detail": "url scheme must be http or https",
  "request_id": "…"
}
```

| Код   | Когда                                                             |
| ----- | ----------------------------------------------------------------- |
| 422   | Некорректный URL, батч слишком большой, threshold вне 0–1         |
| 404   | Неизвестный путь                                                  |
| 500   | Непредвиденная серверная ошибка; `request_id` попадёт в логи      |
| 503   | Только `/readyz`, пока модель ещё грузится                        |

---

## Конфигурация

Все настройки — переменные окружения. Значения по умолчанию в
`app/config.py`, боевые — в `k8s/10-configmap.yaml`.

| Переменная             | По умолчанию              | Назначение                                          |
| ---------------------- | ------------------------- | --------------------------------------------------- |
| `LOG_LEVEL`            | `INFO`                    | Уровень логов корневого логгера                     |
| `ENVIRONMENT`          | `production`              | Метка окружения                                      |
| `SERVICE_NAME`         | `url-fraud-api`           | Возвращается в `/` и `/health`                       |
| `ENABLE_METRICS`       | `true`                    | Отдавать ли `/metrics`                               |
| `TRAIN_ON_STARTUP`     | `false`                   | Обучать, если артефакта нет (удобство для локалки)   |
| `MODEL_PATH`           | `models/model.joblib`     | Путь к пайплайну                                     |
| `MODEL_METADATA_PATH`  | `models/metadata.json`    | Путь к метаданным                                    |
| `DEFAULT_THRESHOLD`    | *(пусто)*                 | Переопределяет threshold из метаданных, если задан   |
| `MAX_BATCH_SIZE`       | `100`                     | Максимум URL в батче                                 |
| `MAX_URL_LENGTH`       | `2048`                    | Максимальная длина нормализованного URL              |
| `CORS_ORIGINS`         | `*`                       | Origins через запятую, либо `*`                      |
| `RANDOM_SEED`          | `42`                      | Сид для всего, что случайно                          |

**Приоритет threshold.** Берётся threshold из `models/metadata.json`
(`0.55`, выбран максимизацией F1 на отложенной выборке), если не задан
`DEFAULT_THRESHOLD`. `threshold` в конкретном запросе приоритетнее обоих.
Так разумный дефолт не требует редеплоя для тюнинга.

---

## Модель и метрики

```
CountVectorizer(analyzer="char", ngram_range=(1, 3), min_df=2)
  -> RandomForestClassifier(n_estimators=120, max_depth=16,
                            min_samples_leaf=2, class_weight="balanced")
```

Символьные n-граммы, а не слова-токены: вредоносные URL — это в основном
гомоглифы, странные TLD и мусор в пути (`wp-admin/includes/inst.exe`), и
линейная модель по char 1–3-граммам ловит этот сигнал вообще без
предобработки.

Обучено на 25% выборки
[faizann24/Using-machine-learning-to-detect-malicious-URLs](https://github.com/faizann24/Using-machine-learning-to-detect-malicious-URLs)
(420 464 строки, метки `bad`/`good`), разбиение 80/20 со стратификацией:

| Метрика    | Значение |
| ---------- | -------- |
| Accuracy   | 0.9233   |
| Precision  | 0.7962   |
| Recall     | 0.7078   |
| F1         | 0.7494   |
| ROC AUC    | 0.9473   |
| Threshold  | 0.55     |
| Признаки   | 55 392   |

82 249 train / 20 563 test строк, 16.2% положительных. Следить нужно за AUC:
качество ранжирования высокое, а threshold — это как раз тот рычаг, который
меняет precision на recall.

Профиль ошибок — самое интересное, и он измерен, а не прикинут. На 20 000
легитимных URL из отложенной выборки:

| Threshold | Доля ложных срабатываний на легитимных URL |
| --------- | ------------------------------------------ |
| 0.55      | 8.0%                                       |
| 0.50      | 15.2%                                      |
| 0.40      | 59.4%                                      |

На деплоенном threshold получается 70.8% recall при 8% ложных срабатываний —
разумно для очереди на ручной разбор. Обрыв между 0.50 и 0.40 — вот о чём
стоит помнить: при 0.50 помечается примерно половина всех легитимных URL,
потому что модель держит много массы чуть выше 0.5. Снижайте threshold, только
если понимаете, чем за это платите.

У той же модели есть забавный известный случай провала:
`docs.python.org` набирает **0.6392** и потому помечается как фрод при
дефолтном threshold. Короткие чистые хосты с непривычными TLD выглядят для
char-n-gram модели на данных 2019 года как фишинг. `github.com/faizann24`
набирает 0.4984 и проходит впритык.

`GET /api/v1/model/info` возвращает всё это в рантайме, а также SHA-256
конкретного `model.joblib`, который отдаётся, — так можно доказать, какой
артефакт крутится в поде:

```bash
curl -s http://127.0.0.1:8000/api/v1/model/info | python3 -m json.tool
```

---

## Тесты

```bash
make test          # 57 passed, 9 skipped
make coverage      # HTML-отчёт в htmlcov/index.html
make lint          # ruff check + format --check
make check         # и то и другое
```

Набор герметичный: в фикстуре собирается маленький синтетический пайплайн,
поэтому тестам не нужны ни сеть, ни датасет, и они проходят меньше чем за
секунду.

9 пропущенных тестов — это live-набор приёмки. Он включается, когда задан
`API_BASE_URL`:

```bash
make acceptance API_BASE_URL=http://<node-ip>:30080
```

Для сквозной проверки без зависимостей (health, оба класса предсказания,
нормализация URL, батч, валидация, docs, метрики — 24 ассерта):

```bash
make smoke API_BASE_URL=http://<node-ip>:30080
```

---

## Docker

```bash
make build          # docker build
make run-docker     # запуск на http://127.0.0.1:8000
make smoke-local    # 24 проверки контейнера
```

Или через compose:

```bash
make compose-up
curl http://127.0.0.1:8000/health
make compose-down
```

Устройство образа:

- **Multi-stage.** Зависимости собираются в `/opt/venv` на стадии билдера и
  копируются в чистую рантайм-стадию; тулчейн компиляции в образ не едет.
- **Не root.** Работает под uid/gid `10001`.
- **Зафиксированные зависимости.** `requirements.txt` на `==`, поэтому образ
  воспроизводим.
- **Артефакт внутри.** `models/model.joblib` лежит в git и копируется в образ,
  поэтому pull не требует ни PyPI, ни датасета.
- **Python 3.12** — тот же интерпретатор, который создал pickle.
- **Один воркер на контейнер.** Параллелизм берётся репликами Deployment, а не
  `--workers` — потоки внутри одного процесса дали бы только конкуренцию за GIL
  и продублировали бы модель на 3 МБ в памяти.
- **Healthcheck** на `/readyz`, повторяет k8s-пробы.

Образ собирается под `linux/amd64` (платформа нод Yandex Cloud). На Apple
Silicon для локальных запусков можно переопределить:

```bash
make build PLATFORM=linux/arm64
```

### Пуш в GHCR

Владелец в GHCR берётся из remote `origin`, так что переменные не нужны:

```bash
make -n push          # покажет ghcr.io/lizkakostereva-lgtm/url-fraud-api:<sha>
make login-registry   # docker login ghcr.io, читает GITHUB_USER / GHCR_TOKEN
make push
```

Переопределить при необходимости: `make push IMAGE_TAG=1.0.0
GITHUB_OWNER=my-org`.

---

## CI/CD

`.github/workflows/hw_09-ci-cd.yml` (в корне репозитория) состоит из пяти jobs:

```
lint ─┐
      ├─> build ──> push ──> deploy
test ─┘
```

| Job      | Триггер                                              | Что делает                                                                   |
| -------- | ---------------------------------------------------- | ---------------------------------------------------------------------------- |
| `lint`   | PR, push в `main`, тег, ручной запуск                | `ruff check` + `ruff format --check`                                         |
| `test`   | то же                                                | `pytest` с coverage, затем ре-обучение на малой выборке как защита от регрессий |
| `build`  | после обоих                                          | `docker buildx` build, затем запуск образа и вызов API внутри него            |
| `push`   | `main`, теги `v*` или ручной запуск с `push_image`  | Сборка и пуш в GHCR с тегами `branch`, `sha`, `latest` и semver               |
| `deploy` | теги `v*` или ручной запуск с `deploy=true`          | Применение k8s-манифестов, ожидание rollout, прогон smoke-теста               |

`build` намеренно поднимает контейнер и дёргает `/api/v1/predict` до того, как
что-то попадёт в реестр, чтобы сломанный образ туда не доехал.

Пайплайн срабатывает только на PR, push в `main`, тегах `v*` и ручном запуске —
**просто push в feature-ветку его не запустит**.

### Что нужно настроить в GitHub

Всё это ставится одной командой вместо ручного заполнения Settings:

```bash
brew install gh && gh auth login     # один раз
make gh-setup                        # или ./scripts/setup_github.sh
```

Скрипт идемпотентен (каждый шаг перезаписывает своё значение), так что чинить
устаревший секрет — та же команда. Что он выставляет:

Repository **Settings → Secrets and variables → Actions**:

| Имя                | Тип     | Зачем                                                  |
| ------------------ | ------- | ------------------------------------------------------ |
| `KUBECONFIG`       | secret  | `deploy` — base64 kubeconfig кластера                  |
| `KUBE_CONTEXT`     | var     | Имя контекста, по умолчанию `url-fraud-cluster`        |
| `IMAGE_PULL_SECRET`| var     | Имя существующего pull-секрета, если пакет приватный   |

Полезные флаги:

```bash
./scripts/setup_github.sh --no-kubeconfig              # только CI, кластера ещё нет
./scripts/setup_github.sh --pull-secret ghcr-pull       # приватный GHCR-пакет
```

`--pull-secret` дополнительно создаёт сам секрет в кластере и требует
`GHCR_TOKEN` (PAT с `read:packages`) и `GITHUB_USER`. Без этого флага при
приватном пакете деплой уйдёт в `ImagePullBackOff` — job теперь проверяет
наличие секрета и падает сразу с понятной ошибкой.

> **Кнопка «Run workflow» появится только после merge в `main`.** GitHub
> показывает ручной запуск лишь для workflow, который лежит в ветке по
> умолчанию. Это и есть то, что делает пайплайн запускаемым одной кнопкой:
> https://github.com/lizkakostereva-lgtm/otus-practices/pull/new/hw_09

PAT не нужен: `push` и `deploy` используют автоматический `GITHUB_TOKEN`,
который уже имеет `packages: write` для этого репозитория. Локальный
`GHCR_TOKEN` требуется только для `make login-registry` на своей машине.

Создать base64-секрет с kubeconfig вручную:

```bash
base64 < ~/.kube/config-url-fraud-cluster | tr -d '\n'   # macOS и Linux одинаково
```

Job `deploy` закрыт environment `production` — добавьте туда обязательного
ревьюера, если нужен человек в контуре.

> При ручном запуске `deploy` требует `push`: он берёт образ из
> `needs.push.outputs.image`, поэтому запуск только с `deploy=true` пропускается
> по замыслу. Дефолты обоих флагов — `true`, так что кнопка «Run workflow» без
> галочек прогоняет всю цепочку: lint → test → build → push → deploy → smoke.
> Для CI-only прогона снимите галочку `deploy`.

> В языке выражений GitHub **нет** тернарного оператора `? :`. Условия вида
> `event == 'workflow_dispatch' ? inputs.x : ...` делают невалидным весь файл
> workflow: GitHub создаёт «run» с именем-путем файла и нулём jobs. Проверяйте
> локально: `actionlint .github/workflows/hw_09-ci-cd.yml`.

---

## Деплой в Yandex Cloud

Кластер создаётся Terraform, приложение деплоится обычными манифестами.
**Это создаёт платные ресурсы** — команда destroy в конце их удалит.

### Что создаётся

```
VPC  url-fraud-net
├── shared egress gateway  url-fraud-nat      ← у сети default нет route table
├── route table            0.0.0.0/0 → gateway
├── 3 node subnets         10.130/10.131/10.132.0.0/24  (a/b/d)
└── 3 master subnets       10.140/10.141/10.142.0.0/24  (a/b/d)

Managed Kubernetes  url-fraud-cluster        региональный, 3 master, k8s 1.33
└── node group      url-fraud-workers       fixed_scale = 3, по одной на зону
    ├── security group  SSH(22), kubelet, self, egress
    └── NAT на каждой ноде                 ← обязательно для NodePort-эндпоинта
```

Выделенная сеть вместо общей `default`, потому что у тех подсетей нет route
table: ноды остались бы без egress и не смогли бы стянуть образ из GHCR.

### Шаг 1 — аутентификация

```bash
yc init     # интерактивно; либо: export YC_TOKEN=$(yc iam create-token)
yc version
```

> `yc config list` печатает ваш IAM-токен открытым текстом — удобно, чтобы
> посмотреть ID облака и каталога, но не вставляйте этот вывод в чат или задачу.

### Шаг 2 — настройка Terraform

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
```

Правьте файл или передавайте значения инлайном:

```bash
# ID облака и каталога (в этом CLI нет верхнеуровневых `yc cloud`/`yc zone`)
yc resource-manager cloud list
yc resource-manager folder list

#compute-зоны и доступные версии Kubernetes
yc compute zone list
yc managed-kubernetes list-versions
```

В `terraform.tfvars.example` уже проставлены ID облака и каталога этого
аккаунта, так что достаточно `cp` + `make tf-init`. Перед apply убедитесь, что
в списке зон есть `ru-central1-a`, `-b` и `-c`.

Оставьте `token = ""`, чтобы переиспользовать профиль `yc` CLI — это
рекомендуется, тогда секрет вообще не попадает в файлы. `terraform.tfvars` в
`.gitignore`.

### Шаг 3 — plan, затем apply

```bash
make tf-init      # terraform init
make tf-plan      # внимательно прочитать план
make tf-apply     # ~10-15 минут
```

После этого Terraform печатает нужные выходы:

```bash
cd terraform && terraform output          # api_endpoint, node_public_ips, ...
cd ..
```

Нодам нужно 2–5 минут, чтобы стать `READY`:

```bash
kubectl --context url-fraud-cluster get nodes -w
```

### Шаг 4 — получить kubeconfig

```bash
make kubeconfig
kubectl --context url-fraud-cluster get nodes
```

Скрипт использует `yc managed-kubernetes cluster get-credentials` (команды
`get-kubeconfig` в этом CLI нет), пишет `~/.kube/config-url-fraud-cluster`,
мержит контекст в активный kubeconfig — с учётом `$KUBECONFIG`, если он задан
— и затем проверяет, что обычный `kubectl` действительно видит контекст.
Извне VPC запрашивается **external** эндпоинт; если kubectl запускается
внутри VPC, используйте `ENDPOINT_MODE=internal`.

### Шаг 5 — запушить образ

```bash
export GHCR_USER=<your-github-login>
export GHCR_TOKEN=<PAT with write:packages>

make push          # ghcr.io/<your-github-login>/url-fraud-api:<sha>

# только если пакет GHCR приватный
export GITHUB_USER=<your-github-login>
make pull-secret      # создаёт секрет ghcr-pull
```

### Шаг 6 — задеплоить приложение

```bash
make deploy IMAGE_TAG=1.0.0
make status
```

Или вручную:

```bash
kubectl --context url-fraud-cluster apply -f k8s/00-namespace.yaml
kubectl --context url-fraud-cluster apply -f k8s/10-configmap.yaml
kubectl --context url-fraud-cluster apply -f k8s/20-deployment.yaml
kubectl --context url-fraud-cluster -n url-fraud \
  set image deployment/url-fraud-api api=ghcr.io/<you>/url-fraud-api:1.0.0
kubectl --context url-fraud-cluster apply -f k8s/30-service-nodeport.yaml
kubectl --context url-fraud-cluster -n url-fraud rollout status deployment/url-fraud-api
```

С приватным образом добавьте `--with-pull-secret ghcr-pull`.

### Шаг 7 — дёрнуть публичное API

Service — это `NodePort` на `30080`, поэтому подойдёт публичный IP любой ноды:

```bash
NODE_IP=$(kubectl --context url-fraud-cluster get nodes -o \
  jsonpath='{.items[0].status.addresses[?(@.type=="ExternalIP")].address}')

curl "http://${NODE_IP}:30080/health"

curl -X POST "http://${NODE_IP}:30080/api/v1/predict" \
  -H 'Content-Type: application/json' \
  -d '{"url":"upstreams.info/wp-admin/includes/inst.exe"}'

make smoke API_BASE_URL="http://${NODE_IP}:30080"
```
