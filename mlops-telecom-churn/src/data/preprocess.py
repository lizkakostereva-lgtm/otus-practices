import os

import pandas as pd

from src.data.load import load_raw_data, load_config, upload_to_s3


def preprocess_data(df):
    df = df.copy()
    # Fix TotalCharges
    df["TotalCharges"] = pd.to_numeric(df["TotalCharges"], errors="coerce")
    df["TotalCharges"] = df["TotalCharges"].fillna(df["TotalCharges"].median())
    # Target
    df["Churn"] = df["Churn"].map({"Yes": 1, "No": 0})
    return df


def main():
    config = load_config()
    df = load_raw_data(config)
    df_clean = preprocess_data(df)
    os.makedirs("data/processed", exist_ok=True)
    out_path = config["data"]["processed_path"]
    df_clean.to_csv(out_path, index=False)
    upload_to_s3(out_path, "processed/cleaned.csv")
    print(f"Saved processed data to {out_path}, shape={df_clean.shape}")


if __name__ == "__main__":
    main()
