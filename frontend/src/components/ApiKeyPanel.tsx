import { useEffect, useRef, useState } from "react";
import { clearApiKey, getApiKey, setApiKey, validateKey } from "../api";
import { IconCheckSmall, IconKey, IconXSmall } from "../icons";
import { withPeriod } from "../lib";

type KeyStatus = "unknown" | "checking" | "valid" | "invalid";

export default function ApiKeyPanel({ onClose, onKeyChange }: {
  onClose: () => void;
  onKeyChange: (has: boolean) => void;
}) {
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
        <label style={{ margin: 0 }}>Your Gemini API key <span className="muted">· optional</span></label>
      </div>
      <input
        type="password"
        style={{ marginTop: 10 }}
        placeholder={keySaved ? "•••••••• (saved)" : "Paste a key (AIza… or AQ…)"}
        value={keyInput}
        onChange={(e) => setKeyInput(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && keyInput.trim() && saveAndTest()}
      />
      <div className="row" style={{ marginTop: 8 }}>
        <button className="btn-primary" onClick={saveAndTest} disabled={!keyInput.trim() || status === "checking"} style={{ flex: 1 }}>
          {status === "checking" ? "Testing…" : "Save & Test"}
        </button>
        {keySaved && <button className="btn-quiet" onClick={removeKey}>Clear</button>}
      </div>
      <div className="key-status">
        {status === "valid" && <IconCheckSmall width={14} height={14} className="status-icon good" />}
        {status === "invalid" && <IconXSmall width={14} height={14} className="status-icon bad" />}
        {(status === "unknown" || status === "checking") && (
          <span className={status === "checking" ? "dot checking" : keySaved ? "dot on" : "dot off"} />
        )}
        <span>
          {status === "checking" && "Checking with Gemini…"}
          {status === "valid" && "Valid. This spends your own free quota, not the shared demo's."}
          {status === "invalid" && withPeriod(detail || "This key was rejected by Gemini.")}
          {status === "unknown" && (keySaved
            ? "Saved. Used for your requests. Click Save & Test again to re-verify."
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
