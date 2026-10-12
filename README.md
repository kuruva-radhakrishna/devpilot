# 🧭 DevPilot — AI Software Engineering Agent

DevPilot ingests a GitHub repository, indexes it with RAG, and runs a
**tool-calling agent** that can understand, explore, and debug the codebase.
Ask it *"why does the login endpoint return 401?"* and it searches the repo,
reads the relevant files, traces the call flow, and answers **grounded in code
it actually read** — with `file:line` citations, not hallucinations.

It is deliberately built as a **production-shaped** GenAI system rather than a
ChatGPT wrapper: retrieval, an explicit agent loop, tools, a code-review agent,
an MCP server, and an evaluation harness that measures retrieval and task
quality the same way you'd evaluate a real agent — and then a real
**measure → diagnose → fix → re-benchmark** loop that took end-to-end repair
success from **33% to 97%** on a 36-case benchmark.

> Provider-independent (`LLMProvider` interface): **Google Gemini**
> (`gemini-2.5-flash` + `gemini-embedding-001`) or a **local Ollama** model
> (`qwen2.5-coder` + `nomic-embed-text`). The benchmark below was run locally on
> Ollama, so the numbers measure the *system*, not a vendor API.

---

## Headline result — a measured engineering loop

On a **36-case deterministic repair benchmark** (local `qwen2.5-coder:7b`), each
case = a real failing test the agent must make pass by patching the repo:

| Configuration | Repair success | Patch applied | Avg. iterations |
|---|--:|--:|--:|
| Baseline — no RAG, no tools | 33% (12/36) | 72% | 2.58 |
| v1 — code-aware RAG + tools | 75% (27/36) | 100% | 1.58 |
| v2 — prompt iteration | 81% (29/36) | 100% | 1.50 |
| v3 — prompt iteration | 83% (30/36) | 97% | 1.42 |
| **v4 — AST whole-function patches** | **97% (35/36)** | **100%** | **1.03** |

**The story isn't the 97% — it's how the loop got there:**

1. **Code-aware RAG + tools** took a no-context baseline from **33% → 75%** and
   eliminated patch-*application* failures.
2. **Prompt iteration** added a modest **75% → 83%** — diminishing returns.
3. **Failure analysis** (inspecting the actual diffs, not just the score) found
   that the remaining failures were **not** retrieval or reasoning: the model was
   producing the *correct* fix but mis-specifying the fragile **line-range patch
   format** — dropping/duplicating a function's `def` line → `IndentationError`.
4. **The fix targeted the interface, not the model**: replace line-range edits
   with **AST-located whole-function replacement** (the model returns a whole new
   function; the backend swaps the node and rejects any patch that would produce
   invalid Python). Result: **83% → 97%**, the structural failure class gone, and
   average iterations down to **1.03** (near one-shot).

```
failure taxonomy   v3: 30 success · 5 applied_but_tests_fail · 1 patch_not_applied
                   v4: 35 success · 1 applied_but_tests_fail · 0 patch_not_applied
```

The bottleneck was the **interface between the model and the execution system**,
found by instrumentation and failure analysis — not the model itself.

