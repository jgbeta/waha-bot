FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN adduser --disabled-password --gecos "" appuser

ARG BOT_STORE=sqlite
COPY requirements.txt requirements-postgres.txt ./
RUN set -eux; \
    if [ "$BOT_STORE" = "postgres" ]; then \
        pip install --no-cache-dir -r requirements-postgres.txt; \
    else \
        pip install --no-cache-dir -r requirements.txt; \
    fi

COPY app ./app
COPY prompts ./prompts
COPY scripts ./scripts
COPY examples ./examples

RUN mkdir -p /data && chown -R appuser:appuser /app /data
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
