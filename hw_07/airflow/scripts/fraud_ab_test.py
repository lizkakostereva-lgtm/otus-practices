#!/usr/bin/env python3
"""Spark job: A/B validation of the retrained model vs the current Production model.

=== Стратегия валидации (offline A/B на holdout) ===

Сравниваем двух «пациентов»:
  * champion  — текущая модель в стадии Production (загружается из MLflow Registry);
  * candidate — только что переобученная модель (загружается из того же Registry).

Обе модели применяются к ОДНОМУ holdout (test) срезу данных (парный дизайн), поэтому
разница метрик не «зашумлена» случайным разбросом выборок. Метрики считаются по
bootstrap-репликам holdout, что даёт:

  * выборочное распределение каждой метрики для каждой модели (не только точечную оценку);
  * доверительные интервалы (по умолчанию 95 %);
  * p-value и размер эффекта (Cohen's d) для проверки, является ли улучшение
    статистически значимым, а не флуктуацией на случайной выборке.

Решение о выкате принимается по главной метрике F1: candidate выкатывается в Production,
только если улучшение статистически значимо (p < alpha) и практически значимо
(Cohen's d >= effect_threshold). Чтобы не уронить качество по «страховочной» метрике,
дополнительно требуется, чтобы PR-AUC не деградировал значимо.

Метрики A/B теста фиксируются в MLflow (эксперимент fraud_ab_testing), решение
пишется в S3 (Object Storage) и подхватывается шагом Airflow apply_ab_decision,
который выполняет переход версии в Production.

Artifacts stored in S3 (Object Storage).
"""

import argparse
import json
import os
import sys

import numpy as np

from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.window import Window
from pyspark.storagelevel import StorageLevel


def normalize_dataproc_args(argv):
    """Yandex Data Proc passes all PySpark job args as a single comma-joined token."""
    if len(argv) == 1 and argv[0].startswith("--") and "," in argv[0]:
        return argv[0].split(",")
    return argv


def parse_args(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    argv = normalize_dataproc_args(argv)
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="s3a://spark-bucket-ek/fraud_clean_parquet",
                        help="Path to cleaned Parquet dataset")
    parser.add_argument("--mlflow-tracking-uri", required=True,
                        help="MLflow Tracking Server URI")
    parser.add_argument("--mlflow-s3-endpoint", default="https://storage.yandexcloud.net")
    parser.add_argument("--aws-access-key-id", default=None)
    parser.add_argument("--aws-secret-access-key", default=None)
    parser.add_argument("--experiment-name", default="fraud_ab_testing",
                        help="MLflow experiment where A/B metrics are logged")
    parser.add_argument("--train-experiment-name", default="fraud_detection",
                        help="Experiment where candidate models are trained")
    parser.add_argument("--model-name", default="fraud-detector")
    parser.add_argument("--bootstrap-iterations", type=int, default=200,
                        help="Number of bootstrap resamples (metric distribution)")
    parser.add_argument("--alpha", type=float, default=0.01,
                        help="Significance level for statistical tests")
    parser.add_argument("--effect-threshold", type=float, default=0.2,
                        help="Minimal Cohen's d to consider practical significance")
    parser.add_argument("--sample-fraction", type=float, default=0.1,
                        help="Fraction of dataset to use for the test set")
    parser.add_argument("--decision-output",
                        default="s3a://spark-bucket-ek/ab_validation",
                        help="S3 path to write the deployment decision JSON")
    parser.add_argument("--metrics-output",
                        default="s3a://spark-bucket-ek/ab_metrics",
                        help="S3 path to append A/B metrics (CSV)")
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args(argv)


MLFLOW_VERSION = "2.16.2"


