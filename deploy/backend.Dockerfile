FROM python:3.11-slim

# Dependencias del sistema (libxslt para lxml, libffi/openssl para zeep+mTLS, libpq por si)
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential libxml2-dev libxslt1-dev libffi-dev libssl-dev \
      curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Cache de pip
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt && \
    pip install --no-cache-dir emergentintegrations --extra-index-url https://d33sy5i8bnduwe.cloudfront.net/simple/ || true

# Código backend
COPY backend/ /app/

# Carpetas de storage y assets (montadas como volúmenes en docker-compose)
RUN mkdir -p /app/storage/jobs /app/storage/pagos_ventanilla /app/assets/logos

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8001/api/auth/me -o /dev/null || exit 0

# 2 workers por defecto; usa env WORKERS para override
ENV WORKERS=2
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port 8001 --workers ${WORKERS}"]
