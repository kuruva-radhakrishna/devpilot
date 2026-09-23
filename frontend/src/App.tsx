import { useEffect, useRef, useState } from "react";
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
import {
  BrandMark,
  IconBars,
  IconChat,
  IconCheck,
  IconHelp,
  IconKey,
  IconLogOut,
  IconMoon,
  IconNetwork,
  IconSearch,
  IconSettings,
  IconSun,
} from "./icons";

const FEATURES = [
  { Icon: IconSearch, title: "Code-aware RAG", body: "Indexes your repo with hybrid semantic + keyword search." },
  { Icon: IconChat, title: "Grounded Q&A", body: "Reads real files and shows exactly which tools it used." },
  { Icon: IconCheck, title: "Autonomous repair", body: "Patches bugs and verifies the fix with real test runs." },
  { Icon: IconBars, title: "Evaluated, not vibes", body: "97% repair success, measured on a 40-case benchmark." },
  { Icon: IconNetwork, title: "Provider-agnostic", body: "Runs on Gemini or a local Ollama model." },
  { Icon: IconKey, title: "Bring your own key", body: "Use your own free key — the demo quota never runs out." },
];

function Wordmark() {
  return (
    <span className="wordmark">
      <span className="dim">Dev</span><span className="pop">Pilot</span>
    </span>
  );
}

function ThemeToggle() {
  const [theme, setTheme] = useState(
    () => document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark"
  );
  function toggle() {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("devpilot_theme", next); } catch { /* ignore */ }
  }
  return (
    <button className="icon-btn" onClick={toggle} title="Toggle theme">
      {theme === "dark" ? <IconSun /> : <IconMoon />}
    </button>
  );
}

function HelpButton({ onClick }: { onClick: () => void }) {
  return (
    <button className="icon-btn" onClick={onClick} title="How DevPilot works">
      <IconHelp />
    </button>
  );
}

