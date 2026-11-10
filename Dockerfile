# Container image for the stdio MCP server.
#
# Glama builds this to introspect the 29 tools and to offer a deployable image;
# `docker build -t turbo-quant-memory . && docker run -i --rm turbo-quant-memory`
# is the same thing by hand. The server speaks MCP over stdin/stdout, so the
# container is meant to be driven by an MCP client, not published on a port.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TQMEMORY_HOME=/data \
    TQMEMORY_DAEMON_DISABLE=1 \
    TQMEMORY_MIGRATE_ON_STARTUP=1

# onnxruntime (through fastembed) and pyarrow (through lancedb) load libgomp at
# import time; the slim image does not carry it.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .
RUN pip install .

RUN useradd --create-home --uid 1000 tqmemory \
 && mkdir -p /data \
 && chown -R tqmemory:tqmemory /data
USER tqmemory

# Memory lives in /data; mount a volume to keep it across runs.
VOLUME ["/data"]

CMD ["turbo-memory-mcp", "serve"]