def ensure_mlflow():
    """Make sure the pinned mlflow is importable on the driver.

    The cluster is created with ONE consistent ML stack pinned into the base
    conda env (see the DAG's `pip:*` properties): numpy 1.24.4, pandas 2.0.3,
    scikit-learn 1.3.2, scipy 1.10.1 and mlflow 2.16.2 - all Python-3.8
    compatible and mutually consistent. That single stack is used by pyspark's
    MLlib (which imports numpy), sklearn, scipy and mlflow alike, so there is
    exactly ONE numpy in the process.

    We deliberately do NOT pip-install a second mlflow/numpy/pandas into /tmp
    here: doing so previously loaded a SECOND numpy (base 1.20.1 + /tmp 1.26.4)
    into the same process and crashed the A/B bootstrap with
    "RecursionError: maximum recursion depth exceeded" (the two numpy module
    trees calling into each other via np.quantile/np.linspace/np.any).

    We only re-expose two mlflow.models attributes that mlflow 2.16.2 wraps in
    try/except ImportError (silently leaving them unbound) and verify the
    version actually loaded matches the Tracking Server.
    """
    import mlflow
    import mlflow.models as _models

    for _name in ("ModelInputExample", "ModelSignature"):
        if not hasattr(_models, _name):
            try:
                setattr(_models, _name, _models.Model)
                print(f"Shimmed mlflow.models.{_name} (was swallowed)", flush=True)
            except Exception as _exc:  # pragma: no cover
                print(f"Could not shim mlflow.models.{_name}: {_exc}", flush=True)

    import mlflow.spark  # noqa: F401
    path = os.path.abspath(mlflow.__file__)
    print(f"Using mlflow {mlflow.__version__} from {path}", flush=True)
    if mlflow.__version__ != MLFLOW_VERSION:
        raise RuntimeError(f"Expected mlflow {MLFLOW_VERSION}, got "
                           f"{mlflow.__version__} loaded from {path}")


# ------------------------------ Метрики качества ------------------------------
def binary_metrics_array(y, p):
    """Бинарные метрики по numpy-векторам (y - метки, p - вероятность класса 1).

    Возвращает dict с ключами precision, recall, f1, auc, pr_auc (и best_thr).

    Векторизованная версия: ВСЕ метрики вычисляются из ОДНОЙ сортировки по
    вероятностям + кумулятивных сумм (O(n log n)), вместо прежнего подхода с
    внутренним циклом по ~101 порогу (O(n * thresholds)) и повторными вызовами
    sklearn. Это даёт порядковый прирост скорости на больших holdout-выборках:
    на 2.35M строк 200 bootstrap-итераций выполняются за ~3.5 мин вместо долгих
    часов / фактического зависания на драйвере.
    """
    y = np.asarray(y, dtype=bool)
    p = np.asarray(p, dtype=float)

    pos = int(y.sum())
    neg = int((~y).sum())

    # Вырожденный случай: одна метка на всём срезе
    if pos == 0 or neg == 0:
        return {
            "precision": 0.0, "recall": 0.0, "f1": 0.0,
            "auc": 0.0, "pr_auc": 0.0, "best_thr": 0.5,
        }

    # AUC по ранговому тождеству (с усреднением рангов при совпадении вероятностей).
    order = np.argsort(p, kind="stable")
    ps = p[order]
    is_new = np.ones(len(p), dtype=bool)
    is_new[1:] = ps[1:] != ps[:-1]
    group = np.cumsum(is_new) - 1
    grp_first = np.flatnonzero(is_new)
    grp_len = np.diff(np.r_[grp_first, len(p)])
    grp_sum = (grp_first + 1) * grp_len + grp_len * (grp_len - 1) / 2.0
    avg_rank = grp_sum / grp_len
    ranks = np.empty(len(p))
    ranks[order] = avg_rank[group]
    sum_rank_pos = float(ranks[y].sum())
    auc = float(np.clip((sum_rank_pos - pos * (pos + 1) / 2.0)
                        / (float(pos) * float(neg)), 0.0, 1.0))

    # PR-AUC и кривая F1 из одного прохода по отсортированным (desc) вероятностям.
    desc = np.argsort(-p, kind="stable")
    ys = y[desc]
    tp = np.cumsum(ys)
    fp = np.cumsum(~ys)
    recall = tp / pos
    precision = np.where((tp + fp) == 0, 0.0, tp / (tp + fp).astype(float))
    pr_auc = float((np.diff(np.r_[0.0, recall]) * precision).sum())

    f1curve = np.where((precision + recall) == 0, 0.0,
                       2 * precision * recall / (precision + recall))
    best_k = int(np.argmax(f1curve))
    return {
        "precision": float(precision[best_k]),
        "recall": float(recall[best_k]),
        "f1": float(f1curve[best_k]),
        "auc": auc,
        "pr_auc": pr_auc,
        "best_thr": float(p[desc[best_k]]),
    }


