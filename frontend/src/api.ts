// Thin typed client for the DevPilot backend.
// Set VITE_API_BASE at build time (e.g. on Vercel) to your deployed backend URL;
// falls back to the local dev server.
const BASE = (import.meta.env.VITE_API_BASE ?? "http://localhost:8000").replace(/\/$/, "");

// --- Local-only credential storage --------------------------------------
// The session token and the user's own Gemini key live ONLY in this browser
// (localStorage). The key is never sent to our database — only forwarded to the
// backend per request as the X-Gemini-Key header so calls use the visitor's own
// free quota. Wrapped in try/catch so private-mode / blocked storage degrades.
const TOKEN_KEY = "devpilot_token";
const GEMINI_KEY = "devpilot_gemini_key";

function ls(key: string): string {
  try {
    return localStorage.getItem(key) ?? "";
  } catch {
    return "";
  }
}
function lsSet(key: string, value: string) {
  try {
    if (value) localStorage.setItem(key, value);
    else localStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}

export const getToken = () => ls(TOKEN_KEY);
export const setToken = (t: string) => lsSet(TOKEN_KEY, t);
export const clearToken = () => lsSet(TOKEN_KEY, "");
export const getApiKey = () => ls(GEMINI_KEY);
export const setApiKey = (k: string) => lsSet(GEMINI_KEY, k.trim());
export const clearApiKey = () => lsSet(GEMINI_KEY, "");

// --- Types ---------------------------------------------------------------
export interface RepoMeta {
  repo_id: string;
  source: string;
  files: number;
  chunks: number;
  repo_dir?: string;
  /** Friendly label for the sidebar — a user-set name, or a default derived
   * from the source (e.g. "weather") when they haven't renamed it yet. */
  display_name?: string;
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

export interface Health {
  status: string;
  provider: string;
  model: string;
  auth_enabled: boolean;
  gemini_key_set: boolean;
}

/** Thrown on a 401 so the UI can drop the session and show the login screen. */
export class AuthError extends Error {}

// --- Request plumbing ----------------------------------------------------
function headers(json = true): Record<string, string> {
  const h: Record<string, string> = {};
  if (json) h["Content-Type"] = "application/json";
  const token = getToken();
  if (token) h["Authorization"] = `Bearer ${token}`;
  const key = getApiKey();
  if (key) h["X-Gemini-Key"] = key;
  return h;
}

async function handle<T>(res: Response): Promise<T> {
  if (res.status === 401) {
    clearToken();
    const d = await res.json().catch(() => ({ detail: "Session expired." }));
    throw new AuthError(d.detail || "Please log in again.");
  }
  if (!res.ok) {
    const d = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(d.detail || "Request failed");
  }
  return res.json();
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(BASE + path, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(body),
  });
  return handle<T>(res);
}

// --- Auth ----------------------------------------------------------------
export interface AuthResponse {
  token: string;
  email: string;
  name: string;
}

export const register = (email: string, password: string, name: string) =>
  post<AuthResponse>("/api/auth/register", { email, password, name });

export const login = (email: string, password: string) =>
  post<AuthResponse>("/api/auth/login", { email, password });

/** Tests a Gemini key with a cheap, no-generation call. Pass an explicit key
 * to test a candidate before saving it — falls back to the saved one. */
export const validateKey = async (candidateKey?: string): Promise<{ valid: boolean; detail?: string }> => {
  const h = headers(false);
  if (candidateKey !== undefined) h["X-Gemini-Key"] = candidateKey;
  const res = await fetch(BASE + "/api/auth/validate-key", { method: "POST", headers: h });
  return handle(res);
};

export const getHealth = async (): Promise<Health> => {
  const res = await fetch(BASE + "/api/health");
  return res.json();
};

// --- App API -------------------------------------------------------------
export const ingestRepo = (source: string) =>
  post<RepoMeta>("/api/repos/ingest", { source });

export const listRepos = async (): Promise<Record<string, RepoMeta>> => {
  const res = await fetch(BASE + "/api/repos", { headers: headers(false) });
  const data = await handle<{ repos?: Record<string, RepoMeta> }>(res);
  return data.repos ?? {};
};

export const ask = (repo_id: string, question: string) =>
  post<AskResponse>("/api/agent/ask", { repo_id, question });

export interface StoredMessage {
  role: "user" | "assistant" | "error";
  content: string;
  tool_names: string[];
}

/** Prior conversation for this repo, if the server has persistence enabled
 * (a database configured) — otherwise always an empty list. */
export const getMessages = async (repo_id: string): Promise<StoredMessage[]> => {
  const res = await fetch(BASE + `/api/agent/messages/${encodeURIComponent(repo_id)}`, {
    headers: headers(false),
  });
  const data = await handle<{ messages?: StoredMessage[] }>(res);
  return data.messages ?? [];
};

export const deleteRepo = async (repo_id: string): Promise<void> => {
  const res = await fetch(BASE + `/api/repos/${encodeURIComponent(repo_id)}`, {
    method: "DELETE",
    headers: headers(false),
  });
  await handle(res);
};

export const renameRepo = (repo_id: string, display_name: string) =>
  fetch(BASE + `/api/repos/${encodeURIComponent(repo_id)}`, {
    method: "PATCH",
    headers: headers(),
    body: JSON.stringify({ display_name }),
  }).then((res) => handle<{ repo_id: string; display_name: string }>(res));
