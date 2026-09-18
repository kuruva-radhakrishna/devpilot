import { useEffect, useState } from "react";
import { ask, ingestRepo, listRepos, type AskResponse, type RepoMeta } from "./api";

export default function App() {
  const [repos, setRepos] = useState<Record<string, RepoMeta>>({});
  const [source, setSource] = useState("");
  const [repoId, setRepoId] = useState("");
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<AskResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = () => listRepos().then(setRepos).catch(() => {});
  useEffect(() => { refresh(); }, []);

  async function onIngest() {
    setError(""); setBusy(true);
    try {
      const meta = await ingestRepo(source.trim());
      await refresh();
      setRepoId(meta.repo_id);
      setSource("");
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  async function onAsk() {
    if (!repoId || !question.trim()) return;
    setError(""); setBusy(true); setResult(null);
    try {
      setResult(await ask(repoId, question.trim()));
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  const examples = [
    "Give me a high-level overview of this codebase.",
    "Where is authentication handled and how?",
    "What happens when a request hits the main entry point?",
  ];

  return (
    <div className="app">
      <div className="title">🧭 DevPilot</div>
      <div className="subtitle">
        AI software-engineering agent — ingest a repo, then ask it anything. Answers are grounded in code the agent actually read.
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
    </div>
  );
}
