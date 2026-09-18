import { useEffect, useState } from "react";
import {
  ask,
  AuthError,
  clearApiKey,
  clearToken,
  getApiKey,
  getHealth,
  getToken,
  ingestRepo,
  listRepos,
  login,
  register,
  setApiKey,
  setToken,
  type AskResponse,
  type Health,
  type RepoMeta,
} from "./api";

const FEATURES = [
  {
    icon: "🔎",
    title: "Code-aware RAG",
    body: "Ingests a GitHub repo, chunks it by structure, and indexes it with hybrid semantic + keyword retrieval — so answers are grounded in the code, not guessed.",
  },
  {
    icon: "🧭",
    title: "Grounded Q&A",
    body: "A tool-calling agent reads files, searches code, and finds references before answering — and shows you exactly which tools it used.",
  },
  {
    icon: "🛠️",
    title: "Autonomous repair",
    body: "Describe a bug and DevPilot proposes a fix, applies it as a structured patch, runs the tests in a sandbox, and retries until they pass.",
  },
  {
    icon: "🧪",
    title: "Evaluated, not vibes",
    body: "Measured on a 40-case benchmark: retrieval Recall@5 and repair success are tracked across prompt versions (baseline 33% → 97%).",
  },
  {
    icon: "🔌",
    title: "Provider-agnostic",
    body: "Runs on Google Gemini or a local Ollama model behind one interface — swap the backend without touching the agent, RAG, or eval.",
  },
  {
    icon: "🔑",
    title: "Bring your own key",
    body: "Paste your own free Gemini key — it stays in your browser and is used only for your requests, so the shared demo quota never runs out.",
  },
];

function Features() {
  return (
    <div className="features">
      {FEATURES.map((f) => (
        <div className="feature" key={f.title}>
          <div className="feature-icon">{f.icon}</div>
          <div className="feature-title">{f.title}</div>
          <div className="feature-body">{f.body}</div>
        </div>
      ))}
    </div>
  );
}

