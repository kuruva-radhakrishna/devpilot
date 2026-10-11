import { type RepairEvent } from "../api";
import { IconAlert, IconCheckSmall } from "../icons";

type RowState = "active" | "ok" | "bad";

function rowState(e: RepairEvent & { skipped?: boolean }, isLast: boolean, running: boolean): RowState {
  if (isLast && running) return "active";
  if (e.stage === "attempt_failed") return "bad";
  if (e.stage === "verify_done" && e.passed === false) return "bad";
  if (e.stage === "baseline_done" && e.skipped) return "bad";
  // A failing baseline is the point: it means the bug was reproduced.
  return "ok";
}

const LABEL: Record<string, string> = {
  warm: "Wake the runner",
  baseline: "Reproduce",
  baseline_done: "Reproduce",
  retrieval: "Find the code",
  retrieval_done: "Find the code",
  attempt: "Propose a fix",
  attempt_failed: "Propose a fix",
  patch: "Apply the patch",
  verify: "Verify",
  verify_done: "Verify",
};

/** Live timeline of a running repair: one row per step the server reports. */
export default function RepairProgress({ events, elapsed, running }: {
  events: RepairEvent[];
  elapsed: number;
  running: boolean;
}) {
  const last = events[events.length - 1];
  // The first call after a quiet period can spend a while waking the test runner.
  const waking = running && (!last || last.stage === "baseline") && elapsed > 8;
  return (
    <div className="msg assistant">
      <div className="card repair-card" role="status" aria-live="polite">
        <div className="repair-head">
          <span className="repair-title">{running ? "Fixing the bug" : "Repair finished"}</span>
          <span className="repair-clock">{Math.floor(elapsed)}s</span>
        </div>
        <ol className="repair-steps">
          {events.length === 0 && (
            <li className="step active"><span className="spin" aria-hidden="true" /><span>Starting…</span></li>
          )}
          {events.map((e, i) => {
            const state = rowState(e, i === events.length - 1, running);
            return (
              <li key={i} className={`step ${state}`}>
                {state === "active" ? <span className="spin" aria-hidden="true" /> :
                  state === "bad" ? <IconAlert width={15} height={15} /> : <IconCheckSmall width={15} height={15} />}
                <span className="step-label">{LABEL[e.stage] ?? e.stage}</span>
                <span className="step-msg">{e.message}</span>
                <span className="step-time">{e.t.toFixed(0)}s</span>
              </li>
            );
          })}
        </ol>
        {waking && (
          <div className="repair-note">
            Still starting. On free hosting the test runner goes to sleep when idle, so the first run
            after a quiet period can take up to a minute.
          </div>
        )}
      </div>
    </div>
  );
}
