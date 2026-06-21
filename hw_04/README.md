# Домашнее задание №4. Feast Feature Store

## Выполненные изменения

В файле `feature_store/feature_repo/example_repo.py` были добавлены две новые Feature View и одна On-Demand Feature View.

### 1. Feature View: driver_quality_fv

Создано представление признаков, содержащее показатели качества работы водителя:

* `conv_rate` — коэффициент конверсии заказов;
* `acc_rate` — коэффициент принятия заказов.

Назначение Feature View — предоставление признаков, характеризующих качество работы водителя для обучения моделей и online-инференса.

### 2. Feature View: driver_activity_fv

Создано представление признаков, содержащее показатели активности водителя:

* `avg_daily_trips` — среднее количество поездок в день.

Назначение Feature View — предоставление признаков, характеризующих интенсивность работы водителя.

### 3. On-Demand Feature View: driver_realtime_metrics

Создано представление признаков, вычисляемых в режиме реального времени на основе данных из `driver_quality_fv` и `driver_activity_fv`.

Вычисляемые признаки:

* `acceptance_gap` = `acc_rate - conv_rate`
* `realtime_efficiency` = `avg_daily_trips * acc_rate`

Данные признаки не хранятся в источнике данных и вычисляются во время выполнения запроса.

---

## Инициализация Feature Store

Перейти в каталог репозитория:

```bash
cd feature_store/feature_repo
```

Применить изменения в реестре Feast:

```bash
feast apply
```

Проверить зарегистрированные Feature View:

```python
from feast import FeatureStore

store = FeatureStore(repo_path="feature_store/feature_repo")

store.list_feature_views()
```

---

## Описание ноутбука

Файл: `feast_homework_notebook.ipynb`

Ноутбук содержит примеры работы с созданными Feature View и On-Demand Feature View.

### Получение исторических признаков

Используется метод:

```python
store.get_historical_features(...)
```

На основе набора сущностей (`entity_df`) формируется обучающий датасет, содержащий:

* `driver_quality_fv:conv_rate`
* `driver_quality_fv:acc_rate`
* `driver_activity_fv:avg_daily_trips`
* `driver_realtime_metrics:acceptance_gap`
* `driver_realtime_metrics:realtime_efficiency`

Полученные данные могут использоваться для обучения моделей машинного обучения.

### Получение признаков для online-инференса

Используется метод:

```python
store.get_online_features(...)
```

Для указанного `driver_id` выполняется получение признаков из online store и вычисление признаков из On-Demand Feature View в режиме реального времени.

Результат представляет собой набор признаков, готовый для передачи в модель во время инференса.
