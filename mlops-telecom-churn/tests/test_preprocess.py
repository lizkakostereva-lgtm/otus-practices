from src.data.preprocess import preprocess_data
import pandas as pd


def test_preprocess_churn_mapping():
    df = pd.DataFrame({
        "customerID": ["1"],
        "gender": ["Female"],
        "SeniorCitizen": [0],
        "Partner": ["No"],
        "Dependents": ["No"],
        "tenure": [1],
        "PhoneService": ["Yes"],
        "MultipleLines": ["No"],
        "InternetService": ["DSL"],
        "OnlineSecurity": ["No"],
        "OnlineBackup": ["No"],
        "DeviceProtection": ["No"],
        "TechSupport": ["No"],
        "StreamingTV": ["No"],
        "StreamingMovies": ["No"],
        "Contract": ["Month-to-month"],
        "PaperlessBilling": ["Yes"],
        "PaymentMethod": ["Electronic check"],
        "MonthlyCharges": [29.85],
        "TotalCharges": ["29.85"],
        "Churn": ["Yes"]
    })
    out = preprocess_data(df)
    assert out["Churn"].iloc[0] == 1
    assert out["TotalCharges"].iloc[0] == 29.85
