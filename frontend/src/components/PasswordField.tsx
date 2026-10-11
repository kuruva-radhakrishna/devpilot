import { useId, useState } from "react";
import { IconEye, IconEyeOff } from "../icons";

// Same list the server rejects (backend/app/auth/security.py). Advisory here.
const COMMON = new Set([
  "password", "password1", "password12", "password123", "passw0rd", "12345678",
  "123456789", "1234567890", "11111111", "00000000", "qwerty123", "qwertyuiop",
  "iloveyou", "abc12345", "abcd1234", "letmein1", "welcome1", "admin123",
  "changeme", "football", "baseball", "sunshine", "princess", "monkey123",
]);

/** Advisory only: the server enforces the real rules. Mirrors them closely enough
 * that users rarely see a server rejection. Returns a 0-4 score and a hint. */
export function passwordStrength(pw: string): { score: number; label: string; hint: string } {
  if (!pw) return { score: 0, label: "", hint: "" };
  if (pw.length < 8) return { score: 1, label: "Too short", hint: "Use at least 8 characters." };
  if (new Set(pw).size < 4) return { score: 1, label: "Weak", hint: "Mix in more different characters." };
  if (COMMON.has(pw.toLowerCase())) return { score: 1, label: "Too common", hint: "Pick something less guessable." };
  let score = 1;
  if (pw.length >= 10) score++;
  if (/[a-z]/.test(pw) && /[A-Z]/.test(pw)) score++;
  if (/\d/.test(pw) && /[^A-Za-z0-9]/.test(pw)) score++;
  else if (pw.length >= 14) score++;
  score = Math.min(score, 4);
  const labels = ["", "Weak", "Okay", "Good", "Strong"];
  const hints = ["", "Add length and variety.", "A longer password is safer.", "Nice.", "Great password."];
  return { score, label: labels[score], hint: hints[score] };
}

export function StrengthMeter({ password }: { password: string }) {
  const s = passwordStrength(password);
  if (!password) return null;
  return (
    <div className="strength" aria-live="polite">
      <div className="strength-bars" data-score={s.score}>
        <span /><span /><span /><span />
      </div>
      <span className="strength-label">{s.label}{s.hint ? ` · ${s.hint}` : ""}</span>
    </div>
  );
}

/** Password input with a show/hide toggle. */
export default function PasswordField({ label, value, onChange, autoComplete, placeholder, onEnter, autoFocus }: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  autoComplete: "current-password" | "new-password";
  placeholder?: string;
  onEnter?: () => void;
  autoFocus?: boolean;
}) {
  const id = useId();
  const [show, setShow] = useState(false);
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <div className="input-wrap">
        <input
          id={id}
          type={show ? "text" : "password"}
          autoComplete={autoComplete}
          placeholder={placeholder ?? "••••••••"}
          value={value}
          autoFocus={autoFocus}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && onEnter) onEnter(); }}
        />
        <button
          type="button"
          className="input-adorn"
          onClick={() => setShow((s) => !s)}
          title={show ? "Hide password" : "Show password"}
          aria-label={show ? "Hide password" : "Show password"}
          tabIndex={-1}
        >
          {show ? <IconEyeOff width={16} height={16} /> : <IconEye width={16} height={16} />}
        </button>
      </div>
    </div>
  );
}
