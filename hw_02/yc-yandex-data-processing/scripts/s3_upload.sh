#!/bin/bash

SOURCE_BUCKET="s3://otus-mlops-source-data/"
DEST_BUCKET="s3://spark-bucket-ek/"
LOCAL_DIR="./data/"

mkdir -p "${LOCAL_DIR}"

FILES=$(s3cmd ls "${SOURCE_BUCKET}" | awk '{print $4}')

for FILE_URL in ${FILES}; do
    FILE_NAME=$(basename "${FILE_URL}")

    echo "Downloading ${FILE_NAME}..."
    s3cmd get "${FILE_URL}" "${LOCAL_DIR}${FILE_NAME}"

    echo "Uploading ${FILE_NAME} to ${DEST_BUCKET}..."
    s3cmd put "${LOCAL_DIR}${FILE_NAME}" "${DEST_BUCKET}${FILE_NAME}"

    echo "Deleting local ${FILE_NAME}..."
    rm -f "${LOCAL_DIR}${FILE_NAME}"

    echo "Done: ${FILE_NAME}"
done

echo "All files transferred."