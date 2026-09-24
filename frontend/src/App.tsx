import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  ask,
  AuthError,
  clearApiKey,
  clearToken,
  deleteRepo,
  getApiKey,
  getHealth,
  getMessages,
  getToken,
  ingestRepo,
  listRepos,
  login,
  register,
  setApiKey,
  setToken,
  validateKey,
  type Health,
  type RepoMeta,
} from "./api";
import {
  BrandMark,
  IconBars,
  IconBot,
  IconChat,
  IconCheck,
  IconCheckSmall,
  IconHelp,
  IconKey,
  IconLogOut,
  IconMenu,
  IconMoon,
  IconNetwork,
  IconPlus,
  IconSearch,
  IconSend,
  IconSun,
  IconTrash,
  IconXSmall,
} from "./icons";

const FEATURES = [
  { Icon: IconSearch, title: "Code-aware RAG", body: "Indexes your repo with hybrid semantic + keyword search." },
  { Icon: IconChat, title: "Grounded Q&A", body: "Reads real files and shows exactly which tools it used." },
  { Icon: IconCheck, title: "Autonomous repair", body: "Patches bugs and verifies the fix with real test runs." },
  { Icon: IconBars, title: "Evaluated, not vibes", body: "97% repair success, measured on a 40-case benchmark." },
  { Icon: IconNetwork, title: "Provider-agnostic", body: "Runs on Gemini or a local Ollama model." },
  { Icon: IconKey, title: "Bring your own key", body: "Use your own free key — the demo quota never runs out." },
];

const EXAMPLES = [
  "Give me a high-level overview of this codebase.",
  "Where is authentication handled and how?",
  "What happens when a request hits the main entry point?",
];

function describeError(e: unknown): string {
  if (e instanceof AuthError) return "Your session ended — please log in again.";
  if (e instanceof TypeError) {
    // fetch() throws a bare TypeError (not an HTTP error) when the connection
    // itself failed — e.g. a host request-duration limit killing a long
    // request. There's no server response to show.
    return (
      "Network error — the request was interrupted before it finished. " +
      "Large repos (or free-tier rate-limit backoffs) can take a while and may " +
      "exceed the hosting timeout; try again, a smaller repo, or your own API key."
    );
  }
  return (e as Error).message || "Something went wrong.";
}

function initials(name: string, email: string): string {
  const src = (name || "").trim() || email || "?";
  const parts = src.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return src.slice(0, 2).toUpperCase();
}

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
          <li><span>Paste a GitHub URL (or a local path) and hit <b>Add repo</b> — it indexes the repo for retrieval.</span></li>
          <li><span>Pick that repo from the sidebar, then either ask a question or describe a bug.</span></li>
          <li><span>Get a grounded answer citing real files — or a patch, verified by running the actual tests.</span></li>
        </ol>

        <h4>What it can do</h4>
        <ul className="modal-list good">
          <li>Answer questions about a codebase, grounded in files it actually retrieved and read.</li>
          <li>Propose a fix, apply it as a patch, and run the repo's tests to verify it.</li>
          <li>Work with your own Gemini key so you're never limited by the shared demo quota.</li>
          <li>Remember every repo and conversation — come back later and pick up where you left off.</li>
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

interface ChatMessage {
  role: "user" | "assistant" | "error";
  content: string;
  toolNames?: string[];
}

function ChatBubble({ msg }: { msg: ChatMessage }) {
  if (msg.role === "user") {
    return (
      <div className="bubble-row user">
        <div className="bubble user">{msg.content}</div>
      </div>
    );
  }
  return (
    <div className="bubble-row assistant">
      <div className={msg.role === "error" ? "bubble error" : "bubble assistant"}>
        <ReactMarkdown>{msg.content}</ReactMarkdown>
        {!!msg.toolNames?.length && (
          <div className="bubble-tools">🔧 {msg.toolNames.join(", ")}</div>
        )}
      </div>
    </div>
  );
}

function TypingBubble() {
  return (
    <div className="bubble-row assistant">
      <div className="bubble assistant typing">
        <span className="typing-dots"><span /><span /><span /></span>
      </div>
    </div>
  );
}