def bootstrap_scores(y, p, n_iter, seed, metrics=("precision", "recall", "f1", "auc", "pr_auc")):
    """Bootstrap-распределение метрик по ресемплированию holdout с возвращением.

    Возвращает dict: metric -> np.array[n_iter] (распределение метрики).
    """
    rng = np.random.default_rng(seed)
    y = np.asarray(y, dtype=int)
    p = np.asarray(p, dtype=float)
    n = len(y)
    scores = {m: np.empty(n_iter) for m in metrics}
    idx = np.arange(n)

    classified = 0
    for i in range(n_iter):
        sample = rng.choice(idx, size=n, replace=True)
        m = binary_metrics_array(y[sample], p[sample])
        if m["auc"] > 0:
            classified += 1
        for met in metrics:
            scores[met][i] = m[met]

    if classified == 0:
        raise RuntimeError("Bootstrap: ни одна реплика не содержала обоих классов - "
                           "holdout слишком дисбалансирован/мал.")
    return scores


def statistical_comparison(base, cand, alpha, effect_threshold,
                           metrics=("precision", "recall", "f1", "auc", "pr_auc")):
    """Сравнение распределений метрик (champion=base, candidate=cand).

    Для каждой метрики - t-тест Стьюдента (сравнение bootstrap-распределений),
    размер эффекта Cohen's d, доверительные интервалы, решение о значимости.
    """
    from scipy.stats import ttest_ind

    results = {}
    for met in metrics:
        b = base[met]
        c = cand[met]
        t_stat, p_value = ttest_ind(b, c, equal_var=False)
        diff = float(c.mean() - b.mean())

        # Cohen's d с объединённым среднеквадратичным отклонением
        pooled_std = float(np.sqrt((b.var() + c.var()) / 2.0))
        effect_size = abs(diff) / pooled_std if pooled_std > 0 else 0.0

        # 95 % доверительные интервалы
        b_lo, b_hi = np.percentile(b, [2.5, 97.5])
        c_lo, c_hi = np.percentile(c, [2.5, 97.5])

        is_significant = bool(p_value < alpha)
        practically_significant = bool(effect_size >= effect_threshold)

        results[met] = {
            "champion_mean": float(b.mean()),
            "candidate_mean": float(c.mean()),
            "champion_ci": [float(b_lo), float(b_hi)],
            "candidate_ci": [float(c_lo), float(c_hi)],
            "difference": diff,
            "p_value": float(p_value),
            "t_statistic": float(t_stat),
            "effect_size": effect_size,
            "is_significant": is_significant,
            "practically_significant": practically_significant,
        }
    return results


