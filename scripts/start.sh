#!/bin/bash
set -e

DB_PATH="/app/data/admission.db"

# Download admission.db from GitHub Release on first boot
if [ ! -f "$DB_PATH" ]; then
    if [ -z "$ADMISSION_DB_URL" ]; then
        echo "ERROR: admission.db not found and ADMISSION_DB_URL is not set."
        echo "  Upload admission.db as a GitHub Release asset and set ADMISSION_DB_URL."
        exit 1
    fi
    echo "Downloading admission.db from $ADMISSION_DB_URL ..."
    curl -L --fail --progress-bar -o "$DB_PATH" "$ADMISSION_DB_URL"
    echo "Download complete."
else
    echo "admission.db found on volume ($(du -sh $DB_PATH | cut -f1))."
fi

exec uvicorn src.api:app --host 0.0.0.0 --port "${PORT:-8000}"
