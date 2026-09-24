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

## 0. A Postgres database (for login)

Email/password login stores accounts in Postgres. Render's free web service has
**no persistent disk**, so accounts must live in an external DB or they vanish on
every redeploy. Use a free hosted Postgres — **Supabase** is the quickest:

1. https://supabase.com → **New project** (free tier). Pick a region + DB password.
2. Project → **Connect** (or Settings → Database) → copy the **connection string
   (URI)**. It looks like
   `postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres` (Supabase's
   string already requires SSL). The transaction **pooler** URI works too.
3. Keep this string — it's the `DATABASE_URL` env var in step 1.

The `users` table is created automatically on first boot. If you skip this step
and leave `DATABASE_URL` empty, the app still runs — just with **no login gate**
(open access), which is fine for a purely local/private demo.

## 1. Backend → Render

1. Push this repo to GitHub (done).
2. Render Dashboard → **New → Blueprint** → select this repo. Render reads
   [`render.yaml`](render.yaml) and creates the `devpilot-backend` Docker service.
   (Or **New → Web Service → Docker** and point it at [`Dockerfile`](Dockerfile).)
3. Set env vars in the service's **Environment** tab:
   - `GEMINI_API_KEY` = your `AIza…` key (the server's fallback key; visitors can
     also bring their own in the UI — see BYOK below)
   - `ALLOWED_ORIGINS` = your Vercel URL (fill in after step 2 below; you can
     start with `*` to test, then lock it down)
   - `DATABASE_URL` = the Postgres URI from step 0 (enables login)
   - `JWT_SECRET` = the blueprint generates one automatically; or set your own
     long random string. Signs login sessions — keep it secret.
   - `LLM_PROVIDER=gemini`, `GEMINI_MODEL=gemini-3.5-flash-lite`,
     `SANDBOX_BACKEND=local`, `VECTOR_BACKEND=memory` (already set by the blueprint)
4. Deploy. When it's live, note the URL, e.g. `https://devpilot-backend.onrender.com`.
   Health check: open `<url>/api/health` → `{"status":"ok","auth_enabled":true,...}`.

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
2. Open the Vercel URL, **create an account**, paste your own Gemini key if you
   have one, pick a repo, and try the agent.

## Bring your own key (BYOK)

The UI has an optional **"Your Gemini API key"** field. When a visitor pastes a
key there:

- it's stored **only in their browser** (localStorage) — never in our database
  or logs;
- it's sent with each request as the `X-Gemini-Key` header and used **only** for
  that request, then discarded;
- if no key is set, requests fall back to the server's `GEMINI_API_KEY`.

This is why the public demo doesn't die on the free-tier quota: each visitor
spends *their own* ~20 calls/day, not a single shared budget. Get a free key at
https://aistudio.google.com/app/apikey.

## Local dev (unchanged)

```bash
cd backend && uvicorn app.main:app --reload --port 8000
cd frontend && npm install && npm run dev   # http://localhost:5173
```
