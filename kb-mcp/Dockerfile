FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml /app/
COPY kb /app/kb
ARG KB_EXTRAS=
RUN if [ -n "$KB_EXTRAS" ]; then pip install --no-cache-dir ".[${KB_EXTRAS}]"; \
    else pip install --no-cache-dir .; fi
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh
# Pre-download the embedding model into the image cache (optional but avoids first-call latency)
ENV KB_EMBED_MODEL=BAAI/bge-small-en-v1.5
# Reranker model name baked in so `FastReranker.__init__` doesn't need the env var at build time;
# the first call will download the ONNX weights (~90 MB) on image start.
ENV KB_RERANK_MODEL=BAAI/bge-reranker-base
EXPOSE 8077
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "--factory", "kb.server:app_factory", "--host", "0.0.0.0", "--port", "8077"]
