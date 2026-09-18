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

**Ingestion:** `repo → chunk (AST-aware) → embed (Gemini) → vector store`
**Retrieval:** hybrid semantic + keyword with reciprocal-rank fusion.
**Repair:** `bug → run tests → RAG → structured patch → apply → test → retry`

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
│   │   │   ├── chunker.py       AST-aware chunking (tree-sitter) + fallback
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
│   │   │   ├── runner.py        isolated execution (docker/local/disabled)
│   │   │   ├── tests.py         test-command detection + result parsing
│   │   │   └── Dockerfile       the sandbox image
│   │   ├── patch/patcher.py     workspace + structured patch application + git diff
│   │   ├── obs/                 lifecycle tracing (spans, tokens, redaction) + viewer
│   │   ├── review/reviewer.py   code-review agent (structured findings)
│   │   └── db/schema.sql        pgvector table + index
│   ├── mcp_server/server.py     expose 7 tools over MCP (incl. patch/test loop)
│   ├── eval/
│   │   ├── benchmark.json       10 deterministic tasks across categories
│   │   ├── harness.py           full-pipeline metrics + persisted runs
│   │   ├── report.py            renders the evaluation report
│   │   └── run_eval.py          CLI: run / compare prompts / re-print
│   └── cli.py                   use everything from the terminal (incl. `debug`)
├── examples/                    10 benchmark fixture repos (each: bug + failing test)
│   ├── sample_app/ bank_app/ cart_app/ auth_app/ inventory_app/  (single-bug)
│   └── shopcore/ webapp/ securestore/ datapipe/ formsvc/          (multi-module)
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
cases + an LLM-as-judge grader, a Node/JS sandbox, and trace export to OTel.

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
   → LLM emits a STRUCTURED patch (root cause + exact line edits)
   → backend validates & applies the patch       (LLM never touches the FS)
   → run tests in the sandbox again
   → still failing? feed the output back, revise  (up to REPAIR_MAX_ITERS)
   → return: root cause · files changed · unified diff · test results
```

Key design points to speak to in an interview:

- **The LLM never edits the filesystem.** It returns a structured patch
  (`{file, changes:[{start_line, end_line, replacement}]}`); the backend
  validates line ranges and applies it deterministically
  ([`patch/patcher.py`](backend/app/patch/patcher.py)). Controllable and auditable.
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
`docker` (default, isolated) · `local` (host subprocess — **dev only, not
isolated**, for when Docker isn't installed) · `disabled` (stub).

### API

```bash
POST /api/agent/debug   { "repo_id": "...", "bug_report": "...", "test_path": "" }
```

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

### Resume framing

Lead with the *experiment*, not "an AI coding chatbot":

> **DevPilot — AI Software-Engineering Agent** · Python, FastAPI, Gemini/Ollama,
> pgvector, tree-sitter, Docker, MCP
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

State it as **"97% end-to-end repair success on a 36-case benchmark"** — never
"97% accuracy," and don't generalize beyond the benchmark.

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
- ✅ **Embedding-config guard** — each repo records its embedder; switching provider auto-re-ingests
  instead of silently querying a mismatched vector space. **Failure taxonomy** in the eval report.

Next, in priority order:

1. **Run the matrix on a local model** (`--provider ollama`, baseline + v1/v2/v3
   over all 40 cases) and/or Gemini once quota allows, to populate the results
   table — then add an **LLM-as-judge grader** for answer quality (keyword proxy today).
3. **Code-review context enrichment** — feed retrieved surrounding code into the
   reviewer so it sees callers/definitions the diff omits.
4. **Node/JS sandbox** — extend the sandbox image + `detect_test_command` for jest.
5. **Export traces to OTel/LangSmith**; **LangGraph** port of the loop.
6. **UI**: surface the repair loop + a trace timeline in the React app.
7. **Migrate to the `google-genai` SDK** (`google-generativeai` is deprecated).

---

## Safety notes

- File tools are sandboxed to the repo directory (`_safe_path`) — no path escape.
- Patch application validates line ranges and refuses paths that escape the
  workspace; the LLM never writes to disk directly.
- Test execution defaults to Docker isolation (`SANDBOX_BACKEND=docker`). The
  `local` backend runs repo code on the host with **no isolation** — use it only
  for repos you trust, and it prints a loud warning every run.
- Your API key stays in `.env` (gitignored). Don't commit it.
