# Backend image for Render (or any container host).
# Build context = repo root.
FROM python:3.11-slim

# git: clone repos + set the workspace git baseline. The sandbox runs repo tests
# as subprocesses (SANDBOX_BACKEND=local, since a PaaS gives no Docker daemon).
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt pytest==8.3.4

COPY backend ./backend
COPY examples ./examples

WORKDIR /app/backend
ENV PYTHONUNBUFFERED=1 \
    LLM_PROVIDER=gemini \
    VECTOR_BACKEND=memory \
    SANDBOX_BACKEND=local
EXPOSE 8000
# $PORT is provided by the host (Render sets it); default 8000 locally.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
