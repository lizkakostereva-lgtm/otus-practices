import os
import requests

DATA_DIR = "data/raw"
os.makedirs(DATA_DIR, exist_ok=True)

# Telco Customer Churn dataset (Kaggle mirror / alternative sources)
# For course project, user can download manually from Kaggle:
# https://www.kaggle.com/datasets/blastchar/telco-customer-churn
# Place as: data/raw/WA_Fn-UseC_-Telco-Customer-Churn.csv

print("Please download Telco Customer Churn dataset from Kaggle:")
print("https://www.kaggle.com/datasets/blastchar/telco-customer-churn")
print("Save to: data/raw/WA_Fn-UseC_-Telco-Customer-Churn.csv")
