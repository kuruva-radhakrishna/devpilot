// Thin typed client for the DevPilot backend.
const BASE = "http://localhost:8000";

export interface RepoMeta {
  repo_id: string;
  source: string;
  files: number;
  chunks: number;
  repo_dir?: string;
}

export interface ToolCall {
  name: string;
  args: Record<string, unknown>;
  result_preview: string;
}

export interface AskResponse {
  answer: string;
  steps: number;
  tool_calls: ToolCall[];
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(detail.detail || "Request failed");
  }
  return res.json();
}

export const ingestRepo = (source: string) =>
  post<RepoMeta>("/api/repos/ingest", { source });

export const listRepos = async (): Promise<Record<string, RepoMeta>> => {
  const res = await fetch(BASE + "/api/repos");
  const data = await res.json();
  return data.repos ?? {};
};

export const ask = (repo_id: string, question: string) =>
  post<AskResponse>("/api/agent/ask", { repo_id, question });
