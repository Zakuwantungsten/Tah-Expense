# syntax=docker/dockerfile:1.7
FROM python:3.13-slim-bookworm

ARG MONGODB_REPO_SERIES=8.0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg; \
    install -d -m 0755 /etc/apt/keyrings; \
    curl --fail --show-error --silent --location \
      "https://pgp.mongodb.com/server-${MONGODB_REPO_SERIES}.asc" \
      | gpg --dearmor --yes --output /etc/apt/keyrings/mongodb-server.gpg; \
    echo "deb [signed-by=/etc/apt/keyrings/mongodb-server.gpg] https://repo.mongodb.org/apt/debian bookworm/mongodb-org/${MONGODB_REPO_SERIES} main" \
      > /etc/apt/sources.list.d/mongodb-org.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends mongodb-database-tools; \
    apt-get purge -y --auto-remove curl gnupg; \
    rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 10001 tahmeed \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent \
      --shell /usr/sbin/nologin tahmeed \
    && install -d -o 10001 -g 10001 -m 0700 /var/lib/tahmeed

WORKDIR /app
COPY pyproject.toml README.md ./
COPY app ./app
RUN python -m pip install --no-cache-dir .

USER 10001:10001

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3).read()"]

CMD ["uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000", "--proxy-headers", "--no-server-header"]
