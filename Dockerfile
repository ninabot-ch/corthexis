# CortHeXis service image: indexer + reviewer + dashboard + MCP over HTTP (corthexis serve).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    HOME=/data CORTHEXIS_DATA_DIR=/data CORTHEXIS_MODELS_DIR=/models CORTHEXIS_MEMORY_DIR=/notes

WORKDIR /opt/corthexis
COPY pyproject.toml README.md LICENSE ./
COPY corthexis ./corthexis
RUN pip install --no-compile . \
 && mkdir -p /data /models /notes && chmod 0777 /data /notes \
 && rm -rf /root/.cache

# runs as the owner of the notes folder (CORTHEXIS_UID / CORTHEXIS_GID in the compose)
USER 1000:1000
EXPOSE 8420
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8420/api/info', timeout=4)" || exit 1
CMD ["corthexis", "serve", "--host", "0.0.0.0", "--port", "8420"]
