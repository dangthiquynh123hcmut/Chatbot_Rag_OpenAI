FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DEFAULT_TIMEOUT=300 \
    PIP_RETRIES=10 \
    HF_HUB_DISABLE_XET=1 \
    DOCLING_ARTIFACTS_PATH=/opt/docling-models
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-docling.txt .
RUN pip install --no-cache-dir -r requirements-docling.txt
RUN docling-tools models download layout tableformer picture_classifier rapidocr -o /opt/docling-models

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ENV HF_HUB_OFFLINE=1

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