function HelpModal({ onClose }: { onClose: () => void }) {
  useEffect(() => {
    function onKey(e: KeyboardEvent) { if (e.key === "Escape") onClose(); }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>How DevPilot works</h2>
          <button className="icon-btn" onClick={onClose} title="Close">✕</button>
        </div>
        <div className="muted">An AI agent that reads, debugs, and repairs a real codebase.</div>

        <h4>Quick start</h4>
        <ol className="modal-steps">
          <li><span>Paste a GitHub URL (or a local path) and hit <b>Ingest</b> — it indexes the repo for retrieval.</span></li>
          <li><span>Pick that repo, then either ask a question or describe a bug.</span></li>
          <li><span>Get a grounded answer citing real files — or a patch, verified by running the actual tests.</span></li>
        </ol>

        <h4>What it can do</h4>
        <ul className="modal-list good">
          <li>Answer questions about a codebase, grounded in files it actually retrieved and read.</li>
          <li>Propose a fix, apply it as a patch, and run the repo's tests to verify it.</li>
          <li>Work with your own Gemini key so you're never limited by the shared demo quota.</li>
        </ul>

        <h4>What it can't do (yet)</h4>
        <ul className="modal-list bad">
          <li>Push, deploy, or open a PR — patches are proposed, not shipped anywhere.</li>
          <li>Guarantee a fix on the first try — repair retries automatically, but isn't 100%.</li>
          <li>Run tests in a fully isolated sandbox on this hosted demo — stick to trusted repos.</li>
        </ul>

        <div className="modal-foot">
          Source, benchmark results, and the write-up:{" "}
          <a href="https://github.com/kuruva-radhakrishna/devpilot" target="_blank" rel="noreferrer">
            github.com/kuruva-radhakrishna/devpilot
          </a>
        </div>
      </div>
    </div>
  );
}

function FeatureList() {
  return (
    <>
      {FEATURES.map((f) => (
        <div className="feature-row" key={f.title}>
          <div className="feature-icon"><f.Icon width={17} height={17} /></div>
          <div>
            <div className="feature-title">{f.title}</div>
            <div className="feature-body">{f.body}</div>
          </div>
        </div>
      ))}
    </>
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

  const [showHelp, setShowHelp] = useState(false);

  return (
    <div className="app wide">
      <div className="topbar" style={{ marginBottom: 0, justifyContent: "flex-end" }}>
        <div className="corner-controls">
          <HelpButton onClick={() => setShowHelp(true)} />
          <ThemeToggle />
        </div>
      </div>
      {showHelp && <HelpModal onClose={() => setShowHelp(false)} />}
      <div className="auth-wrap">
        <div className="brand" style={{ marginBottom: 10 }}>
          <BrandMark />
          <Wordmark />
        </div>
        <div className="tagline" style={{ marginBottom: 28 }}>
          Reads your repo, debugs it, and ships the fix.
        </div>

        <div className="auth-card">
          <div className="auth-form-side">
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
            <div style={{ height: 14 }} />
            <label>Password {mode === "register" && <span className="muted">· min 8 characters</span>}</label>
            <input
              type="password"
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              placeholder="••••••••"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && email && password && submit()}
            />
            <div style={{ height: 18 }} />
            <button style={{ width: "100%" }} onClick={submit} disabled={busy || !email.trim() || password.length < 8}>
              {busy ? "Please wait…" : mode === "login" ? "Log in" : "Create account"}
            </button>
            {error && <div className="error">{error}</div>}
          </div>

          <div className="auth-feature-side">
            <h3>Why DevPilot</h3>
            <FeatureList />
          </div>
        </div>
      </div>
    </div>
  );
}

function SettingsMenu() {
  const [open, setOpen] = useState(false);
  const [keyInput, setKeyInput] = useState("");
  const [keySaved, setKeySaved] = useState(!!getApiKey());
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  function saveKey() {
    setApiKey(keyInput);
    setKeySaved(!!keyInput.trim());
    setKeyInput("");
  }
  function removeKey() {
    clearApiKey();
    setKeySaved(false);
  }

  return (
    <div className="settings-wrap" ref={ref}>
      <button
        className={open || keySaved ? "icon-btn on" : "icon-btn"}
        onClick={() => setOpen((v) => !v)}
        title="Gemini API key settings"
      >
        <IconSettings />
      </button>
      {open && (
        <div className="settings-drop">
          <div className="settings-drop-head">
            <IconKey width={15} height={15} />
            <label style={{ margin: 0 }}>Your Gemini API key <span className="muted">— optional</span></label>
          </div>
          <div className="row" style={{ marginTop: 10 }}>
            <input
              type="password"
              placeholder={keySaved ? "•••••••• (saved)" : "AIza… paste your own key"}
              value={keyInput}
              onChange={(e) => setKeyInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && keyInput.trim() && saveKey()}
            />
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            <button onClick={saveKey} disabled={!keyInput.trim()} style={{ flex: 1 }}>Save</button>
            {keySaved && <button className="ghost" onClick={removeKey}>Clear</button>}
          </div>
          <div className="key-status">
            <span className={keySaved ? "dot on" : "dot off"} />
            <span>
              {keySaved
                ? "Using your key — spends your own free quota, not the shared demo's."
                : "Using the server's shared key (limited free quota)."}{" "}
              Stored only in this browser. Get one free at{" "}
              <a href="https://aistudio.google.com/app/apikey" target="_blank" rel="noreferrer">
                aistudio.google.com/app/apikey
              </a>.
            </span>
          </div>
        </div>
      )}
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
  // Separate flags: ingest and ask are independent, unrelated requests — a
  // shared flag made "Ask DevPilot" show "Thinking…" while only Ingest was
  // running (and vice versa), which reads as a false/stuck request.
  const [ingestBusy, setIngestBusy] = useState(false);
  const [askBusy, setAskBusy] = useState(false);
  const [error, setError] = useState("");
  const [showHelp, setShowHelp] = useState(false);

  useEffect(() => {
    getHealth().then(setHealth).catch(() => setHealth(null));
  }, []);

  const authRequired = health?.auth_enabled ?? false;
  const showApp = !authRequired || authed;

  const refresh = () => listRepos().then(setRepos).catch(() => {});
  useEffect(() => {
    if (!showApp) return;
    refresh();
    // First-ever visit to the working app: open Help once, unprompted.
    try {
      if (!localStorage.getItem("devpilot_seen_help")) {
        setShowHelp(true);
        localStorage.setItem("devpilot_seen_help", "1");
      }
    } catch { /* ignore */ }
  }, [showApp]);

  function handleRequestError(e: unknown) {
    if (e instanceof AuthError) {
      setAuthed(false);
      setError("Your session ended — please log in again.");
    } else if (e instanceof TypeError) {
      // fetch() throws a bare TypeError (not an HTTP error) when the
      // connection itself failed — e.g. a host request-duration limit
      // killing a long-running ingest. There's no server response to show.
      setError(
        "Network error — the request was interrupted before it finished. " +
        "Large repos (or free-tier rate-limit backoffs) can take a while and may " +
        "exceed the hosting timeout; try a smaller repo, or your own API key, or try again."
      );
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

  async function onIngest() {
    setError(""); setIngestBusy(true);
    try {
      const meta = await ingestRepo(source.trim());
      await refresh();
      setRepoId(meta.repo_id);
      setSource("");
    } catch (e) { handleRequestError(e); }
    finally { setIngestBusy(false); }
  }

  async function onAsk() {
    if (!repoId || !question.trim()) return;
    setError(""); setAskBusy(true); setResult(null);
    try {
      setResult(await ask(repoId, question.trim()));
    } catch (e) { handleRequestError(e); }
    finally { setAskBusy(false); }
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
      {showHelp && <HelpModal onClose={() => setShowHelp(false)} />}
      <div className="topbar">
        <div className="brand">
          <BrandMark size={32} />
          <Wordmark />
        </div>
        <div className="account">
          <HelpButton onClick={() => setShowHelp(true)} />
          <ThemeToggle />
          <SettingsMenu />
          {authRequired && authed && (
            <>
              <span className="account-email" title={email}>{email || "signed in"}</span>
              <button className="ghost" onClick={logout} title="Log out">
                <IconLogOut width={16} height={16} />
              </button>
            </>
          )}
        </div>
      </div>

      {/* Step 1: Ingest */}
      <div className="panel step">
        <div className="step-badge">1</div>
        <label>Ingest a repository (GitHub URL or local path)</label>
        <div className="row">
          <input
            placeholder="https://github.com/pallets/flask  ·  or  ·  ./some/local/repo"
            value={source}
            onChange={(e) => setSource(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && source.trim() && onIngest()}
          />
          <button onClick={onIngest} disabled={ingestBusy || !source.trim()}>
            {ingestBusy ? "Indexing…" : "Ingest"}
          </button>
        </div>
        <div className="muted" style={{ marginTop: 8 }}>
          Indexing embeds the code into the vector store — larger repos take longer.
        </div>
      </div>

      {/* Step 2: Ask */}
      <div className="panel step">
        <div className="step-badge">2</div>
        <label>Repository</label>
        <select value={repoId} onChange={(e) => setRepoId(e.target.value)}>
          <option value="">Select an ingested repo…</option>
          {Object.values(repos).map((r) => (
            <option key={r.repo_id} value={r.repo_id}>
              {r.repo_id} · {r.files} files, {r.chunks} chunks
            </option>
          ))}
        </select>

        <div style={{ height: 14 }} />
        <label>Question</label>
        <textarea
          rows={3}
          placeholder="Why does the login endpoint return 401 with valid credentials?"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
        />
        <div style={{ margin: "10px 0" }}>
          {examples.map((ex) => (
            <span className="pill" key={ex} onClick={() => setQuestion(ex)}>{ex}</span>
          ))}
        </div>
        <button onClick={onAsk} disabled={askBusy || !repoId || !question.trim()}>
          {askBusy ? "Thinking…" : "Ask DevPilot"}
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
    </div>
  );
}
