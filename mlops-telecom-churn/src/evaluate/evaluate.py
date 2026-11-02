import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.metrics import roc_auc_score, f1_score, classification_report
import joblib

from src.data.load import load_config


def main():
    config = load_config()
    df = pd.read_csv(config["data"]["processed_path"])
    target = config["features"]["target"]
    drop_cols = config["features"]["drop_cols"]

    X = df.drop(columns=[target] + drop_cols, errors="ignore")
    y = df[target]
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=config["data"]["test_size"], random_state=config["data"]["random_state"], stratify=y
    )

    pipe = joblib.load("models/pipeline.joblib")
    y_pred = pipe.predict(X_test)
    y_proba = pipe.predict_proba(X_test)[:, 1]

    print("ROC-AUC:", roc_auc_score(y_test, y_proba))
    print("F1:", f1_score(y_test, y_pred))
    print("\n", classification_report(y_test, y_pred))


if __name__ == "__main__":
    main()