**Honest scope:** this is a 36-case deterministic benchmark, not a claim about
arbitrary repositories, and per-category n is small (3–5) so single-case flips
are noise. The robust claims are the **aggregate lift** and the **elimination of
the structural patch-failure class** (deterministic — the AST replacement cannot
drop a `def` line). See [Benchmark & results](#benchmark--results) for detail.

---

## Architecture

```
                        ┌──────────────────┐
                        │   React + Vite   │   ingest repo · ask questions
                        │       UI         │   see the agent's tool calls
                        └────────┬─────────┘
                                 │ HTTP (JSON)
                                 ▼
                        ┌──────────────────┐   /api/repos  /api/agent(/debug)
                        │  FastAPI backend │   /api/review  /api/traces
                        └────────┬─────────┘         ▲ MCP server exposes the same
                                 ▼                    │ tools to any MCP host
                ┌──────────────────────────────────┐ │  ┌───────────────────────┐
                │      Agent Runtime + Repair       │─┘  │ Tracing (every request)│
                │  plan→tool→obs · patch→test→retry │◄───│ spans · tokens · redact │
                └───┬───────────┬───────────────┬──┘    └───────────────────────┘
      search_code / │  read_file │  run_tests /  │  apply structured patch
                    ▼           ▼   git_diff     ▼
           ┌──────────────┐  ┌──────────┐  ┌──────────────────┐
           │  RAG search  │  │Repo files│  │  Sandbox (Docker)│  no network,
           │  (hybrid)    │  │(sandboxed)│ │  workspace copy  │  limits, timeout
           └──────┬───────┘  └──────────┘  └──────────────────┘
                  ▼
           ┌──────────────┐     memory (numpy)  ── zero infra
           │ Vector store │  or
           │              │     pgvector (Postgres) ── production
           └──────────────┘
```

**Ingestion:** `repo → chunk → embed (Gemini) → vector store`. The chunker splits on function
boundaries when `tree-sitter-language-pack` is installed and otherwise falls back to
overlapping line windows (the hosted deployment uses the fallback).
**Retrieval:** hybrid semantic + keyword with reciprocal-rank fusion.
**Repair:** `bug → run tests → RAG → whole-function patch → apply → test → retry`, in
Python, JavaScript/TypeScript and Go (see [Languages, dependencies and the hosted runner](#languages-dependencies-and-the-hosted-runner)).

---

## Repository layout

```
devpilot/
├── backend/
│   ├── app/
│   │   ├── main.py              FastAPI app + routes
│   │   ├── config.py            env-driven settings
│   │   ├── llm/
│   │   │   ├── client.py        provider-neutral entry point (what the app imports)
│   │   │   └── providers/       LLMProvider interface + gemini / ollama backends
│   │   ├── rag/
│   │   │   ├── chunker.py       function-boundary chunking (optional tree-sitter) + line-window fallback
│   │   │   ├── vector_store.py  memory (numpy) OR pgvector backend
│   │   │   ├── retriever.py     hybrid search + RRF + dependency-aware expansion
│   │   │   ├── import_graph.py  per-repo import graph (symptom → root-cause hops)
│   │   │   └── ingest.py        clone → chunk → embed → store
│   │   ├── agent/
│   │   │   ├── runtime.py       the agent loop (Gemini function calling)
│   │   │   ├── repair.py        the repair loop: patch → test → retry
│   │   │   ├── tools.py         search_code, read_file, find_references, run_tests
│   │   │   └── prompts/         VERSIONED prompts (debugging, code_review, patch)
│   │   ├── sandbox/
│   │   │   ├── runner.py        execution backends: docker / remote (jobs API) / local / disabled
│   │   │   ├── tests.py         language detection, test commands, pytest/jest/vitest/mocha/node:test/go parsers
│   │   │   └── Dockerfile       the Docker sandbox image (Python only)
│   │   ├── patch/
│   │   │   ├── patcher.py       workspace + whole-function patch application (Python `ast`) + git diff
│   │   │   └── functions.py     the same for JS/TS/TSX/Go via tree-sitter (qualified names, parse guard)
│   │   ├── auth/                accounts, sessions, rate limits, password-reset email
│   │   ├── obs/                 lifecycle tracing (spans, tokens, redaction) + viewer
│   │   ├── review/reviewer.py   code-review agent (structured findings)
│   │   └── db/schema.sql        pgvector table + index
│   ├── mcp_server/server.py     expose 7 tools over MCP (incl. patch/test loop)
│   ├── eval/
│   │   ├── benchmark.json       the 36-case measured Python benchmark (+ QA cases)
│   │   ├── benchmark_multilang.json  5 JS/Go/pip-dependency cases (no pass rate recorded yet)
│   │   ├── harness.py           full-pipeline metrics + persisted runs
│   │   ├── report.py            renders the evaluation report
│   │   └── run_eval.py          CLI: run / compare prompts / re-print
│   └── cli.py                   use everything from the terminal (incl. `debug`)
├── runner/                      the isolated test-runner service (Docker: Python + Node + Go)
├── examples/                    fixture repos (each: one bug + a failing test)
│   ├── sample_app/ bank_app/ cart_app/ auth_app/ inventory_app/  (Python, single-bug)
│   ├── shopcore/ webapp/ securestore/ datapipe/ formsvc/          (Python, multi-module)
│   └── js_cart/ js_text/ go_stack/ go_wallet/ py_deps/            (JavaScript, Go, pip dependency)
├── frontend/                    React + TypeScript chat UI
├── docker-compose.yml           Postgres + pgvector
├── requirements.txt
└── .env.example
```

**Working end-to-end** (verified with live Gemini calls): config, a **pluggable
LLM provider** (gemini/ollama) with retry/backoff, chunker, both vector backends,
retriever, ingest, agent
runtime + tools, **the sandboxed repair loop** (patch → test → retry), review
agent, **the MCP server** (7 tools), **lifecycle tracing**, **the evaluation
system** (40-case benchmark across 9 categories, no-RAG baseline vs v1/v2/v3,
full-pipeline metrics), API, CLI, and frontend. Clear extension **TODOs** (inline + in
[Roadmap](#roadmap-next-things-to-build)): grow the benchmark to 30–50 harder
cases + an LLM-as-judge grader, a measured pass rate for the JS/Go repair path, and trace export to OTel.

---

## Quickstart

### 0. Prerequisites
- Python 3.11+
- Node 18+ (only for the UI)
- A **free** Gemini API key: https://aistudio.google.com/app/apikey

### 1. Backend

```bash
cd devpilot
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt

cp .env.example .env          # then paste your GEMINI_API_KEY into .env
```

Run the API:

```bash
cd backend
uvicorn app.main:app --reload --port 8000
```

Open **http://localhost:8000/docs** for the interactive API.

### 2. Try it from the CLI (no UI needed)

```bash
cd backend
python cli.py ingest https://github.com/pallets/click
python cli.py repos
python cli.py ask click-XXXXXXXX "How does Click parse command-line options?"
```

### 3. See the repair loop (the killer demo)

A bundled example repo has one deliberate bug and a failing test, so you can
watch DevPilot go **red → patch → green** locally. One command does the whole
live path (ingest + repair) — this is the smoke test to run once after adding
your Gemini key:

```bash
cd backend
python cli.py demo
```

Or drive it manually against any repo:

```bash
python cli.py ingest ../examples/sample_app
# copy the repo_id it prints, then:
python cli.py debug sample_app-XXXXXXXX "Orders without an address crash with a KeyError; they should ship to 'unknown'."
```

The repair loop is hardened against real-model quirks: prose instead of JSON
(retries with a stricter nudge), trailing commas / markdown fences (tolerant
JSON extraction), wrong file paths (basename resolution), bad line ranges (error
fed back), and no-op or repeated patches (short-circuited). Verified against
simulated messy Gemini output.

DevPilot runs the tests (1 fails), generates a **structured patch**, applies it,
re-runs the tests **in the sandbox** (both pass), and prints the root cause, the
diff, and the validated result. See [Repair loop](#repair-loop-the-killer-feature).

### 4. Frontend (optional)

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
```

`VECTOR_BACKEND=memory` (the default) needs **no database** — it runs on numpy
and persists to `data/memory_store.pkl`. That's the fastest way to see it work.

---

## Switching to the production vector store (pgvector)

```bash
docker compose up -d           # starts Postgres + pgvector, loads schema.sql
# in .env:  VECTOR_BACKEND=pgvector
```

Re-ingest a repo and it now lives in Postgres, queried with pgvector's cosine
operator (`<=>`) and an IVFFlat index. Same code path — only the backend swaps.

---

## Repair loop (the killer feature)

DevPilot doesn't just explain code — it **diagnoses a bug, writes a fix, and
proves it** by running tests. This is what makes it an *agent* rather than a
code chatbot.

```
bug report
   → copy repo into a throwaway workspace (git baseline)
   → run tests in the sandbox                    (confirm the failure)
   → retrieve relevant files (RAG) + read source
   → LLM emits a STRUCTURED patch (root cause + one whole function per edit)
   → backend validates & applies the patch       (LLM never touches the FS)
   → run tests in the sandbox again
   → still failing? feed the output back, revise  (up to REPAIR_MAX_ITERS)
   → return: root cause · files changed · unified diff · test results
```

Key design points to speak to in an interview:

- **The LLM never edits the filesystem.** It returns a structured patch
  (`{file, function, new_source}`: a whole new function); the backend locates the function
  by name (Python `ast`, or tree-sitter for JS/TS/Go), swaps it in, and rejects anything
  that doesn't parse ([`patch/patcher.py`](backend/app/patch/patcher.py),
  [`patch/functions.py`](backend/app/patch/functions.py)). The original line-range format is
  still supported for the older prompts. Controllable and auditable.
- **Tests run in an isolated sandbox** ([`sandbox/runner.py`](backend/app/sandbox/runner.py)):
  Docker with `--network=none`, memory/CPU/PID limits, a wall-clock timeout, and
  a non-root user. Same systems thinking as Docker-isolated compilation in Code
  Arena, applied to an LLM agent.
- **Work happens on a workspace copy** with a git baseline, so patches and test
  runs never corrupt the indexed source, and `git diff` yields a clean patch.
- **The loop self-corrects**: a failing patch's test output is fed back for the
  next attempt.

### Building the sandbox image

```bash
docker build -t devpilot-sandbox:latest backend/app/sandbox
```

`SANDBOX_BACKEND` selects the executor:
`docker` (default, isolated, Python only) · `remote` (an isolated, secret-free
[runner service](runner) — how the hosted demo does it, since a PaaS has no Docker
daemon) · `local` (host subprocess — **dev only, not isolated**, for when Docker isn't
installed) · `disabled` (stub).

The runner (`runner/`) runs exactly one fixed command per language on an uploaded workspace
(`python -m pytest`, `npm test` or `go test`), as an unprivileged per-slot user with resource
limits, and refuses to run if it can't isolate. Its isolation is attacked for real in CI
(privilege escape, fork/memory/disk bombs, stray daemons, malicious archives, install-time
script and option injection) against the built container, for all three languages.

### Languages, dependencies and the hosted runner

Repair works on **Python, JavaScript/TypeScript and Go** repositories. The hosted demo runs
every repair in a separate, secret-free [runner service](runner) (a PaaS has no Docker
daemon, so isolation comes from an unprivileged per-run user, resource limits and a
runner that holds nothing worth stealing). One runner image has all three toolchains.

| | Python | JavaScript / TypeScript | Go |
|---|---|---|---|
| **Detected by** | `test_*.py` / `*_test.py` files | `package.json` with a `test` script | `go.mod` + `*_test.go` files |
| **Test command (fixed)** | `python -m pytest` | `npm test` (whatever the repo's `test` script runs: node:test, jest, vitest, mocha) | `go test -v ./...` (or the requested package) |
| **Dependencies** | `pip install --only-binary=:all:` from `requirements*.txt` and `pyproject.toml` (test/dev extras) | `npm ci --ignore-scripts` (or `npm install --ignore-scripts` without a lockfile) | `go mod download`, then tests run offline |
| **How the fix is applied** | `ast`: replace one whole function | tree-sitter: replace one function, method, arrow function or object member (`.js .jsx .mjs .cjs .ts .tsx .mts .cts`) | tree-sitter: replace one function or method (`Type.Method`) |
| **Prompt** | `v4` (the measured one) | `v5` | `v5` |

**How a fix is made in every language.** The model never edits files. It returns the *whole
new function*, named by its qualified name (`Cart.total`, `Stack.Pop`); the backend finds
that function, swaps its source and refuses the patch if the result doesn't parse, if the
bare name is ambiguous (`total` exists on two classes: the model is told to qualify it and
retry), or if it touches a test file (`*_test.go`, `*.test.*`, `*.spec.*`, `test_*.py`).

**What "installing dependencies" means here, and what it doesn't.** A failing test is only
evidence of a code bug if the packages it imports are present, so the runner installs them
first, and does it defensively because the repo is untrusted:

- **No repo code runs at install time.** pip is restricted to wheels (so no `setup.py` ever
  executes), npm runs with `--ignore-scripts` and from a manifest stripped of the repo's own
  `scripts`, and Go only downloads source archives. Requirement lines that are options
  (`--index-url`, `-r`, `-e`), URLs, VCS references or local paths are skipped and reported,
  never passed to pip.
- Installs run as the same unprivileged per-run user, under CPU/memory/process/file-size
  limits, a 150-second timeout and a 300 MB size cap.
- Results are cached by a hash of the manifest in a **read-only, root-owned** directory, so
  the baseline run, the verification run and every retry of one repair install once, and test
  code can read the cache but cannot modify what a later run will use.
- If the install fails, the repair **stops before spending a model call** and reports which
  packages failed and the log tail (`dependencies_unavailable`). It does not guess a patch
  against tests that fail only because their packages are missing.

**Limits, stated plainly**

- **Network egress can't be blocked** on Render's free tier (no `CAP_SYS_ADMIN`), so a test
  run, like an install, can reach the internet. Every runner response says
  `network_isolated: false`. The runner holds no secrets, but run only code you'd be fine
  executing. (The Docker backend does run with `--network=none`.)
- Packages that ship only source (no wheel), need system libraries, native builds, a
  database or a browser will not install or run. Full React, Next.js or Vite apps are
  limited by the free runner's 512 MB of memory; small libraries and Node services are the
  realistic target.
- The Docker backend (`SANDBOX_BACKEND=docker`) is still **Python-only**; JavaScript and Go
  need the remote runner (or `local` with Node/Go installed).
- A runner that can't isolate (not root, no token) **refuses** to run, and a run that never
  executed is reported as *not executed*, never as a pass.

**The runner API** ([`runner/app/main.py`](runner/app/main.py)). Jobs, not one long request,
because installing dependencies plus a test run can outlast the ~100 s a free host's edge
allows a request:

```text
POST /jobs?lang=node&test_path=test/cart.test.js&timeout=60&install=1
     Authorization: Bearer <RUNNER_TOKEN>      body: the workspace as a .tar.gz
     -> 202 {"job_id"}        (503 when every slot is busy)
GET  /jobs/{id}               -> {"status": "running|done|error", "stage", "result"?, "error"?}
GET  /healthz                 -> {"isolation", "ready", "slots", "toolchains": {python,node,go}}
```

The backend uploads, polls every 1.5 s, and gives up with *not executed* if the runner is
lost, restarted or too slow.

**What is and isn't measured.** The **97%** figure is from the 36-case **Python**
benchmark. The JavaScript, Go and dependency-install paths are covered by tests (the
function-replacement logic, parsers for node:test/jest/vitest/mocha/`go test` output, the
repair loop against a scripted runner, and attacks on the real runner container in CI), and
there are five fixture cases in [`backend/eval/benchmark_multilang.json`](backend/eval/benchmark_multilang.json),
but **no repair success rate has been recorded for them**, so none is claimed:

```bash
cd backend
SANDBOX_BACKEND=local python -m eval.run_eval --benchmark benchmark_multilang.json --prompt-versions v4
```

The sample bugs in the UI's empty state (`examples/js_cart`, `js_text`, `go_stack`,
`go_wallet`, `py_deps`) are those fixtures.

### API

```bash
POST /api/agent/debug        { "repo_id": "...", "bug_report": "...", "test_path": "" }
                             -> 202 { "job_id": "..." }      # starts a background job
GET  /api/agent/debug/{id}   -> { status, events[], result? }  # live progress + the result
```

Repairs are background jobs because one can outlive a free host's request limit. The
web UI's **Fix a bug** mode drives this, and the bundled demo bugs (`examples/`) are
one click away as `sample:<name>` sources.

---

## What each piece demonstrates (interview talking points)

| Component | What you can speak to |
|---|---|
| **RAG pipeline** | AST-aware chunking vs windowing, embeddings, cosine similarity, metadata, context-window budgeting |
| **Hybrid retrieval** | why pure vector search misses exact symbols, reciprocal-rank fusion, recall vs precision |
| **Agent runtime** | plan→tool→observe loop, function calling, step budgets, why hand-rolled vs LangGraph |
| **Tools** | tool schema design via docstrings, path sandboxing |
| **Repair loop** | structured patches, propose→apply→validate→retry, why the LLM never edits the FS |
| **Sandboxed execution** | Docker isolation: no network, resource limits, timeout, non-root; why untrusted code needs it |
| **Observability** | lifecycle traces (span tree), token/latency capture, attribute redaction; OTel-shaped |
| **MCP** | internal tool abstractions exposed as a standard interface; inspect→patch→validate over MCP |
| **Resilient infra** | provider-aware retry/backoff (respects Retry-After), quota-aware fail-fast, partial runs |
| **Provider abstraction** | one `LLMProvider` interface, swap gemini/ollama; evaluate the same agent across models |
| **Vector store abstraction** | one interface, two backends; when numpy is fine and when you need pgvector |
| **Code-review agent** | structured JSON output, robust parsing, security/correctness/perf dimensions |
| **MCP server** | standardized, discoverable tool interface decoupled from the agent |
| **Evaluation** | full-pipeline metrics (retrieval→repair→cost), Repair Success Rate, comparing prompt versions objectively, persisted runs |
| **Versioned prompts** | prompts as artifacts you measure, not vibes |

This directly extends your Meesho Genie evaluation work: same discipline
(a labelled question set + measurable metrics), applied to an agent you built.

---

## Evaluation

A real LLM-evaluation system, not a metric or two. It runs a benchmark of
deterministic tasks against bundled fixture repos and measures the **whole
pipeline**, then persists every run and prints a report.

```bash
cd backend
# baseline (no RAG) + one prompt version + all QA cases:
SANDBOX_BACKEND=local python -m eval.run_eval

# the full experiment — baseline vs prompt versions:
SANDBOX_BACKEND=local python -m eval.run_eval --prompt-versions v1,v2,v3

# fast iteration on one case or a whole category:
python -m eval.run_eval --only repair_overdraft --no-baseline
python -m eval.run_eval --only security

# re-print a saved run:
python -m eval.run_eval --report eval/results/<run_id>.json
```

**Benchmark v2** ([`eval/benchmark.json`](backend/eval/benchmark.json)): **40
deterministic cases** (36 repair + 4 QA) across **9 categories**, each pinned to a
bundled fixture repo under [`examples/`](examples) with a real failing test:

| Category | n | | Category | n |
|---|--:|---|---|--:|
| single-file-logic | 5 | | security | 4 |
| multi-file-dependency | 5 | | root-cause-differs | 4 |
| retrieval-distractor | 5 | | performance | 3 |
| misleading-names | 4 | | regression | 3 |
| repository-qa | 4 | | multi-iteration | 3 |

The harder categories are the point — **root-cause-differs** (the failing test is
in the obvious module, the bug is in a helper it calls), **misleading-names**
(a function named `sort_by_price` that sorts by name), **retrieval-distractor**
(the bug lives among many similar modules), and **security/performance** cases
with deterministic tests (path traversal, SQL injection, N+1 queries via call
counters). Every fixture is verified to fail as designed.

**The baseline comparison** is the headline experiment: each repair case also
runs with **no retrieval and no tools** (`baseline`), so the report shows the lift
from code-aware RAG — *"RAG + tools improved repair success from X% (baseline) to
Y%"* — a far stronger story than "v3 beat v2".

**Metrics measured** (per [`eval/harness.py`](backend/eval/harness.py)):

| Layer | Metrics |
|---|---|
| Retrieval | Recall@K, MRR, relevant-file hit |
| Tool use | tool-call count, tool-success rate |
| Agent | task completion, iterations/task |
| Repair | patch generated / applied, tests before → after |
| LLM | groundedness (expected-keyword coverage) |
| Perf | latency, input/output tokens, estimated cost |

Headline: **Repair Success Rate = validated fixes / total repair bugs.** The
report prints baseline-vs-v1/v2/v3, success **by category**, pipeline-wide
metrics, and per-case detail (with `trace_id`). Every run — including partial
runs interrupted by quota — is saved to `eval/results/<run_id>.json`.

**Failure analysis** — the report also buckets every repair outcome so you can
answer *"retrieval is 100%, why does the agent still fail?"*: `retrieval_failure`
(relevant file never retrieved) · `no_patch_generated` · `patch_not_applied`
(bad line ranges/paths) · `applied_but_tests_fail` (reasoning/patch quality) ·
`wrong_file` · `success`. That turns an aggregate score into a diagnosis of
*where* the pipeline breaks (retrieval vs reasoning vs patch vs validation).

> Numbers come only from real runs — the harness never fabricates results. Run it
> with your key to populate the table before quoting any figure.

---

## Benchmark & results

Full end-to-end results on the 36-case repair benchmark (local `qwen2.5-coder:7b`
+ `nomic-embed-text`, `SANDBOX_BACKEND=local`). Repair success by category, v3
(line-range patches) vs v4 (whole-function patches):

| Category | n | v3 | v4 |
|---|--:|--:|--:|
| single-file-logic | 5 | 5/5 | 5/5 |
| multi-file-dependency | 5 | 5/5 | 5/5 |
| retrieval-distractor | 5 | 5/5 | 5/5 |
| root-cause-differs | 4 | 4/4 | 4/4 |
| misleading-names | 4 | 3/4 | 4/4 |
| security | 4 | 2/4 | 4/4 |
| regression | 3 | 2/3 | 3/3 |
| multi-iteration | 3 | 2/3 | 3/3 |
| performance | 3 | 3/3 | 2/3 |
| **Total** | **36** | **30 (83%)** | **35 (97%)** |

The **retrieval layer was measured separately** (retrieval-only eval, no
generation) and reached **100% Recall@5 / 100% file-hit** after dependency-aware
expansion — see [Provider abstraction → measured experiment](#a-measured-experiment-dependency-aware-retrieval).
So retrieval is not the limiter; the arc above is about the *agent + patch* half.

**Reproduce** (needs Ollama, or `--provider gemini` with quota):

```bash
cd backend
SANDBOX_BACKEND=local python -m eval.run_eval --provider ollama --prompt-versions v1,v2,v3
SANDBOX_BACKEND=local python -m eval.run_eval --provider ollama --no-baseline --prompt-versions v4
```

Result JSONs are kept under `backend/eval/results/` as the reproducible record.

The five multi-language cases live in a separate file on purpose, so they can't dilute
or inflate the Python number: `python -m eval.run_eval --benchmark benchmark_multilang.json`.

### Resume framing

Lead with the *experiment*, not "an AI coding chatbot":

> **DevPilot — AI Software-Engineering Agent** · Python, FastAPI, Gemini/Ollama,
> pgvector, tree-sitter (JS/TS/Go patching), Docker, MCP, React
> - Built an AI software-engineering agent that debugs GitHub repositories using
>   **code-aware RAG, tool calling, sandboxed execution, and an automated repair
>   loop** (search → patch → run tests → retry), with lifecycle tracing.
> - Built a **36-case evaluation harness** measuring repair success, retrieval
>   quality, tool reliability, patch validity, iterations, and failure modes;
>   code-aware RAG lifted repair success **33% → 75%** over a no-RAG baseline.
> - Diagnosed residual failures via trace-level analysis and replaced fragile
>   line-range patches with **AST-located whole-function replacement**, raising
>   end-to-end repair success **83% → 97%** and cutting avg. repair iterations
>   **1.42 → 1.03**.
> - Made the LLM layer **provider-independent** (Gemini/Ollama) so the same agent
>   is evaluated across models; added provider-aware retry/backoff and an
>   embedding-config guard.
> - Extended repair to **JavaScript/TypeScript and Go** (tree-sitter whole-function
>   patching with qualified names and a parse guard) and added a hardened
>   **dependency-install** step to an isolated, secret-free runner (wheels-only pip,
>   `--ignore-scripts` npm, read-only cached installs). Only the Python path is
>   benchmarked (97%); the JS/Go path is tested but its pass rate is not measured.

State it as **"97% end-to-end repair success on a 36-case Python benchmark"** — never
"97% accuracy," don't generalize beyond the benchmark, and don't attach that number to the
JavaScript/Go support.

---

## MCP server

```bash
cd backend
export DEVPILOT_ACTIVE_REPO=<repo_id>     # Windows: set DEVPILOT_ACTIVE_REPO=<repo_id>
python -m mcp_server.server
```

Exposes `search_repository`, `read_file`, `find_references`, `list_files`,
`run_tests`, `git_diff`, and `create_patch` over the Model Context Protocol so
any MCP host (e.g. Claude Desktop) can drive DevPilot's tools — the same
implementations the agent uses. `create_patch` + `run_tests` + `git_diff` operate
on an isolated working copy, giving an external client the full **inspect → patch
→ validate** loop without ever touching the source. Interview framing: *"I built
the agent around internal tool abstractions, then exposed them through MCP so any
client can use them"* — a stronger story than "I used MCP."

---

## Observability

Every agent turn and repair run emits a **trace** — a tree of spans across the
lifecycle (`retrieval`, `llm.generate`, `tool.*`, `repair.iteration`,
`apply_patch`, `run_tests`) — each with duration, token counts, tool args/status,
iteration, and errors. Traces persist to `traces/<id>.json`.

```bash
cd backend
python cli.py traces              # list recent traces
python cli.py traces <trace_id>   # render one as a span tree
# also: GET /api/traces  and  GET /api/traces/{id}
```

A real repair trace looks like:

```
repair.run 9050ms status=ok
├── run_tests 3356ms  passed=False  (2 passed, 1 failed)     ← baseline
└── repair.iteration 3653ms  outcome=passed  attempt=1
    ├── llm.generate  total_tokens=165
    ├── apply_patch 7ms
    └── run_tests 2783ms  passed=True  (3 passed, 0 failed)  ← verify
```

It's a small, self-contained tracer (span/attribute model mirrors OpenTelemetry,
so exporting to OTel/LangSmith later is an additive change). **Redaction is
built in** — attributes are truncated and secret-looking keys dropped, so traces
never contain API keys or whole repository files. The `ask`/`debug` API responses
also return the `trace_id`.

---

## Resilient LLM infrastructure

Provider calls go through `with_retry` ([gemini_client.py](backend/app/llm/gemini_client.py)):
exponential backoff on transient errors (429/503/500) that **respects the API's
suggested `retry_delay`**, but **fails fast on per-day quotas** (which won't
recover for hours). The evaluation harness records **partial runs** — a case that
errors (e.g. quota exhausted) is captured as a failed row and the run still
persists and reports, instead of losing everything.

---

## Provider abstraction (evaluate the same agent across models)

The whole stack — retriever, agent runtime, repair loop, evaluation, tracing,
MCP, sandbox — talks to an **`LLMProvider`** interface
([`app/llm/providers/base.py`](backend/app/llm/providers/base.py)), never to a
vendor SDK. Two providers ship today:

- **`gemini`** (default) — `gemini-2.5-flash` + `gemini-embedding-001`.
- **`ollama`** — a **local** model (no API key, no quota), e.g. `qwen2.5-coder`
  + `nomic-embed-text` (768-dim). Repair + retrieval work on any provider;
  native tool-calling (the QA agent) is currently Gemini-only, and the runtime
  degrades gracefully when a provider lacks it.

Each ingested repo records its embedding identity (provider / model / dims). If
you switch providers, DevPilot **detects the mismatch and re-ingests
automatically** — it never queries a Gemini-indexed repo with Ollama query
vectors (different vector spaces), which would silently corrupt retrieval:

```
Embedding configuration changed:
  existing: gemini / gemini-embedding-001
  current:  ollama / nomic-embed-text
Re-ingesting <repo> ...
```

Setup (one-time) then select per run — nothing else changes:

```bash
# 1. install Ollama (https://ollama.com), then pull the models:
ollama pull qwen2.5-coder:7b
ollama pull nomic-embed-text
# 2. run DevPilot against it (Gemini stays the default):
python cli.py --provider ollama demo
SANDBOX_BACKEND=local python -m eval.run_eval --provider ollama --prompt-versions v1,v2,v3
```

Interview framing: *"I separated the model provider from the agent runtime so I
could evaluate the same agent architecture across models"* — far stronger than
"I called Gemini's API." Adding OpenAI/Anthropic/LiteLLM is one new subclass
implementing `generate` + `embed_texts` (keep `EMBED_DIM` and the `vector(768)`
column in sync with the embedder).

### Retrieval-only evaluation (no generation, no quota)

Because the 40 fixtures are deterministic (the relevant file per case is known),
retrieval quality can be scored with **no LLM generation at all** — only cheap
embeddings (free on a local provider):

```bash
python -m eval.run_eval --retrieval-only                    # all 40 cases
python -m eval.run_eval --provider ollama --retrieval-only  # zero-cost, local
```

Reports Recall@K, MRR, and relevant-file hit overall and **by category** — so you
can measure and tune retrieval even while generation quota is exhausted.

### A measured experiment: dependency-aware retrieval

The retrieval-only eval isolated one real weakness and a fix was measured
end-to-end (hypothesis → benchmark → failure analysis → change → re-benchmark),
Gemini `gemini-embedding-001`, all 40 cases, real runs:

| | Recall@5 | MRR | Notes |
|---|--:|--:|---|
| **Baseline retrieval** | 98% | 0.89 | `root-cause-differs` only **75% / 0.29** |
| **+ dependency expansion** | **100%** | **0.90** | `root-cause-differs` **100% / 0.38**, no regressions |

**The failure**: on `root-cause-differs` cases the query describes a symptom in the
obvious module (`report.py`), but the bug is in a helper it imports (`stats.py`),
which ranked below top-5 among distractor modules — `repair_report_mean` missed
entirely (Recall 0).
**The fix** ([`rag/import_graph.py`](backend/app/rag/import_graph.py) +
`_expand_by_dependencies`): after hybrid retrieval, follow the import graph — for
each base top-k hit, insert a file it *imports* that isn't already present, then
trim. The root cause lands directly below the symptom.
**Two dead ends worth noting** (both measured, not guessed): a score *bonus*
couldn't lift the added file because RRF scores are too bunched (had to insert by
position); and following *importers* reordered good cases (test files pulling
symptom modules up) — restricting to *imports only* on the *base top-k* fixed it
with zero regressions. Toggle with `RETRIEVAL_DEPENDENCY_EXPANSION`.

### End-to-end results (to be filled from real repair runs)

Numbers go here only once actually measured — nothing is estimated:

| Provider | Baseline repair | RAG+tools repair | Recall@5 |
|---|--:|--:|--:|
| _local (ollama)_ | — | — | — |
| _gemini_ | — | — | 100% |

---

## Roadmap (next things to build)

Done:

- ✅ **Docker sandbox for `run_tests`** — isolated execution (no network, resource
  limits, timeout, non-root). See [`sandbox/`](backend/app/sandbox).
- ✅ **Patch generation + validation** — structured patches applied & validated
  against the test suite in the repair loop. See [`agent/repair.py`](backend/app/agent/repair.py).
- ✅ **Evaluation system** — 40-case benchmark across 9 categories, no-RAG baseline vs
  v1/v2/v3, full-pipeline metrics, persisted runs + report. See [`eval/`](backend/eval).
- ✅ **MCP server** — 7 tools incl. the inspect→patch→validate loop. See [`mcp_server/`](backend/mcp_server).
- ✅ **Observability** — lifecycle tracing with spans/tokens/latency + redaction. See [`obs/`](backend/app/obs).
- ✅ **Resilient LLM infra** — provider-aware retry/backoff, quota-aware failure, partial eval runs.
- ✅ **Provider abstraction** — pluggable `LLMProvider` (gemini/ollama); stack is model-independent.
  Plus retrieval-only eval that needs no generation. See [`llm/providers/`](backend/app/llm/providers).
- ✅ **Retrieval experiment** — measured a `root-cause-differs` gap (75%→100% Recall@5) and closed
  it with dependency-aware expansion; a real hypothesis→fix→re-benchmark loop.
- ✅ **Hosted repair** — an isolated runner service, async repair jobs with live progress, and
  a "Fix a bug" UI with one-click sample bugs. See [`runner/`](runner).
- ✅ **Multi-language repair** — JavaScript/TypeScript and Go (tree-sitter function patching,
  `npm test` / `go test` parsers) plus defensive dependency installs for all three languages.
  See [Languages, dependencies and the hosted runner](#languages-dependencies-and-the-hosted-runner).
- ✅ **Accounts** — sign-up/login, sessions that can be revoked, rate limits, password reset by email.
- ✅ **`google-genai` SDK** — migrated off the deprecated `google-generativeai`; supports both
  `AIza…` and `AQ…` keys.
- ✅ **Embedding-config guard** — each repo records its embedder; switching provider auto-re-ingests
  instead of silently querying a mismatched vector space. **Failure taxonomy** in the eval report.

Next, in priority order:

1. **Run the matrix on a local model** (`--provider ollama`, baseline + v1/v2/v3
   over all 40 cases) and/or Gemini once quota allows, to populate the results
   table — then add an **LLM-as-judge grader** for answer quality (keyword proxy today).
3. **Code-review context enrichment** — feed retrieved surrounding code into the
   reviewer so it sees callers/definitions the diff omits.
4. **Measure the JS/Go/dependency path** — run `benchmark_multilang.json` and grow it past
   five cases, then report a pass rate for it separately from the Python 97%.
5. **Real network isolation for the runner** — needs a host that grants `CAP_SYS_ADMIN`
   (or a gVisor/Firecracker-style sandbox); today egress is open on the free tier.
6. **More stacks**: Java/Maven, Rust, a Node runner image with a native build chain.
7. **Export traces to OTel/LangSmith**; **LangGraph** port of the loop.
8. **Function-boundary chunking in production** — install `tree-sitter-language-pack` and measure
   retrieval against the line-window fallback.

---

## Safety notes

- File tools are confined to the repo directory (`_safe_path`, resolved with
  `realpath`, so symlinks and junctions can't escape). Ingestion skips symlinked
  files and anything resolving outside the repo.
- Patch application validates line ranges and refuses paths that escape the
  workspace; the LLM never writes to disk directly. Patches may not modify test
  files (so a repair can't "pass" by editing the tests).
- Test execution defaults to Docker isolation (`SANDBOX_BACKEND=docker`). The
  `local` backend runs repo code on the host with **no isolation** — use it only
  for repos you trust, and it prints a loud warning every run. In hosted
  multi-user mode (`DATABASE_URL` set) `local` is refused outright unless
  `ALLOW_UNSANDBOXED_LOCAL=true`. A disabled/blocked sandbox reports tests as
  *not executed*, never as passing.
- `/api/traces` and `/api/review` require login; traces are visible only to the
  user who triggered them.
- Rate limits exist (login, signup, reset, repairs per user) but are in-memory, per
  process. The Docker sandbox image only supports Python; JavaScript and Go run on the
  remote runner, which cannot block outbound network on a free host (see above).
- Dependency installs execute no repo code (wheels only, `--ignore-scripts`, download-only
  Go) but do reach public package registries.
- Regression tests for all of the above: `cd backend && python -m pytest tests -q`.
- Your API key stays in `.env` (gitignored). Don't commit it.