def main():
    args = parse_args()

    os.environ["MLFLOW_TRACKING_URI"] = args.mlflow_tracking_uri
    os.environ["MLFLOW_S3_ENDPOINT_URL"] = args.mlflow_s3_endpoint
    os.environ["AWS_DEFAULT_REGION"] = "ru-central1"
    if args.aws_access_key_id:
        os.environ["AWS_ACCESS_KEY_ID"] = args.aws_access_key_id
        os.environ["AWS_SECRET_ACCESS_KEY"] = args.aws_secret_access_key
    else:
        os.environ["MLFLOW_ENABLE_ARTIFACT_PROXY"] = "true"

    ensure_mlflow()
    import mlflow
    from mlflow.tracking import MlflowClient
    from mlflow.utils import mlflow_tags

    client = MlflowClient()

    spark = SparkSession.builder \
        .appName("FraudABTest") \
        .config("spark.sql.adaptive.enabled", "true") \
        .getOrCreate()

    spark._jsc.hadoopConfiguration().set("fs.s3a.endpoint", "storage.yandexcloud.net")
    spark._jsc.hadoopConfiguration().set("fs.s3a.path.style.access", "true")
    spark._jsc.hadoopConfiguration().set("fs.s3a.connection.ssl.enabled", "true")

    # ------------------ Данные (общая holdout-выборка) ----------------------
    df = spark.read.parquet(args.input)
    if 0.0 < args.sample_fraction < 1.0:
        df = df.sample(withReplacement=False, fraction=args.sample_fraction, seed=args.seed)
    print(f"Loaded {df.count()} rows", flush=True)

    cust_w = Window.partitionBy("customer_id")
    term_w = Window.partitionBy("terminal_id")
    feat = (
        df
        .withColumn("avg_customer_amount", F.avg("tx_amount").over(cust_w))
        .withColumn("customer_tx_count", F.count("tx_amount").over(cust_w))
        .withColumn("avg_terminal_amount", F.avg("tx_amount").over(term_w))
        .withColumn("terminal_tx_count", F.count("tx_amount").over(term_w))
        .withColumn(
            "customer_amount_ratio",
            F.col("tx_amount") / F.when(F.col("avg_customer_amount") <= 0, 1.0)
                .otherwise(F.col("avg_customer_amount")),
        )
    )
    # Семплирование ВЫПОЛНЯЕТСЯ ДО feature engineering, но разбиение на train/test -
    # здесь. Используем тот же randomSplit-срез (seed), что и при обучении, чтобы
    # holdout совпадал с тем, на котором считался кандидат.
    train, test = feat.randomSplit([0.8, 0.2], seed=args.seed)
    print(f"Holdout (test) rows: {test.count()}", flush=True)
    test.persist(StorageLevel.MEMORY_AND_DISK)

    # ------------------ Модели: champion + candidate ------------------------
    # Candidate = самая свежая версия (последний зарегистрированный run candidate).
    # Champion   = версия в стадии Production (если есть).

    versions = client.get_latest_versions(args.model_name, stages=["None", "Production"])
    prod_version = next((v for v in versions if v.current_stage == "Production"), None)
    # Candidate: самая свежая версия (по created timestamp)
    candidate_version = max(versions, key=lambda v: v.creation_timestamp) if versions else None

    if candidate_version is None:
        print("Нет кандидата для валидации - пропускаем A/B (ничего не обучено).", flush=True)
        spark.stop()
        return

    champion_model = None
    if prod_version is not None:
        print(f"Champion: version {prod_version.version} (stage=Production)", flush=True)
        champion_model = mlflow.spark.load_model(
            f"models:/{args.model_name}/{prod_version.version}")
    else:
        print("Production model not found - first run, A/B skipped, candidate will seed.", flush=True)

    print(f"Candidate: version {candidate_version.version} (stage={candidate_version.current_stage})",
          flush=True)
    candidate_model = mlflow.spark.load_model(
        f"models:/{args.model_name}/{candidate_version.version}")

    # ------------------ Предсказания обеих моделей на holdout ---------------
    # Собираем (label, p_champion, p_candidate) на драйвер - стандартный приём для
    # bootstrap-агрегации (модели и фичи считаются в Spark, агрегация - в numpy).
    cand_pred = candidate_model.transform(test)
    cand_rows = cand_pred.select("tx_fraud", "probability").rdd.map(
        lambda r: (int(r["tx_fraud"]), float(r["probability"][1]))).collect()
    y = np.array([r[0] for r in cand_rows])
    p_candidate = np.array([r[1] for r in cand_rows])
    print(f"Collected {len(y)} holdout predictions for candidate", flush=True)

    if champion_model is not None:
        ch_pred = champion_model.transform(test)
        ch_rows = ch_pred.select("tx_fraud", "probability").rdd.map(
            lambda r: (int(r["tx_fraud"]), float(r["probability"][1]))).collect()
        p_champion = np.array([r[1] for r in ch_rows])
    else:
        p_champion = None

    # ------------------ Bootstrap распределения ------------------------------
    print(f"Running bootstrap ({args.bootstrap_iterations} iterations)...", flush=True)
    cand_dist = bootstrap_scores(y, p_candidate, args.bootstrap_iterations,
                                 seed=args.seed, )
    if p_champion is not None:
        champ_dist = bootstrap_scores(y, p_champion, args.bootstrap_iterations,
                                      seed=args.seed + 1)
    else:
        champ_dist = None

    metrics_names = ("precision", "recall", "f1", "auc", "pr_auc")

    # ------------------ Статистическое сравнение ----------------------------
    if champ_dist is not None:
        comparison = statistical_comparison(champ_dist, cand_dist,
                                            args.alpha, args.effect_threshold,
                                            metrics=metrics_names)
    else:
        comparison = None

    # ------------------ Логирование A/B метрик в MLflow ----------------------
    mlflow.set_experiment(args.experiment_name)
    with mlflow.start_run() as ab_run:
        ab_run_id = ab_run.info.run_id
        mlflow.log_params({
            "model_name": args.model_name,
            "champion_version": prod_version.version if prod_version else "none",
            "candidate_version": candidate_version.version,
            "bootstrap_iterations": args.bootstrap_iterations,
            "alpha": args.alpha,
            "effect_threshold": args.effect_threshold,
            "holdout_rows": int(len(y)),
            "candidate_run_id": candidate_version.run_id or "",
        })
        if champion_model is not None:
            mlflow.set_tag("champion_version", prod_version.version)
        mlflow.set_tag("candidate_version", candidate_version.version)

        flat = {}
        if comparison is not None:
            for met in metrics_names:
                r = comparison[met]
                mlflow.log_metric(f"ab_{met}_difference", r["difference"])
                mlflow.log_metric(f"ab_{met}_p_value", r["p_value"])
                mlflow.log_metric(f"ab_{met}_effect_size", r["effect_size"])
                mlflow.log_metric(f"ab_{met}_champion_mean", r["champion_mean"])
                mlflow.log_metric(f"ab_{met}_candidate_mean", r["candidate_mean"])
                mlflow.log_metric(f"ab_{met}_ci_low", r["candidate_ci"][0])
                mlflow.log_metric(f"ab_{met}_ci_high", r["candidate_ci"][1])
                mlflow.log_metric(f"ab_{met}_is_significant", 1 if r["is_significant"] else 0)
                for kk, vv in r.items():
                    if isinstance(vv, list):
                        vv = vv[0]
                    flat[f"{met}_{kk}"] = vv

        # ------------------ Решение о выкате --------------------------------
        # Главная метрика: F1. Требуем стат. значимость И практическую значимость.
        # Страховка: PR-AUC не должен значимо деградировать.
        should_deploy = False
        decision_reason = "no champion (seed)"
        if comparison is not None:
            f1 = comparison["f1"]
            prauc = comparison["pr_auc"]
            sig_improve = f1["is_significant"] and f1["practically_significant"] and f1["difference"] > 0
            no_degrade = prauc["difference"] >= 0 or not prauc["is_significant"] or \
                prauc["difference"] > -0.005
            should_deploy = sig_improve and no_degrade
            decision_reason = (
                "deploy" if should_deploy else
                "stats_sig_practical_sig_but_guard_degraded" if sig_improve else
                "not_significant_or_not_practical")
        else:
            should_deploy = True  # первый запуск - выкатываем базу как champion

        mlflow.log_metric("should_deploy", 1 if should_deploy else 0)
        mlflow.log_metric("holdout_rows", float(len(y)))
        mlflow.set_tag("decision", decision_reason)
        flat["should_deploy"] = should_deploy
        flat["decision_reason"] = decision_reason
        flat["ab_run_id"] = ab_run_id
        flat["candidate_version"] = candidate_version.version
        flat["champion_version"] = prod_version.version if prod_version else "none"

        for met in metrics_names:
            mlflow.log_metric(f"candidate_{met}_mean",
                              float(np.mean(cand_dist[met])) if met in cand_dist else 0.0)

        print(f"A/B metrics logged in run {ab_run_id} (experiment {args.experiment_name})",
              flush=True)
        print(f"Decision: should_deploy={should_deploy} ({decision_reason})", flush=True)

    # ------------------ Экспорт решения и метрик в S3 -------------------------
    decision = {
        "should_deploy": bool(should_deploy),
        "reason": decision_reason,
        "ab_run_id": ab_run_id,
        "candidate_version": candidate_version.version,
        "champion_version": prod_version.version if prod_version else None,
        "model_name": args.model_name,
        "alpha": args.alpha,
        "effect_threshold": args.effect_threshold,
        "bootstrap_iterations": args.bootstrap_iterations,
    }
    # Компактный однострочный JSON для S3: saveAsTextFile дробит по переводам строк,
    # поэтому indent-многострочный вариант был бы разнесён по нескольким part-файлам.
    decision_json = json.dumps(decision, indent=2)
    print("Decision JSON:", decision_json, flush=True)

    # Пишем решение в S3 простым текстом (для PythonOperator в Airflow).
    # saveAsTextFile НЕ перезаписывает существующий каталог (FileAlreadyExistsException,
    # YC прячет его как "code 2"). Поэтому каждый запуск пишет в УНИКАЛЬНЫЙ каталог
    # decision_<timestamp>. DAG (apply_ab_decision) читает самый свежий непустой part.
    from datetime import datetime, timezone, timedelta
    ts = (datetime.now(timezone.utc) + timedelta(seconds=1)).strftime("%Y%m%dT%H%M%SZ")
    decision_out = f"{args.decision_output}/decision_{ts}"
    spark.sparkContext.parallelize(
        [json.dumps(decision, separators=(",", ":"))], 1).coalesce(1) \
        .saveAsTextFile(decision_out)

    metrics_df = spark.createDataFrame(
        [(k, str(v)) for k, v in flat.items() if not isinstance(v, (list, dict))],
        ["metric", "value"],
    ).withColumn("run_id", F.lit(ab_run_id))
    metrics_df.coalesce(1).write.mode("append").option("header", "true").csv(args.metrics_output)
    print(f"Decision + metrics exported to {args.decision_output} / {args.metrics_output}",
          flush=True)

    spark.stop()
    print("A/B validation finished.")


