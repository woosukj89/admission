FROM python:3.11-slim

WORKDIR /app

# System deps for psycopg2-binary, pdfplumber, etc.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x scripts/start.sh

# Data directory is mounted as a Railway volume at runtime
# (admission.db + users.db live there)
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["scripts/start.sh"]
