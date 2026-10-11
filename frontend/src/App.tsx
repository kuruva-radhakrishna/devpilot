import { isValidElement, useEffect, useRef, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import {
  ask,
  AuthError,
  clearToken,
  deleteRepo,
  renameRepo,
  getApiKey,
  getHealth,
  getMe,
  getMessages,
  getRepairJob,
  getToken,
  ingestRepo,
  listRepos,
  startRepair,
  warmRunner,
  type Health,
  type RepairEvent,
  type RepoMeta,
} from "./api";
import AccountModal from "./components/AccountModal";
import ApiKeyPanel from "./components/ApiKeyPanel";
import AuthScreen from "./components/AuthScreen";
import HelpModal from "./components/HelpModal";
import RepairProgress from "./components/RepairProgress";
import ThemeSwitch from "./components/ThemeSwitch";
import {
  BrandMark,
  IconAlert,
  IconArrowRight,
  IconBot,
  IconChat,
  IconCheckSmall,
  IconCopy,
  IconEdit,
  IconHelp,
  IconKey,
  IconLogOut,
  IconMenu,
  IconPlus,
  IconSearch,
  IconSend,
  IconTrash,
  IconWrench,
  IconXSmall,
} from "./icons";
import { describeError, initials } from "./lib";
import { SAMPLE_BUGS, type SampleBug } from "./samples";

const JOB_KEY = "devpilot_repair_job";

const EXAMPLES = [
  "Give me a high-level overview of this codebase.",
  "Where is authentication handled, and how?",
  "What happens when a request hits the main entry point?",
  "Which files would I change to add a new feature?",
];

const SAMPLE_REPOS = [
  { name: "pallets/click", url: "https://github.com/pallets/click", note: "Python command-line toolkit" },
  { name: "sindresorhus/p-limit", url: "https://github.com/sindresorhus/p-limit", note: "Tiny JavaScript library, quick to index" },
  { name: "kuruva-radhakrishna/Talent-Flow", url: "https://github.com/kuruva-radhakrishna/Talent-Flow", note: "A full-stack React app" },
];

/** Plain text of a React subtree (for the code-block copy button). */
function textOf(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (isValidElement(node)) return textOf((node.props as { children?: ReactNode }).children);
  return "";
}

function CodeBlock({ children }: { children?: ReactNode }) {
  const [copied, setCopied] = useState(false);
  const text = textOf(children).replace(/\n$/, "");
  // react-markdown renders ```diff as <pre><code class="language-diff">.
  const className = isValidElement(children) ? (children.props as { className?: string }).className ?? "" : "";
  const isDiff = /\blanguage-diff\b/.test(className);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch { /* clipboard can be blocked */ }
  }
  return (
    <div className="codeblock">
      <button type="button" className="code-copy" onClick={copy} title={isDiff ? "Copy patch" : "Copy code"}>
        {copied ? <><IconCheckSmall width={13} height={13} /> Copied</> : <><IconCopy width={13} height={13} /> Copy</>}
      </button>
      {isDiff ? (
        <pre className="diff">
          {text.split("\n").map((line, i) => (
            <span key={i} className={
              line.startsWith("+++") || line.startsWith("---") ? "dl file" :
              line.startsWith("@@") ? "dl hunk" :
              line.startsWith("+") ? "dl add" :
              line.startsWith("-") ? "dl del" : "dl"
            }>{line || " "}</span>
          ))}
        </pre>
      ) : <pre>{children}</pre>}
    </div>
  );
}

interface ChatMessage {
  role: "user" | "assistant" | "error";
  content: string;
  toolNames?: string[];
}

const REPAIR_PREFIX = "[repair] ";