def _dump_error_to_s3():
    """Пишет полный traceback в S3 (job_errors/), чтобы его можно было прочитать
    даже после авто-удаления кластера Data Proc (иначе YC даёт только "code 2")."""
    import traceback
    tb = traceback.format_exc()
    print("=== DEBUG FULL TRACEBACK (also dumped to S3) ===\n" + tb, flush=True)
    try:
        import uuid
        from pyspark.sql import SparkSession
        s = SparkSession.builder.appName("FaultDump").getOrCreate()
        s._jsc.hadoopConfiguration().set("fs.s3a.endpoint", "storage.yandexcloud.net")
        s._jsc.hadoopConfiguration().set("fs.s3a.path.style.access", "true")
        s._jsc.hadoopConfiguration().set("fs.s3a.connection.ssl.enabled", "true")
        out = f's3a://spark-bucket-ek/job_errors/fraud_ab_test_{uuid.uuid4().hex[:8]}'
        s.sparkContext.parallelize([tb], 1).coalesce(1).saveAsTextFile(out)
        print(f"Traceback saved to {out}", flush=True)
        s.stop()
    except Exception as e:  # noqa: BLE001 - диагностика не должна маскировать основную ошибку
        print(f"Не удалось сохранить traceback в S3: {e}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        _dump_error_to_s3()
        raise
