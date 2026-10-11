# DevPilot test runner. Build context = the runner/ directory.
#
# Deliberately tiny and secret-free: the only thing in this image's environment is
# RUNNER_TOKEN. It runs as root ONLY so it can drop each untrusted test run to an
# unprivileged per-slot uid (see app/sandbox.py); /run refuses to work if it can't.
FROM python:3.11-slim

# util-linux (prlimit) is already in the base image; pytest is what the runner executes.
WORKDIR /srv
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt pytest==8.3.4 \
    && mkdir -p /tmp/dp-runs && chmod 711 /tmp/dp-runs

COPY app ./app

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 10000
# $PORT is provided by Render; default 10000 locally.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000} --no-server-header"]
