import os

import mlflow
import yaml
from mlflow.tracking import MlflowClient


def load_config(path="params.yaml"):
    with open(path) as f:
        return yaml.safe_load(f)


def main():
    config = load_config()
    mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000"))
    client = MlflowClient()
    model_name = config["mlflow"]["registered_model_name"]
    metric = config["promote"]["metric_name"]
    min_imp = config["promote"]["min_improvement"]

    try:
        latest = client.get_latest_versions(model_name, stages=["None"])[0]
        run = client.get_run(latest.run_id)
        new_metric = run.data.metrics.get(metric, 0)
        print(f"Candidate version {latest.version}: {metric}={new_metric:.4f}")

        prod_versions = client.get_latest_versions(model_name, stages=["Production"])
        if not prod_versions:
            client.transition_model_version_stage(name=model_name, version=latest.version, stage="Production", archive_existing_versions=False)
            print(f"Promoted v{latest.version} to Production (first model)")
            return
        prod = prod_versions[0]
        prod_run = client.get_run(prod.run_id)
        prod_metric = prod_run.data.metrics.get(metric, 0)
        print(f"Current Production v{prod.version}: {metric}={prod_metric:.4f}")
        if new_metric >= prod_metric + min_imp:
            client.transition_model_version_stage(name=model_name, version=latest.version, stage="Production", archive_existing_versions=True)
            print(f"Promoted v{latest.version} to Production (improved)")
        else:
            print("Not promoted: insufficient improvement")
    except Exception as e:  # noqa: BLE001
        print(f"Promote skipped/failed: {e}")


if __name__ == "__main__":
    main()
