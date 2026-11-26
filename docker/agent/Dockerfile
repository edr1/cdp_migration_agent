# Imagen del motor agentico (multi-arch: amd64 / arm64 DGX Spark)
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends gcc g++ libsasl2-dev \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements-agent.txt .
RUN pip install --no-cache-dir -r requirements-agent.txt
COPY migration_agent ./migration_agent
COPY config ./config
COPY skills ./skills
COPY examples ./examples
COPY tests ./tests
ENTRYPOINT ["python", "-m", "migration_agent"]
CMD ["--help"]
