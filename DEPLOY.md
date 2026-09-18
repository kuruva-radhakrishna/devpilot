# Deploying DevPilot — backend on Render, frontend on Vercel

DevPilot splits across two hosts: the FastAPI backend (needs a real container +
long-running requests + a writable filesystem) on **Render**, and the static
React UI on **Vercel**. Deploy the backend first so you have its URL for the
frontend.

> Honest limits of a live deploy: on a PaaS there's no Docker daemon, so the
> sandbox runs in `local` mode — repo tests execute **unsandboxed** in the
> backend container. Only run it against the trusted bundled samples, never
> arbitrary public input. Repairs take minutes, and the free Gemini tier allows
> ~20 generate calls/day, so treat this as a demo, not a public service.

## 1. Backend → Render

1. Push this repo to GitHub (done).
2. Render Dashboard → **New → Blueprint** → select this repo. Render reads
   [`render.yaml`](render.yaml) and creates the `devpilot-backend` Docker service.
   (Or **New → Web Service → Docker** and point it at [`Dockerfile`](Dockerfile).)
3. Set env vars in the service's **Environment** tab:
   - `GEMINI_API_KEY` = your `AIza…` key
   - `ALLOWED_ORIGINS` = your Vercel URL (fill in after step 2 below; you can
     start with `*` to test, then lock it down)
   - `LLM_PROVIDER=gemini`, `SANDBOX_BACKEND=local`, `VECTOR_BACKEND=memory`
     (already set by the blueprint)
4. Deploy. When it's live, note the URL, e.g. `https://devpilot-backend.onrender.com`.
   Health check: open `<url>/api/health` → `{"status":"ok",...}`.

Free-tier note: the service sleeps after inactivity; the first request cold-starts
in ~30–60s.

## 2. Frontend → Vercel

1. Vercel → **Add New → Project** → import this repo.
2. **Root Directory** = `frontend` (important — the app lives in that subfolder).
   Vercel auto-detects Vite ([`frontend/vercel.json`](frontend/vercel.json) sets
   build `npm run build`, output `dist`, and SPA rewrites).
3. Add an **Environment Variable**:
   - `VITE_API_BASE` = your Render backend URL from step 1
     (e.g. `https://devpilot-backend.onrender.com`)
4. Deploy → you get a URL like `https://devpilot.vercel.app`.

## 3. Connect them

1. Back in Render, set `ALLOWED_ORIGINS` to the exact Vercel URL
   (`https://devpilot.vercel.app`) and redeploy so the browser CORS check passes.
2. Open the Vercel URL, pick a repo, and try the agent.

## Local dev (unchanged)

```bash
cd backend && uvicorn app.main:app --reload --port 8000
cd frontend && npm install && npm run dev   # http://localhost:5173
```
