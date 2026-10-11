import { useState } from "react";
import { forgotPassword, login, register, resetPassword, setToken, type Health } from "../api";
import {
  BrandMark, IconAlert, IconBars, IconCheck, IconCheckSmall, IconChat, IconHelp,
  IconKey, IconMail, IconNetwork, IconSearch,
} from "../icons";
import HelpModal from "./HelpModal";
import PasswordField, { StrengthMeter } from "./PasswordField";
import ThemeSwitch from "./ThemeSwitch";

const FEATURES = [
  { Icon: IconSearch, title: "Code-aware RAG", body: "Indexes your repo with hybrid semantic + keyword search." },
  { Icon: IconChat, title: "Grounded Q&A", body: "Reads real files and shows exactly which tools it used." },
  { Icon: IconCheck, title: "Verified repair", body: "Patches a bug, then re-runs the tests in an isolated runner to prove the fix." },
  { Icon: IconBars, title: "Evaluated, not vibes", body: "97% repair success, measured on a 36-case benchmark." },
  { Icon: IconNetwork, title: "Provider-agnostic", body: "Runs on Gemini or a local Ollama model." },
  { Icon: IconKey, title: "Bring your own key", body: "Use your own free Gemini key. The demo quota never runs out." },
];

type Mode = "login" | "register" | "forgot" | "reset";

export default function AuthScreen({ health, resetToken, onAuthed, onResetDone }: {
  health: Health | null;
  resetToken: string;
  onAuthed: (email: string, name: string) => void;
  onResetDone: () => void;
}) {
  const [mode, setMode] = useState<Mode>(resetToken ? "reset" : "login");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [sent, setSent] = useState(false);
  const [showHelp, setShowHelp] = useState(false);

  const emailOk = email.trim().length > 3 && email.includes("@");
  const ready =
    mode === "login" ? emailOk && password.length >= 8 :
    mode === "register" ? !!name.trim() && emailOk && password.length >= 8 :
    mode === "forgot" ? emailOk :
    password.length >= 8;

  function go(next: Mode) { setMode(next); setError(""); setSent(false); setPassword(""); }

  async function submit() {
    if (!ready || busy) return;
    setError(""); setBusy(true);
    try {
      if (mode === "forgot") {
        await forgotPassword(email.trim().toLowerCase());
        setSent(true);
        return;
      }
      const res =
        mode === "login" ? await login(email.trim().toLowerCase(), password) :
        mode === "register" ? await register(email.trim().toLowerCase(), password, name.trim()) :
        await resetPassword(resetToken, password);
      setToken(res.token);
      if (mode === "reset") onResetDone();
      onAuthed(res.email, res.name);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const emailDisabled = mode === "forgot" && health?.email_enabled === false;
  const titles: Record<Mode, [string, string]> = {
    login: ["Welcome back", "Log in to pick up where you left off."],
    register: ["Create your account", "Your repos and conversations are private to you."],
    forgot: ["Reset your password", "We'll email you a link that works once."],
    reset: ["Choose a new password", "You'll be signed in right after."],
  };

  return (
    <div className="auth-page">
      <header className="auth-top">
        <div className="brand"><BrandMark size={30} /><span className="wordmark">DevPilot</span></div>
        <div className="top-actions">
          <button type="button" className="btn-chip" onClick={() => setShowHelp(true)} title="How DevPilot works">
            <IconHelp width={16} height={16} /><span className="chip-label">Help</span>
          </button>
          <ThemeSwitch />
        </div>
      </header>
      {showHelp && <HelpModal onClose={() => setShowHelp(false)} />}

      <main className="auth-main">
        <div className="auth-card">
          <section className="auth-form-side">
            <h1 className="auth-title">{titles[mode][0]}</h1>
            <p className="auth-sub">{titles[mode][1]}</p>

            {(mode === "login" || mode === "register") && (
              <div className="tabs" role="tablist">
                <button type="button" role="tab" aria-selected={mode === "login"}
                  className={mode === "login" ? "tab active" : "tab"} onClick={() => go("login")}>Log in</button>
                <button type="button" role="tab" aria-selected={mode === "register"}
                  className={mode === "register" ? "tab active" : "tab"} onClick={() => go("register")}>Create account</button>
              </div>
            )}

            {mode === "forgot" && sent ? (
              <div className="alert ok" role="status">
                <IconMail width={18} height={18} />
                <div>
                  <b>Check your email.</b> If an account exists for <b>{email.trim()}</b>, a reset link is on its way.
                  It works once and expires in 30 minutes.
                </div>
              </div>
            ) : (
              <form onSubmit={(e) => { e.preventDefault(); submit(); }} noValidate>
                {mode === "register" && (
                  <div className="field">
                    <label htmlFor="auth-name">Name</label>
                    <input id="auth-name" autoComplete="name" placeholder="Ada Lovelace"
                      value={name} onChange={(e) => setName(e.target.value)} />
                  </div>
                )}

                {mode !== "reset" && (
                  <div className="field">
                    <label htmlFor="auth-email">Email</label>
                    <input id="auth-email" type="email" autoComplete="email" placeholder="you@example.com"
                      value={email} onChange={(e) => setEmail(e.target.value)} />
                  </div>
                )}

                {mode !== "forgot" && (
                  <>
                    <PasswordField
                      label={mode === "reset" ? "New password" : "Password"}
                      value={password}
                      onChange={setPassword}
                      autoComplete={mode === "login" ? "current-password" : "new-password"}
                      autoFocus={mode === "reset"}
                    />
                    {mode !== "login" && <StrengthMeter password={password} />}
                    {mode === "login" && (
                      <div className="field-aside">
                        <button type="button" className="link" onClick={() => go("forgot")}>Forgot password?</button>
                      </div>
                    )}
                  </>
                )}

                {emailDisabled && (
                  <div className="alert warn" role="alert">
                    <IconAlert width={18} height={18} />
                    <div>Password reset email isn't set up on this server yet.</div>
                  </div>
                )}

                <button type="submit" className="btn-primary block" disabled={busy || !ready || emailDisabled}>
                  {busy ? "Please wait…" :
                    mode === "login" ? "Log in" :
                    mode === "register" ? "Create account" :
                    mode === "forgot" ? "Send reset link" : "Set password and sign in"}
                </button>
                {error && <div className="alert error" role="alert"><IconAlert width={18} height={18} /><div>{error}</div></div>}
              </form>
            )}

            {(mode === "forgot" || mode === "reset") && (
              <div className="auth-foot">
                <button type="button" className="link" onClick={() => { go("login"); if (mode === "reset") onResetDone(); }}>
                  Back to log in
                </button>
              </div>
            )}
          </section>

          <aside className="auth-feature-side">
            <h3>Why DevPilot</h3>
            {FEATURES.map((f) => (
              <div className="feature-row" key={f.title}>
                <div className="feature-icon"><f.Icon width={17} height={17} /></div>
                <div>
                  <div className="feature-title">{f.title}</div>
                  <div className="feature-body">{f.body}</div>
                </div>
              </div>
            ))}
            <div className="trust"><IconCheckSmall width={14} height={14} /> Passwords are hashed. Sessions end when you change one.</div>
          </aside>
        </div>
      </main>
    </div>
  );
}
