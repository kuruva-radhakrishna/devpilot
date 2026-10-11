import { useEffect, useRef, type ReactNode } from "react";
import { IconXSmall } from "../icons";

/** Accessible dialog shell: Esc and backdrop-click close it, focus moves into it. */
export default function Modal({ title, subtitle, onClose, children, wide }: {
  title: string;
  subtitle?: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  const card = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onKey(e: KeyboardEvent) { if (e.key === "Escape") onClose(); }
    document.addEventListener("keydown", onKey);
    card.current?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div
        className={wide ? "modal-card wide" : "modal-card"}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        ref={card}
      >
        <div className="modal-head">
          <div>
            <h2>{title}</h2>
            {subtitle && <div className="modal-sub">{subtitle}</div>}
          </div>
          <button type="button" className="icon-btn" onClick={onClose} title="Close" aria-label="Close">
            <IconXSmall width={16} height={16} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
