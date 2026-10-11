import { useState } from "react";
import { AuthError, changePassword, deleteAccount, setToken } from "../api";
import { IconAlert, IconCheckSmall, IconLock, IconTrash } from "../icons";
import { describeError, initials } from "../lib";
import Modal from "./Modal";
import PasswordField, { StrengthMeter } from "./PasswordField";

type Tab = "profile" | "security" | "danger";

export default function AccountModal({ name, email, repoCount, onClose, onSessionEnded, onDeleted }: {
  name: string;
  email: string;
  repoCount: number;
  onClose: () => void;
  onSessionEnded: () => void;
  onDeleted: () => void;
}) {
  const [tab, setTab] = useState<Tab>("profile");

  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [pwBusy, setPwBusy] = useState(false);
  const [pwError, setPwError] = useState("");
  const [pwDone, setPwDone] = useState(false);

  const [delPassword, setDelPassword] = useState("");
  const [understood, setUnderstood] = useState(false);
  const [delBusy, setDelBusy] = useState(false);
  const [delError, setDelError] = useState("");

  const mismatch = confirm.length > 0 && confirm !== next;
  const canChange = current.length > 0 && next.length >= 8 && confirm === next && !pwBusy;

  async function submitPassword() {
    if (!canChange) return;
    setPwError(""); setPwDone(false); setPwBusy(true);
    try {
      const res = await changePassword(current, next);
      setToken(res.token); // this device stays signed in; every other one is signed out
      setCurrent(""); setNext(""); setConfirm("");
      setPwDone(true);
    } catch (e) {
      if (e instanceof AuthError) { onSessionEnded(); return; }
      setPwError(describeError(e));
    } finally {
      setPwBusy(false);
    }
  }

  async function submitDelete() {
    if (!understood || !delPassword || delBusy) return;
    setDelError(""); setDelBusy(true);
    try {
      await deleteAccount(delPassword);
      onDeleted();
    } catch (e) {
      if (e instanceof AuthError) { onSessionEnded(); return; }
      setDelError(describeError(e));
    } finally {
      setDelBusy(false);
    }
  }

  return (
    <Modal title="Account" subtitle={email} onClose={onClose}>
      <div className="tabs" role="tablist">
        {(["profile", "security", "danger"] as Tab[]).map((t) => (
          <button key={t} type="button" role="tab" aria-selected={tab === t}
            className={tab === t ? "tab active" : "tab"} onClick={() => setTab(t)}>
            {t === "profile" ? "Profile" : t === "security" ? "Password" : "Delete account"}
          </button>
        ))}
      </div>

      {tab === "profile" && (
        <div className="account-profile">
          <span className="avatar xl">{initials(name, email)}</span>
          <div>
            <div className="account-name">{name || "—"}</div>
            <div className="muted">{email}</div>
          </div>
          <dl className="account-facts">
            <div><dt>Repositories</dt><dd>{repoCount}</dd></div>
            <div><dt>Sessions</dt><dd>Signed in on this device</dd></div>
          </dl>
        </div>
      )}

      {tab === "security" && (
        <form onSubmit={(e) => { e.preventDefault(); submitPassword(); }}>
          <p className="muted section-note">
            Changing your password signs you out everywhere else.
          </p>
          <PasswordField label="Current password" value={current} onChange={setCurrent} autoComplete="current-password" />
          <PasswordField label="New password" value={next} onChange={setNext} autoComplete="new-password" />
          <StrengthMeter password={next} />
          <PasswordField label="Confirm new password" value={confirm} onChange={setConfirm} autoComplete="new-password" />
          {mismatch && <div className="field-hint bad">The two passwords don't match.</div>}
          <button type="submit" className="btn-primary" disabled={!canChange}>
            <IconLock width={15} height={15} /> {pwBusy ? "Updating…" : "Update password"}
          </button>
          {pwDone && (
            <div className="alert ok" role="status">
              <IconCheckSmall width={18} height={18} />
              <div>Password updated. Other devices have been signed out.</div>
            </div>
          )}
          {pwError && <div className="alert error" role="alert"><IconAlert width={18} height={18} /><div>{pwError}</div></div>}
        </form>
      )}

      {tab === "danger" && (
        <form className="danger-zone" onSubmit={(e) => { e.preventDefault(); submitDelete(); }}>
          <div className="alert warn">
            <IconAlert width={18} height={18} />
            <div>
              <b>This permanently deletes your account.</b>
              <ul>
                <li>Every repository you added ({repoCount})</li>
                <li>All of your chat history</li>
                <li>Your search index for repos nobody else added</li>
              </ul>
              It can't be undone.
            </div>
          </div>
          <PasswordField label="Confirm with your password" value={delPassword} onChange={setDelPassword} autoComplete="current-password" />
          <label className="check">
            <input type="checkbox" checked={understood} onChange={(e) => setUnderstood(e.target.checked)} />
            <span>I understand this can't be undone.</span>
          </label>
          <button type="submit" className="btn-danger" disabled={!understood || !delPassword || delBusy}>
            <IconTrash width={15} height={15} /> {delBusy ? "Deleting…" : "Delete my account"}
          </button>
          {delError && <div className="alert error" role="alert"><IconAlert width={18} height={18} /><div>{delError}</div></div>}
        </form>
      )}
    </Modal>
  );
}
