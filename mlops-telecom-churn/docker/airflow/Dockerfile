FROM apache/airflow:2.9.3-python3.11

# Prefer IPv4: the cloud VMs have no IPv6 route and some CDNs publish AAAA.
USER root
RUN echo 'precedence ::ffff:0:0/96  100' >> /etc/gai.conf
USER airflow

# The image is built on the VM (ansible-quota/services.yml). Airflow keeps its
# own pinned dependencies, so the ML libraries are installed against the
# official constraints file.
# Versions below follow the Airflow 2.9.3 constraints file: anything the
# constraints pin (pandas, boto3, PyYAML, s3fs) must match, the rest is free.
RUN pip install --no-cache-dir \
    --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-2.9.3/constraints-3.11.txt" \
    pandas==2.1.4 \
    scikit-learn==1.5.1 \
    mlflow==2.15.1 \
    boto3==1.34.131 \
    joblib==1.4.2 \
    pyyaml==6.0.1 \
    s3fs==2023.12.2