function ChatBubble({ msg }: { msg: ChatMessage }) {
  if (msg.role === "user") {
    const isFix = msg.content.startsWith(REPAIR_PREFIX);
    return (
      <div className="msg user">
        <div className="bubble user">
          {isFix && <span className="bubble-tag"><IconWrench width={12} height={12} /> Fix a bug</span>}
          {isFix ? msg.content.slice(REPAIR_PREFIX.length) : msg.content}
        </div>
      </div>
    );
  }
  return (
    <div className="msg assistant">
      <div className={msg.role === "error" ? "card answer error" : "card answer"}>
        {msg.role === "error" && <div className="answer-title"><IconAlert width={16} height={16} /> Something went wrong</div>}
        <div className="md">
          <ReactMarkdown components={{ pre: ({ children }) => <CodeBlock>{children}</CodeBlock> }}>
            {msg.content}
          </ReactMarkdown>
        </div>
        {!!msg.toolNames?.length && (
          <div className="meta">
            <span className="meta-label">Used</span>
            {[...new Set(msg.toolNames)].map((t) => <span className="pill" key={t}>{t}</span>)}
          </div>
        )}
      </div>
    </div>
  );
}

function TypingBubble() {
  return (
    <div className="msg assistant">
      <div className="card answer pending">
        <span className="spin" aria-hidden="true" />
        <span>Reading the code…</span>
      </div>
    </div>
  );
}