function AuthScreen({ onAuthed }: { onAuthed: (email: string) => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit() {
    setError("");
    setBusy(true);
    try {
      const fn = mode === "login" ? login : register;
      const res = await fn(email.trim().toLowerCase(), password);
      setToken(res.token);
      onAuthed(res.email);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app">
      <div className="title">🧭 DevPilot</div>
      <div className="subtitle">
        An AI software-engineering agent — ingest a repo, then ask it anything or have it
        fix bugs. Grounded in the code it actually read.
      </div>

      <div className="panel auth-panel">
        <div className="tabs">
          <button
            className={mode === "login" ? "tab active" : "tab"}
            onClick={() => { setMode("login"); setError(""); }}
          >
            Log in
          </button>
          <button
            className={mode === "register" ? "tab active" : "tab"}
            onClick={() => { setMode("register"); setError(""); }}
          >
            Create account
          </button>
        </div>

        <label>Email</label>
        <input
          type="email"
          autoComplete="email"
          placeholder="you@example.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <div style={{ height: 12 }} />
        <label>Password {mode === "register" && <span className="muted">(min 8 characters)</span>}</label>
        <input
          type="password"
          autoComplete={mode === "login" ? "current-password" : "new-password"}
          placeholder="••••••••"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && email && password && submit()}
        />
        <div style={{ height: 14 }} />
        <button onClick={submit} disabled={busy || !email.trim() || password.length < 8}>
          {busy ? "Please wait…" : mode === "login" ? "Log in" : "Create account"}
        </button>
        {error && <div className="error">{error}</div>}
      </div>

      <div className="section-label">What DevPilot does</div>
      <Features />
    </div>
  );
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [email, setEmail] = useState("");
  const [authed, setAuthed] = useState(!!getToken());

  const [repos, setRepos] = useState<Record<string, RepoMeta>>({});
  const [source, setSource] = useState("");
  const [repoId, setRepoId] = useState("");
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<AskResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // API-key panel state
  const [keyInput, setKeyInput] = useState("");
  const [keySaved, setKeySaved] = useState(!!getApiKey());

  useEffect(() => {
    getHealth().then(setHealth).catch(() => setHealth(null));
  }, []);

  const authRequired = health?.auth_enabled ?? false;
  const showApp = !authRequired || authed;

  const refresh = () => listRepos().then(setRepos).catch(() => {});
  useEffect(() => {
    if (showApp) refresh();
  }, [showApp]);

  function handleAuthError(e: unknown) {
    if (e instanceof AuthError) {
      setAuthed(false);
      setError("Your session ended — please log in again.");
    } else {
      setError((e as Error).message);
    }
  }

  function logout() {
    clearToken();
    setAuthed(false);
    setResult(null);
    setRepos({});
  }

  function saveKey() {
    setApiKey(keyInput);
    setKeySaved(!!keyInput.trim());
    setKeyInput("");
  }
  function removeKey() {
    clearApiKey();
    setKeySaved(false);
  }

  async function onIngest() {
    setError(""); setBusy(true);
    try {
      const meta = await ingestRepo(source.trim());
      await refresh();
      setRepoId(meta.repo_id);
      setSource("");
    } catch (e) { handleAuthError(e); }
    finally { setBusy(false); }
  }

  async function onAsk() {
    if (!repoId || !question.trim()) return;
    setError(""); setBusy(true); setResult(null);
    try {
      setResult(await ask(repoId, question.trim()));
    } catch (e) { handleAuthError(e); }
    finally { setBusy(false); }
  }

  const examples = [
    "Give me a high-level overview of this codebase.",
    "Where is authentication handled and how?",
    "What happens when a request hits the main entry point?",
  ];

  if (health && authRequired && !authed) {
    return <AuthScreen onAuthed={(em) => { setEmail(em); setAuthed(true); }} />;
  }

  return (
    <div className="app">
      <div className="topbar">
        <div>
          <div className="title">🧭 DevPilot</div>
          <div className="subtitle">
            AI software-engineering agent — ingest a repo, then ask it anything. Answers are
            grounded in code the agent actually read.
          </div>
        </div>
        {authRequired && authed && (
          <div className="account">
            <span className="muted">{email || "signed in"}</span>
            <button className="ghost" onClick={logout}>Log out</button>
          </div>
        )}
      </div>

      {/* API key (BYOK) */}
      <div className="panel">
        <label>
          Your Gemini API key{" "}
          <span className="muted">— optional, stays in your browser</span>
        </label>
        <div className="row">
          <input
            type="password"
            placeholder={keySaved ? "•••••••• (saved in this browser)" : "AIza…  paste your own key"}
            value={keyInput}
            onChange={(e) => setKeyInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && keyInput.trim() && saveKey()}
          />
          <button onClick={saveKey} disabled={!keyInput.trim()}>Save</button>
          {keySaved && <button className="ghost" onClick={removeKey}>Clear</button>}
        </div>
        <div className="muted" style={{ marginTop: 8 }}>
          {keySaved
            ? "Using your key — requests spend your own free quota, not the shared demo's."
            : "No key set — using the server's shared key (limited free quota). "}
          Get a free key at{" "}
          <a href="https://aistudio.google.com/app/apikey" target="_blank" rel="noreferrer">
            aistudio.google.com/app/apikey
          </a>. It's sent only with your requests and never stored on our server.
        </div>
      </div>

      {/* Ingest */}
      <div className="panel">
        <label>Ingest a repository (GitHub URL or local path)</label>
        <div className="row">
          <input
            placeholder="https://github.com/pallets/flask  ·  or  ·  ./some/local/repo"
            value={source}
            onChange={(e) => setSource(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && source.trim() && onIngest()}
          />
          <button onClick={onIngest} disabled={busy || !source.trim()}>
            {busy ? "Indexing…" : "Ingest"}
          </button>
        </div>
        <div className="muted" style={{ marginTop: 8 }}>
          Indexing embeds the code into the vector store — larger repos take longer.
        </div>
      </div>

      {/* Ask */}
      <div className="panel">
        <label>Repository</label>
        <select value={repoId} onChange={(e) => setRepoId(e.target.value)}>
          <option value="">Select an ingested repo…</option>
          {Object.values(repos).map((r) => (
            <option key={r.repo_id} value={r.repo_id}>
              {r.repo_id} · {r.files} files, {r.chunks} chunks
            </option>
          ))}
        </select>

        <div style={{ height: 12 }} />
        <label>Question</label>
        <textarea
          rows={3}
          placeholder="Why does the login endpoint return 401 with valid credentials?"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
        />
        <div style={{ margin: "8px 0" }}>
          {examples.map((ex) => (
            <span className="pill" key={ex} onClick={() => setQuestion(ex)}>{ex}</span>
          ))}
        </div>
        <button onClick={onAsk} disabled={busy || !repoId || !question.trim()}>
          {busy ? "Thinking…" : "Ask DevPilot"}
        </button>
        {error && <div className="error">{error}</div>}
      </div>

      {/* Result */}
      {result && (
        <div className="panel">
          <div className="answer">{result.answer}</div>
          <div className="tools">
            <div className="muted" style={{ marginBottom: 8 }}>
              Agent took {result.steps} tool call{result.steps === 1 ? "" : "s"}:
            </div>
            {result.tool_calls.map((tc, i) => (
              <div className="tool" key={i}>
                {tc.name}({JSON.stringify(tc.args)})
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="section-label">What DevPilot does</div>
      <Features />
    </div>
  );
}
