# DevPilot test sandbox image.
# Build once:
#   docker build -t devpilot-sandbox:latest backend/app/sandbox
#
# The agent's workspace is mounted at /app at run time (see sandbox/runner.py);
# nothing from the repo is baked into the image. Runs as a non-root user.
#
# This image targets Python/pytest (matches the sample demo). To support
# JavaScript repos, add a Node base layer and jest — see README "Roadmap".
FROM python:3.11-slim

# Common test deps. Add project-specific deps via an install phase (see runner)
# or extend this list for your target repos.
RUN pip install --no-cache-dir pytest==8.3.4

# Non-root user — defense in depth even inside the container.
RUN useradd --create-home --shell /bin/bash sandbox
USER sandbox

WORKDIR /app

# Default command; overridden by the runner with the detected test command.
CMD ["python", "-m", "pytest", "-q"]