function EmptyState({ source, setSource, ingestBusy, onIngest, repairEnabled, repairBusy, onTrySample }: {
  source: string;
  setSource: (v: string) => void;
  ingestBusy: boolean;
  onIngest: () => void;
  repairEnabled: boolean;
  repairBusy: boolean;
  onTrySample: (s: SampleBug) => void;
}) {
  return (
    <div className="empty-state">
      <div className="empty-inner">
        <span className="hero-mark"><BrandMark size={52} /></span>
        <h1 className="hero-title">Ask your codebase anything</h1>
        <p className="hero-sub">
          Add a GitHub repo and DevPilot indexes it, then answers questions with the files it
          actually read{repairEnabled ? ", or fixes a bug and proves the fix by running the tests" : ""}.
        </p>

        {repairEnabled && (
          <>
            <h2 className="eyebrow first">Watch it fix a real bug</h2>
            <div className="examples">
              {SAMPLE_BUGS.map((s) => (
                <button type="button" className="ex" key={s.source} disabled={ingestBusy || repairBusy}
                  onClick={() => onTrySample(s)}>
                  <span>
                    <span className="ex-title">{s.title}</span>
                    <span className="ex-note">{s.note}</span>
                  </span>
                  <IconWrench className="ex-arrow" width={16} height={16} />
                </button>
              ))}
            </div>
            <div className="hint">One click: it indexes the sample, reproduces the failing test, patches it, and re-runs the tests.</div>
            <h2 className="eyebrow">Or bring your own repo</h2>
          </>
        )}

        <form className="hero-ingest" onSubmit={(e) => { e.preventDefault(); if (source.trim()) onIngest(); }}>
          <input
            aria-label="GitHub repository URL"
            placeholder="https://github.com/owner/repo"
            value={source}
            onChange={(e) => setSource(e.target.value)}
          />
          <button type="submit" className="btn-primary" disabled={ingestBusy || !source.trim()}>
            {ingestBusy ? "Indexing…" : "Add repo"}
          </button>
        </form>
        <div className="hint">Indexing takes a minute or two for a mid-size repo.</div>

        <h2 className="eyebrow">Some repos to try</h2>
        <div className="examples">
          {SAMPLE_REPOS.map((r) => (
            <button type="button" className="ex" key={r.url} onClick={() => setSource(r.url)}>
              <span>
                <span className="ex-title">{r.name}</span>
                <span className="ex-note">{r.note}</span>
              </span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

/** Shown until the server's health check answers. On a free host the first request
 * after idle can take up to a minute, so say so rather than show an empty screen. */
function Splash() {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setSlow(true), 3500);
    return () => clearTimeout(t);
  }, []);
  return (
    <div className="splash" role="status" aria-live="polite">
      <BrandMark size={52} />
      <span className="spin" aria-hidden="true" />
      <div className="splash-text">{slow ? "Waking up the server…" : "Loading…"}</div>
      {slow && (
        <div className="splash-sub">
          The free hosting plan puts the server to sleep when it's idle, so the first visit can take up to a minute.
        </div>
      )}
    </div>
  );
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [healthState, setHealthState] = useState<"loading" | "ok" | "down">("loading");
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [authed, setAuthed] = useState(!!getToken());
  const [resetToken, setResetToken] = useState(() => {
    const m = window.location.hash.match(/reset=([A-Za-z0-9_-]+)/);
    return m ? m[1] : "";
  });

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
  const [showAccount, setShowAccount] = useState(false);
  const [showKeyPanel, setShowKeyPanel] = useState(false);
  const [hasKey, setHasKey] = useState(!!getApiKey());
  const [confirmDeleteId, setConfirmDeleteId] = useState("");
  const [renamingId, setRenamingId] = useState("");
  const [renameDraft, setRenameDraft] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  // "Fix a bug" mode: a repair runs as a background job on the server; we poll it.
  const [mode, setMode] = useState<"ask" | "fix">("ask");
  const [testPath, setTestPath] = useState("");
  const [job, setJob] = useState<{ repoId: string; id: string; events: RepairEvent[]; elapsed: number } | null>(() => {
    try {
      const saved = JSON.parse(sessionStorage.getItem(JOB_KEY) ?? "null");
      return saved?.id && saved?.repoId ? { repoId: saved.repoId, id: saved.id, events: [], elapsed: 0 } : null;
    } catch { return null; }
  });
  const feedRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);

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
    const el = feedRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length, askBusy, job?.events.length]);

  // Follow a running repair until it finishes, then drop its result into the chat.
  // (The server also saves it to the history, so a reload shows the same thing.)
  const jobId = job?.id;
  useEffect(() => {
    if (!job || !job.id) return; // no id yet: still waking the runner
    const { id, repoId: jobRepo } = job;
    let finished = false;
    async function tick() {
      try {
        const snap = await getRepairJob(id);
        if (finished) return;
        if (snap.status === "running") {
          setJob((j) => (j && j.id === id ? { ...j, events: snap.events, elapsed: snap.elapsed } : j));
          return;
        }
        finished = true;
        const outcome: ChatMessage = snap.status === "done" && snap.result
          ? { role: "assistant", content: snap.result.markdown, toolNames: ["repair"] }
          : { role: "error", content: snap.error ?? "The repair failed." };
        // After a reload the saved history may already end with this very result.
        setChats((c) => {
          const list = c[jobRepo] ?? [];
          return list.length && list[list.length - 1].content === outcome.content
            ? c : { ...c, [jobRepo]: [...list, outcome] };
        });
        setJob(null);
        try { sessionStorage.removeItem(JOB_KEY); } catch { /* ignore */ }
      } catch (e) {
        if (e instanceof AuthError) { finished = true; setAuthed(false); return; }
        // The job is gone (expired, or the server restarted): stop following it.
        if (/no such repair job/i.test((e as Error).message)) {
          finished = true;
          setJob(null);
          try { sessionStorage.removeItem(JOB_KEY); } catch { /* ignore */ }
        }
        // anything else is a hiccup; the next tick retries
      }
    }
    tick();
    const timer = setInterval(tick, 1500);
    return () => { finished = true; clearInterval(timer); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  // Grow the composer with its content, up to a cap.
  useEffect(() => {
    const el = composerRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 150)}px`;
  }, [draft, repoId]);

  useEffect(() => {
    getHealth()
      .then((h) => { setHealth(h); setHealthState("ok"); })
      .catch(() => {
        setHealth(null);
        setHealthState("down");
        setError("Can't reach the DevPilot server right now. It may be restarting. Try again in a minute.");
      });
  }, []);

  const authRequired = health?.auth_enabled ?? false;
  const showApp = !authRequired || authed;
  const repairEnabled = health?.repair_enabled ?? false;
  const wakeUrl = health?.repair_wake_url;
  useEffect(() => { if (health && !repairEnabled && mode === "fix") setMode("ask"); }, [health, repairEnabled, mode]);
  // Give a sleeping free-tier runner a head start as soon as the app is usable, so it is
  // usually awake by the time someone clicks "Fix it".
  useEffect(() => {
    if (showApp && repairEnabled && wakeUrl) void warmRunner(wakeUrl);
  }, [showApp, repairEnabled, wakeUrl]);

  // Who is signed in (also proves the stored session is still valid — a password
  // change on another device or a deleted account answers 401 here).
  useEffect(() => {
    if (!authRequired || !authed) return;
    getMe()
      .then((me) => { setEmail(me.email); setName(me.name); })
      .catch((e) => { if (e instanceof AuthError) setAuthed(false); });
  }, [authRequired, authed]);

  const refresh = () => listRepos().then(setRepos).catch((e) => { if (e instanceof AuthError) setAuthed(false); });
  useEffect(() => {
    // Wait for the health check: before it answers we don't yet know whether a
    // login screen is about to replace the app, and the Help popup (and the
    // "seen" flag) would be spent on a screen that's never shown.
    if (!health || !showApp) return;
    refresh();
    // First-ever visit to the working app: open Help once, unprompted.
    try {
      if (!localStorage.getItem("devpilot_seen_help")) {
        setShowHelp(true);
        localStorage.setItem("devpilot_seen_help", "1");
      }
    } catch { /* ignore */ }
  }, [health, showApp]);

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
    setEmail(""); setName("");
    setShowAccount(false);
    setJob(null); setMode("ask"); setTestPath("");
    try { sessionStorage.removeItem(JOB_KEY); } catch { /* ignore */ }
    fetchedHistoryFor.current.clear();
  }

  function finishReset() {
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
    setResetToken("");
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

  function startRename(r: RepoMeta) {
    setRenamingId(r.repo_id);
    setRenameDraft(r.display_name ?? r.repo_id);
  }
  async function commitRename(id: string) {
    const newName = renameDraft.trim();
    setRenamingId("");
    if (!newName || newName === repos[id]?.display_name) return;
    try {
      await renameRepo(id, newName);
      setRepos((r) => ({ ...r, [id]: { ...r[id], display_name: newName } }));
    } catch (e) {
      handleRequestError(e);
    }
  }

  async function onStartFix(rId = repoId, bug = draft.trim(), path = testPath.trim()) {
    if (!rId || bug.length < 8 || job) return;
    setError("");
    appendMessage(rId, { role: "user", content: `${REPAIR_PREFIX}${bug}` });
    setDraft("");
    // Wake the runner from here, in the browser, BEFORE the job starts: a sleeping free
    // instance doesn't wake for calls from our own server. Show it as the first step.
    let ticker: ReturnType<typeof setInterval> | undefined;
    if (wakeUrl) {
      const t0 = Date.now();
      setJob({ repoId: rId, id: "", elapsed: 0, events: [
        { t: 0, stage: "warm", message: "Waking the test runner. Free hosting puts it to sleep when idle, so this can take up to a minute." }] });
      ticker = setInterval(() => setJob((j) => (j && !j.id ? { ...j, elapsed: (Date.now() - t0) / 1000 } : j)), 1000);
      await warmRunner(wakeUrl);
      clearInterval(ticker);
    }
    try {
      const started = await startRepair(rId, bug, path);
      try { sessionStorage.setItem(JOB_KEY, JSON.stringify({ repoId: rId, id: started.job_id })); } catch { /* ignore */ }
      setJob({ repoId: rId, id: started.job_id, events: [], elapsed: 0 });
    } catch (e) {
      if (ticker) clearInterval(ticker);
      setJob(null);
      if (e instanceof AuthError) setAuthed(false);
      appendMessage(rId, { role: "error", content: describeError(e) });
    }
  }

  async function onTrySample(s: SampleBug) {
    setError(""); setIngestBusy(true);
    try {
      const meta = await ingestRepo(s.source);
      await refresh();
      setRepoId(meta.repo_id);
      setMode("fix");
      setTestPath(s.testPath);
      setSidebarOpen(false);
      await onStartFix(meta.repo_id, s.bug, s.testPath);
    } catch (e) { handleRequestError(e); }
    finally { setIngestBusy(false); }
  }

  async function onSend() {
    if (mode === "fix") { await onStartFix(); return; }
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

  if (healthState === "loading") return <Splash />;

  if (health && ((authRequired && !authed) || resetToken)) {
    return (
      <AuthScreen
        health={health}
        resetToken={resetToken}
        onResetDone={finishReset}
        onAuthed={(em, nm) => { setEmail(em); setName(nm); setAuthed(true); }}
      />
    );
  }

  const repoList = Object.values(repos);
  const q = search.trim().toLowerCase();
  const filteredRepos = q
    ? repoList.filter((r) =>
        r.repo_id.toLowerCase().includes(q) ||
        r.source.toLowerCase().includes(q) ||
        (r.display_name ?? "").toLowerCase().includes(q)
      )
    : repoList;
  const activeRepo = repos[repoId];

  return (
    <div className="shell">
      {showHelp && <HelpModal onClose={() => setShowHelp(false)} />}
      {showAccount && (
        <AccountModal
          name={name}
          email={email}
          repoCount={repoList.length}
          onClose={() => setShowAccount(false)}
          onSessionEnded={logout}
          onDeleted={logout}
        />
      )}

      <header className="topbar">
        <div className="topbar-left">
          <button className="icon-btn mobile-menu-btn" onClick={() => setSidebarOpen(true)} title="Menu" aria-label="Open menu">
            <IconMenu />
          </button>
          <div className="brand">
            <BrandMark size={30} />
            <span className="wordmark">DevPilot</span>
            <span className="brand-sub">AI software-engineering agent</span>
          </div>
        </div>
        <div className="topbar-right">
          <ThemeSwitch />
          {authRequired && authed && (
            <>
              <button className="user-chip" onClick={() => setShowAccount(true)} title="Account settings">
                <span className="avatar">{initials(name, email)}</span>
                <span className="user-chip-name">{name || email}</span>
              </button>
              <button className="btn-chip" onClick={logout} title="Log out" aria-label="Log out">
                <IconLogOut width={16} height={16} />
                <span className="chip-label">Log out</span>
              </button>
            </>
          )}
        </div>
      </header>

      <div className="shell-body">
        {sidebarOpen && <div className="sidebar-backdrop" onClick={() => setSidebarOpen(false)} />}
        <aside className={sidebarOpen ? "sidebar open" : "sidebar"}>
          <h2 className="side-title">Add a repository</h2>
          <form className="sidebar-ingest" onSubmit={(e) => { e.preventDefault(); if (source.trim()) onIngest(); }}>
            <input
              aria-label="GitHub repository URL"
              placeholder="GitHub URL…"
              value={source}
              onChange={(e) => setSource(e.target.value)}
            />
            <button type="submit" className="btn-primary square" disabled={ingestBusy || !source.trim()} title="Add repo" aria-label="Add repo">
              {ingestBusy ? <span className="spin light" /> : <IconPlus width={17} height={17} />}
            </button>
          </form>

          <div className="sidebar-search">
            <IconSearch width={15} height={15} />
            <input
              aria-label="Search repositories"
              placeholder="Search repos…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>

          <h2 className="side-title">Repositories <span className="count">{repoList.length}</span></h2>
          <div className="sidebar-list">
            {filteredRepos.length === 0 ? (
              <div className="sidebar-empty">
                {repoList.length === 0 ? "No repos yet. Add one above." : "No repos match your search."}
              </div>
            ) : (
              filteredRepos.map((r) => (
                <div
                  key={r.repo_id}
                  className={r.repo_id === repoId ? "repo-item active" : "repo-item"}
                  onClick={() => {
                    if (renamingId === r.repo_id) return;
                    setRepoId(r.repo_id); setSidebarOpen(false);
                  }}
                >
                  <div className="repo-text">
                    {renamingId === r.repo_id ? (
                      <input
                        autoFocus
                        className="sidebar-rename-input"
                        value={renameDraft}
                        onClick={(e) => e.stopPropagation()}
                        onChange={(e) => setRenameDraft(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") { e.preventDefault(); commitRename(r.repo_id); }
                          if (e.key === "Escape") { e.preventDefault(); setRenamingId(""); }
                        }}
                        onBlur={() => commitRename(r.repo_id)}
                      />
                    ) : (
                      <>
                        <div className="repo-title">{r.display_name ?? r.repo_id}</div>
                        <div className="repo-sub">{r.files} files · {r.chunks} chunks</div>
                      </>
                    )}
                  </div>
                  {confirmDeleteId === r.repo_id ? (
                    <div className="repo-confirm" onClick={(e) => e.stopPropagation()}>
                      <button className="icon-btn danger" onClick={() => onDeleteRepo(r.repo_id)} title="Confirm delete" aria-label="Confirm delete">
                        <IconCheckSmall width={14} height={14} />
                      </button>
                      <button className="icon-btn" onClick={() => setConfirmDeleteId("")} title="Cancel" aria-label="Cancel">
                        <IconXSmall width={14} height={14} />
                      </button>
                    </div>
                  ) : renamingId === r.repo_id ? null : (
                    <div className="repo-actions">
                      <button
                        className="repo-action"
                        onClick={(e) => { e.stopPropagation(); startRename(r); }}
                        title="Rename" aria-label="Rename"
                      >
                        <IconEdit width={14} height={14} />
                      </button>
                      <button
                        className="repo-action danger-hover"
                        onClick={(e) => { e.stopPropagation(); setConfirmDeleteId(r.repo_id); }}
                        title="Delete this repo" aria-label="Delete this repo"
                      >
                        <IconTrash width={15} height={15} />
                      </button>
                    </div>
                  )}
                </div>
              ))
            )}
          </div>

          <div className="sidebar-footer">
            {authRequired && authed && (
              <button className="sidebar-footer-btn" onClick={() => setShowAccount(true)}>
                <span className="avatar sm">{initials(name, email)}</span> Account
              </button>
            )}
            <div className="settings-wrap">
              <button className="sidebar-footer-btn" onClick={() => setShowKeyPanel((v) => !v)}>
                <IconKey width={16} height={16} /> API key
                <span className={hasKey ? "dot on" : "dot off"} style={{ marginLeft: "auto" }} />
              </button>
              {showKeyPanel && <ApiKeyPanel onClose={() => setShowKeyPanel(false)} onKeyChange={setHasKey} />}
            </div>
            <button className="sidebar-footer-btn" onClick={() => setShowHelp(true)}>
              <IconHelp width={16} height={16} /> Help
            </button>
          </div>
        </aside>

        <main className="main-content">
          {error && (
            <div className="banner" role="alert">
              <IconAlert width={17} height={17} />
              <span>{error}</span>
              <button className="icon-btn" onClick={() => setError("")} title="Dismiss" aria-label="Dismiss">
                <IconXSmall width={14} height={14} />
              </button>
            </div>
          )}

          {!repoId ? (
            <EmptyState
              source={source} setSource={setSource} ingestBusy={ingestBusy} onIngest={onIngest}
              repairEnabled={repairEnabled} repairBusy={!!job} onTrySample={onTrySample}
            />
          ) : (
            <div className="main-chat">
              <div className="chat-header">
                <span className="chat-header-icon"><IconChat width={16} height={16} /></span>
                <div className="chat-header-text">
                  <div className="chat-header-title">{activeRepo?.display_name ?? activeRepo?.repo_id ?? repoId}</div>
                  <div className="chat-header-sub">{activeRepo?.source}</div>
                </div>
              </div>

              <div className="feed" ref={feedRef} role="log" aria-live="polite">
                <div className="wrap">
                  {messages.length === 0 ? (
                    <div className="chat-empty">
                      <span className="hero-mark small"><IconBot width={26} height={26} /></span>
                      {mode === "fix" ? (
                        <>
                          <h2 className="chat-empty-title">Describe a bug to fix</h2>
                          <p>
                            DevPilot reproduces it by running the tests, finds the code, patches it, and
                            re-runs the tests to prove the fix.
                          </p>
                        </>
                      ) : (
                        <>
                          <h2 className="chat-empty-title">Ask anything about this repo</h2>
                          <p>Answers cite the files DevPilot read. Try one of these:</p>
                          <div className="examples">
                            {EXAMPLES.map((ex) => (
                              <button type="button" className="ex" key={ex}
                                onClick={() => { setDraft(ex); composerRef.current?.focus(); }}>
                                <span className="ex-title">{ex}</span>
                                <IconArrowRight className="ex-arrow" width={16} height={16} />
                              </button>
                            ))}
                          </div>
                        </>
                      )}
                    </div>
                  ) : (
                    messages.map((m, i) => <ChatBubble key={i} msg={m} />)
                  )}
                  {job && job.repoId === repoId && <RepairProgress events={job.events} elapsed={job.elapsed} running />}
                  {askBusy && <TypingBubble />}
                </div>
              </div>

              <form className="composer" onSubmit={(e) => { e.preventDefault(); onSend(); }}>
                <div className="wrap">
                  <div className="mode-switch" role="tablist" aria-label="What do you want to do?">
                    <button type="button" role="tab" aria-selected={mode === "ask"}
                      className={mode === "ask" ? "mode active" : "mode"} onClick={() => setMode("ask")}>
                      <IconChat width={15} height={15} /> Ask
                    </button>
                    <button type="button" role="tab" aria-selected={mode === "fix"}
                      className={mode === "fix" ? "mode active" : "mode"}
                      disabled={!repairEnabled}
                      title={repairEnabled ? "Describe a bug and have DevPilot fix it" : (health?.repair_reason || "Repair isn't available here")}
                      onClick={() => setMode("fix")}>
                      <IconWrench width={15} height={15} /> Fix a bug
                    </button>
                  </div>
                  <div className="composer-box">
                    <textarea
                      ref={composerRef}
                      rows={1}
                      aria-label={mode === "fix" ? "Describe the bug" : "Your question"}
                      placeholder={
                        job ? "A repair is running…" :
                        mode === "fix" ? "Describe the bug, e.g. “withdrawals can go negative”" :
                        "Ask about this codebase…"}
                      value={draft}
                      disabled={!!job}
                      onChange={(e) => setDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onSend(); }
                      }}
                    />
                    {mode === "fix" ? (
                      <button type="submit" className="btn-primary fix-go" disabled={!!job || draft.trim().length < 8}>
                        <IconWrench width={16} height={16} /> Fix it
                      </button>
                    ) : (
                      <button type="submit" className="send" disabled={askBusy || !draft.trim()} title="Send" aria-label="Send">
                        <IconSend width={18} height={18} />
                      </button>
                    )}
                  </div>
                  {mode === "fix" ? (
                    <details className="fix-options">
                      <summary>Options and notes{testPath ? ` · ${testPath}` : ""}</summary>
                      <label htmlFor="fix-test-path">Test file <span>(optional, narrows the run)</span></label>
                      <input id="fix-test-path" value={testPath} placeholder="tests/test_orders.py"
                        onChange={(e) => setTestPath(e.target.value)} disabled={!!job} />
                      <div className="hint left">
                        Each attempt uses 1–3 model calls{hasKey ? "." : ", so add your own Gemini key (API key in the sidebar) rather than spend the shared demo quota."}
                        {" "}The first repair after a quiet period can take a minute while the test runner wakes up.
                      </div>
                    </details>
                  ) : (
                    <div className="hint">Enter to send · Shift+Enter for a new line</div>
                  )}
                </div>
              </form>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
