# DevPilot test runner. Build context = the runner/ directory.
#
# Deliberately secret-free: the only thing in this image's environment is RUNNER_TOKEN.
# It runs as root ONLY so it can drop each untrusted test run to an unprivileged per-slot
# uid (see app/sandbox.py); /jobs refuses to work if it can't.
#
# One image, three toolchains (Python, Node, Go), so a single service and a single URL
# serve every supported language. Node and Go are copied from their official images.
FROM node:20-slim AS node
FROM golang:1.22-bookworm AS golang

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Node 20 + npm (the runner installs with --ignore-scripts, so no native build chain is needed)
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

# Go 1.22 (CGO is disabled at run time, so no C compiler is needed either)
COPY --from=golang /usr/local/go /usr/local/go
ENV PATH="${PATH}:/usr/local/go/bin" GOTOOLCHAIN=local

# Pre-warm a Go build cache with the standard library compiled for tests, so a run doesn't
# recompile it (slow on a small host, and it would fill the per-run disk cap). Each run gets
# its own writable copy; see app/sandbox.py.
RUN mkdir -p /tmp/warm /opt/gocache-seed && cd /tmp/warm \
    && printf 'module warm\n\ngo 1.22\n' > go.mod \
    && printf 'package warm\n\nimport (\n\t"bufio"\n\t"bytes"\n\t"context"\n\t"encoding/json"\n\t"errors"\n\t"fmt"\n\t"io"\n\t"math"\n\t"math/rand"\n\t"os"\n\t"path/filepath"\n\t"regexp"\n\t"sort"\n\t"strconv"\n\t"strings"\n\t"sync"\n\t"testing"\n\t"time"\n\t"unicode"\n)\n\nfunc TestWarm(t *testing.T) {\n\t_, _, _, _, _, _, _, _, _ = bufio.NewReader, bytes.NewBuffer, context.Background, json.Marshal, io.EOF, math.Pi, rand.Int, os.Getpid, filepath.Join\n\t_, _, _, _, _, _, _, _ = regexp.MustCompile, sort.Ints, strconv.Itoa, strings.ToUpper, time.Now, unicode.IsLetter, errors.New, fmt.Sprint\n\tvar m sync.Mutex\n\tm.Lock()\n}\n' > warm_test.go \
    && GOCACHE=/opt/gocache-seed CGO_ENABLED=0 GOFLAGS=-buildvcs=false go test ./... \
    && chmod -R a+rX /opt/gocache-seed && rm -rf /tmp/warm

WORKDIR /srv
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt pytest==8.3.4 \
    && mkdir -p /tmp/dp-runs /tmp/dp-deps && chmod 711 /tmp/dp-runs && chmod 755 /tmp/dp-deps

COPY app ./app

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 10000
# $PORT is provided by Render; default 10000 locally.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000} --no-server-header"]
