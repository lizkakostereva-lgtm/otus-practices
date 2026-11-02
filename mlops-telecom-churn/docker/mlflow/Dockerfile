FROM python:3.11-slim

RUN pip install --no-cache-dir \
    mlflow==2.16.2 \
    boto3==1.34.162 \
    psycopg2-binary==2.9.9

EXPOSE 5000

CMD ["mlflow", "server", "--host", "0.0.0.0", "--port", "5000"]
