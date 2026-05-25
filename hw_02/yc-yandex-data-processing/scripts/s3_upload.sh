#!/bin/bash

set -euo pipefail

SOURCE_BUCKET="s3://otus-mlops-source-data/"
DEST_BUCKET="s3://spark-bucket-ek/"
LOCAL_DIR="./data"

mkdir -p "${LOCAL_DIR}"

# Уже загруженные файлы
SKIP_FILES=(
    "2019-08-22.txt"
    "2019-09-21.txt"
    "2019-10-21.txt"
    "2019-11-20.txt"
    "2019-12-20.txt"
    "2020-01-19.txt"
)

should_skip() {
    local file="$1"

    for skipped in "${SKIP_FILES[@]}"; do
        if [[ "$file" == "$skipped" ]]; then
            return 0
        fi
    done

    return 1
}

s3cmd ls "${SOURCE_BUCKET}" | awk '{print $4}' | while read -r FILE_URL; do

    FILE_NAME=$(basename "${FILE_URL}")

    if should_skip "${FILE_NAME}"; then
        echo "Skipping ${FILE_NAME}"
        continue
    fi

    echo "Downloading ${FILE_NAME}..."

    s3cmd get \
        --multipart-chunk-size-mb=512 \
        "${FILE_URL}" \
        "${LOCAL_DIR}/${FILE_NAME}"

    echo "Uploading ${FILE_NAME}..."

    s3cmd put \
        --multipart-chunk-size-mb=512 \
        "${LOCAL_DIR}/${FILE_NAME}" \
        "${DEST_BUCKET}${FILE_NAME}"

    echo "Removing local file..."
    rm -f "${LOCAL_DIR}/${FILE_NAME}"

    echo "Done: ${FILE_NAME}"

done

echo "All files transferred."