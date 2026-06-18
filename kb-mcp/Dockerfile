FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml /app/
COPY kb /app/kb
RUN pip install --no-cache-dir .
# Pre-download the embedding model into the image cache (optional but avoids first-call latency)
ENV KB_EMBED_MODEL=BAAI/bge-small-en-v1.5
EXPOSE 8077
CMD ["uvicorn", "kb.server:app", "--host", "0.0.0.0", "--port", "8077"]