function AuthScreen({ onAuthed }: { onAuthed: (email: string, name: string) => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [showHelp, setShowHelp] = useState(false);

  const ready = mode === "login"
    ? !!email.trim() && password.length >= 8
    : !!name.trim() && !!email.trim() && password.length >= 8;

  async function submit() {
    if (!ready) return;
    setError("");
    setBusy(true);
    try {
      const res = mode === "login"
        ? await login(email.trim().toLowerCase(), password)
        : await register(email.trim().toLowerCase(), password, name.trim());
      setToken(res.token);
      onAuthed(res.email, res.name);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app wide">
      <div className="topbar" style={{ marginBottom: 0, justifyContent: "flex-end" }}>
        <div className="corner-controls">
          <button className="icon-btn" onClick={() => setShowHelp(true)} title="How DevPilot works">
            <IconHelp />
          </button>
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

            {mode === "register" && (
              <>
                <label>Name</label>
                <input
                  autoComplete="name"
                  placeholder="Ada Lovelace"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
                <div style={{ height: 14 }} />
              </>
            )}

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
              onKeyDown={(e) => e.key === "Enter" && ready && submit()}
            />
            <div style={{ height: 18 }} />
            <button style={{ width: "100%" }} onClick={submit} disabled={busy || !ready}>
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

/** Error details (network messages, raw exception text) rarely end in
 * punctuation — ensure one so it doesn't run into the following sentence. */
function withPeriod(s: string): string {
  const t = s.trim();
  return t && !/[.!?]$/.test(t) ? `${t}.` : t;
}

type KeyStatus = "unknown" | "checking" | "valid" | "invalid";

function ApiKeyPanel({ onClose, onKeyChange }: { onClose: () => void; onKeyChange: (has: boolean) => void }) {
  const [keyInput, setKeyInput] = useState("");
  const [keySaved, setKeySaved] = useState(!!getApiKey());
  const [status, setStatus] = useState<KeyStatus>("unknown");
  const [detail, setDetail] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [onClose]);

  async function saveAndTest() {
    const k = keyInput.trim();
    if (!k) return;
    setApiKey(k);
    setKeySaved(true);
    onKeyChange(true);
    setKeyInput("");
    setStatus("checking"); setDetail("");
    try {
      const r = await validateKey(k);
      setStatus(r.valid ? "valid" : "invalid");
      setDetail(r.valid ? "" : (r.detail || "Key rejected."));
    } catch (e) {
      setStatus("invalid");
      setDetail((e as Error).message || "Could not verify the key.");
    }
  }
  function removeKey() {
    clearApiKey();
    setKeySaved(false);
    onKeyChange(false);
    setStatus("unknown"); setDetail("");
  }

  return (
    <div className="popover popover-up" ref={ref}>
      <div className="popover-head">
        <IconKey width={15} height={15} />
        <label style={{ margin: 0 }}>Your Gemini API key <span className="muted">— optional</span></label>
      </div>
      <div className="row" style={{ marginTop: 10 }}>
        <input
          type="password"
          placeholder={keySaved ? "•••••••• (saved)" : "AIza… paste your own key"}
          value={keyInput}
          onChange={(e) => setKeyInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && keyInput.trim() && saveAndTest()}
        />
      </div>
      <div className="row" style={{ marginTop: 8 }}>
        <button onClick={saveAndTest} disabled={!keyInput.trim() || status === "checking"} style={{ flex: 1 }}>
          {status === "checking" ? "Testing…" : "Save & Test"}
        </button>
        {keySaved && <button className="ghost" onClick={removeKey}>Clear</button>}
      </div>
      <div className="key-status">
        {status === "valid" && <IconCheckSmall width={14} height={14} className="status-icon good" />}
        {status === "invalid" && <IconXSmall width={14} height={14} className="status-icon bad" />}
        {(status === "unknown" || status === "checking") && (
          <span className={status === "checking" ? "dot checking" : keySaved ? "dot on" : "dot off"} />
        )}
        <span>
          {status === "checking" && "Checking with Gemini…"}
          {status === "valid" && "Valid — spends your own free quota, not the shared demo's."}
          {status === "invalid" && withPeriod(detail || "This key was rejected by Gemini.")}
          {status === "unknown" && (keySaved
            ? "Saved. Using it for requests — click Save & Test again to re-verify."
            : "Using the server's shared key (limited free quota).")}
          {" "}Stored only in this browser. Get one free at{" "}
          <a href="https://aistudio.google.com/app/apikey" target="_blank" rel="noreferrer">
            aistudio.google.com/app/apikey
          </a>.
        </span>
      </div>
    </div>
  );
}

function EmptyState({ source, setSource, ingestBusy, onIngest, error }: {
  source: string;
  setSource: (v: string) => void;
  ingestBusy: boolean;
  onIngest: () => void;
  error: string;
}) {
  return (
    <div className="empty-state">
      <IconBot width={40} height={40} />
      <div className="empty-title">Where should we begin?</div>
      <div className="muted" style={{ marginBottom: 20, textAlign: "center", maxWidth: 380 }}>
        Add a repo — a GitHub URL or a local path — and DevPilot will index it so you can ask
        questions or have it fix a bug.
      </div>
      <div className="empty-ingest">
        <input
          placeholder="https://github.com/pallets/flask  ·  or  ·  ./some/local/repo"
          value={source}
          onChange={(e) => setSource(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && source.trim() && onIngest()}
        />
        <button onClick={onIngest} disabled={ingestBusy || !source.trim()}>
          {ingestBusy ? "Indexing…" : "Add repo"}
        </button>
      </div>
      {error && <div className="error" style={{ maxWidth: 420 }}>{error}</div>}
    </div>
  );
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [authed, setAuthed] = useState(!!getToken());

  const [repos, setRepos] = useState<Record<string, RepoMeta>>({});
  const [search, setSearch] = useState("");
  const [source, setSource] = useState("");
  const [repoId, setRepoId] = useState("");
  const [draft, setDraft] = useState("");
  const [chats, setChats] = useState<Record<string, ChatMessage[]>>({});
  // Separate flags: ingest and ask are independent, unrelated requests — a
  // shared flag made "Ask DevPilot" show "Thinking…" while only Ingest was
  // running (and vice versa), which reads as a false/stuck request.
  const [ingestBusy, setIngestBusy] = useState(false);
  const [askBusy, setAskBusy] = useState(false);
  const [error, setError] = useState("");
  const [showHelp, setShowHelp] = useState(false);
  const [showKeyPanel, setShowKeyPanel] = useState(false);
  const [hasKey, setHasKey] = useState(!!getApiKey());
  const [confirmDeleteId, setConfirmDeleteId] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const chatWindowRef = useRef<HTMLDivElement>(null);

  const messages = chats[repoId] ?? [];
  function appendMessage(id: string, msg: ChatMessage) {
    setChats((c) => ({ ...c, [id]: [...(c[id] ?? []), msg] }));
  }

  // Load a repo's prior conversation the first time it's selected in this
  // session. Tracked in a ref (not the chats object) so this effect only
  // depends on repoId — otherwise it'd re-run on every new message, since
  // chats gets a new reference each time appendMessage fires.
  const fetchedHistoryFor = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (!repoId || fetchedHistoryFor.current.has(repoId)) return;
    fetchedHistoryFor.current.add(repoId);
    getMessages(repoId)
      .then((stored) => {
        if (!stored.length) return;
        setChats((c) => ({
          ...c,
          [repoId]: stored.map((m) => ({
            role: m.role,
            content: m.content,
            toolNames: m.tool_names,
          })),
        }));
      })
      .catch(() => { /* best-effort — an empty chat is a fine fallback */ });
  }, [repoId]);

  useEffect(() => {
    const el = chatWindowRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length, askBusy]);

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
    if (e instanceof AuthError) setAuthed(false);
    setError(describeError(e));
  }

  function logout() {
    clearToken();
    setAuthed(false);
    setChats({});
    setRepos({});
    setRepoId("");
  }

  async function onIngest() {
    setError(""); setIngestBusy(true);
    try {
      const meta = await ingestRepo(source.trim());
      await refresh();
      setRepoId(meta.repo_id);
      setSource("");
      setSidebarOpen(false);
    } catch (e) { handleRequestError(e); }
    finally { setIngestBusy(false); }
  }

  async function onDeleteRepo(id: string) {
    try {
      await deleteRepo(id);
    } catch (e) {
      handleRequestError(e);
      setConfirmDeleteId("");
      return;
    }
    setRepos((r) => { const next = { ...r }; delete next[id]; return next; });
    setChats((c) => { const next = { ...c }; delete next[id]; return next; });
    if (repoId === id) setRepoId("");
    setConfirmDeleteId("");
  }

  async function onSend() {
    const q = draft.trim();
    if (!repoId || !q || askBusy) return;
    const id = repoId;
    appendMessage(id, { role: "user", content: q });
    setDraft(""); setAskBusy(true);
    try {
      const res = await ask(id, q);
      appendMessage(id, {
        role: "assistant",
        content: res.answer,
        toolNames: res.tool_calls.map((tc) => tc.name),
      });
    } catch (e) {
      if (e instanceof AuthError) setAuthed(false);
      appendMessage(id, { role: "error", content: describeError(e) });
    } finally {
      setAskBusy(false);
    }
  }

  if (health && authRequired && !authed) {
    return <AuthScreen onAuthed={(em, nm) => { setEmail(em); setName(nm); setAuthed(true); }} />;
  }

  const repoList = Object.values(repos);
  const q = search.trim().toLowerCase();
  const filteredRepos = q
    ? repoList.filter((r) => r.repo_id.toLowerCase().includes(q) || r.source.toLowerCase().includes(q))
    : repoList;
  const activeRepo = repos[repoId];

  return (
    <div className="shell">
      {showHelp && <HelpModal onClose={() => setShowHelp(false)} />}

      <header className="topbar-global">
        <div className="topbar-left">
          <button className="icon-btn mobile-menu-btn" onClick={() => setSidebarOpen(true)} title="Menu">
            <IconMenu />
          </button>
          <div className="brand">
            <BrandMark size={30} />
            <Wordmark />
          </div>
        </div>
        <div className="topbar-right">
          <ThemeToggle />
          {authRequired && authed && (
            <>
              <div className="user-chip" title={email}>
                <span className="avatar">{initials(name, email)}</span>
                <span className="user-chip-name">{name || email}</span>
              </div>
              <button className="ghost" onClick={logout} title="Log out">
                <IconLogOut width={16} height={16} />
              </button>
            </>
          )}
        </div>
      </header>

      <div className="shell-body">
        {sidebarOpen && <div className="sidebar-backdrop" onClick={() => setSidebarOpen(false)} />}
        <aside className={sidebarOpen ? "sidebar open" : "sidebar"}>
          <div className="sidebar-ingest">
            <input
              placeholder="GitHub URL or local path…"
              value={source}
              onChange={(e) => setSource(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && source.trim() && onIngest()}
            />
            <button className="icon-btn on" onClick={onIngest} disabled={ingestBusy || !source.trim()} title="Add repo">
              {ingestBusy ? <span className="typing-dots small"><span /><span /><span /></span> : <IconPlus width={16} height={16} />}
            </button>
          </div>

          <div className="sidebar-search">
            <IconSearch width={15} height={15} />
            <input
              placeholder="Search repos…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>

          <div className="sidebar-list">
            {filteredRepos.length === 0 ? (
              <div className="sidebar-empty">
                {repoList.length === 0 ? "No repos yet — add one above." : "No repos match your search."}
              </div>
            ) : (
              filteredRepos.map((r) => (
                <div
                  key={r.repo_id}
                  className={r.repo_id === repoId ? "sidebar-item active" : "sidebar-item"}
                  onClick={() => { setRepoId(r.repo_id); setSidebarOpen(false); }}
                >
                  <IconChat width={16} height={16} />
                  <div className="sidebar-item-text">
                    <div className="sidebar-item-title">{r.repo_id}</div>
                    <div className="sidebar-item-sub">{r.files} files · {r.chunks} chunks</div>
                  </div>
                  {confirmDeleteId === r.repo_id ? (
                    <div className="sidebar-item-confirm" onClick={(e) => e.stopPropagation()}>
                      <button className="icon-btn danger" onClick={() => onDeleteRepo(r.repo_id)} title="Confirm delete">
                        <IconCheckSmall width={14} height={14} />
                      </button>
                      <button className="icon-btn" onClick={() => setConfirmDeleteId("")} title="Cancel">
                        <IconXSmall width={14} height={14} />
                      </button>
                    </div>
                  ) : (
                    <button
                      className="sidebar-item-delete"
                      onClick={(e) => { e.stopPropagation(); setConfirmDeleteId(r.repo_id); }}
                      title="Delete this repo"
                    >
                      <IconTrash width={15} height={15} />
                    </button>
                  )}
                </div>
              ))
            )}
          </div>

          <div className="sidebar-footer">
            <button className="sidebar-footer-btn" onClick={() => setShowHelp(true)}>
              <IconHelp width={16} height={16} /> Help
            </button>
            <div className="settings-wrap">
              <button className="sidebar-footer-btn" onClick={() => setShowKeyPanel((v) => !v)}>
                <IconKey width={16} height={16} /> API key
                <span className={hasKey ? "dot on" : "dot off"} style={{ marginLeft: "auto" }} />
              </button>
              {showKeyPanel && <ApiKeyPanel onClose={() => setShowKeyPanel(false)} onKeyChange={setHasKey} />}
            </div>
          </div>
        </aside>

        <main className="main-content">
          {!repoId ? (
            <EmptyState source={source} setSource={setSource} ingestBusy={ingestBusy} onIngest={onIngest} error={error} />
          ) : (
            <div className="chat-panel main-chat">
              <div className="chat-header">
                <IconChat width={16} height={16} />
                <div className="chat-header-title">{activeRepo?.repo_id ?? repoId}</div>
                <div className="muted chat-header-sub">{activeRepo?.source}</div>
              </div>

              <div className="chat-window" ref={chatWindowRef}>
                {messages.length === 0 ? (
                  <div className="chat-empty">
                    <IconBot width={26} height={26} />
                    <div>Ask anything about this repo.</div>
                    <div className="chat-suggestions">
                      {EXAMPLES.map((ex) => (
                        <span className="pill" key={ex} onClick={() => setDraft(ex)}>{ex}</span>
                      ))}
                    </div>
                  </div>
                ) : (
                  messages.map((m, i) => <ChatBubble key={i} msg={m} />)
                )}
                {askBusy && <TypingBubble />}
              </div>

              <div className="chat-input-row">
                <textarea
                  rows={1}
                  placeholder="Message DevPilot…"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onSend(); }
                  }}
                />
                <button className="send-btn" onClick={onSend} disabled={askBusy || !draft.trim()} title="Send">
                  <IconSend width={16} height={16} />
                </button>
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
