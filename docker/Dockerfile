FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Build deps for hatchling
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl tini \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY agent ./agent

RUN pip install --no-cache-dir .

# Persistent volumes (mount from compose):
#   /data       — sqlite db + RAG index
# AGENT_HOST is intentionally NOT set to 0.0.0.0 here (#13): baking it into
# the image meant `docker run -p 8765:8765` published the agent to the LAN,
# while the compose file carefully bound it to 127.0.0.1. Set AGENT_HOST at
# run time if you really want it on every interface, and put something in
# front of it when you do.
ENV AGENT_DB_PATH=/data/agent.db \
    AGENT_INDEX_PATH=/data/docs_index/index.json.gz \
    AGENT_PORT=8765

RUN mkdir -p /data/docs_index && \
    useradd -u 1000 -m certmate && \
    chown -R certmate:certmate /data

USER certmate
VOLUME ["/data"]
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD curl -fsS http://localhost:8765/health >/dev/null || exit 1

ENTRYPOINT ["tini", "--"]
CMD ["python", "-m", "agent.main"]
