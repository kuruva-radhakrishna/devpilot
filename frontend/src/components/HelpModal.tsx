import Modal from "./Modal";

export default function HelpModal({ onClose }: { onClose: () => void }) {
  return (
    <Modal title="How DevPilot works" subtitle="An AI agent that reads and explains a real codebase." onClose={onClose}>
      <h4>Quick start</h4>
      <ol className="modal-steps">
        <li><span>Paste a GitHub URL and hit <b>Add repo</b>. It clones and indexes the repo for retrieval.</span></li>
        <li><span>Pick the repo in the sidebar and ask a question about how the code works.</span></li>
        <li><span>Get an answer grounded in files it actually read, with the tools it used shown underneath.</span></li>
      </ol>

      <h4>What it can do</h4>
      <ul className="modal-list good">
        <li>Answer questions about a codebase using hybrid semantic + keyword search and real file reads.</li>
        <li>Use your own Gemini key (<code>AIza…</code> or the newer <code>AQ…</code> format), so you are never limited by the shared demo quota.</li>
        <li>Remember every repo and conversation. Come back later and pick up where you left off.</li>
        <li>Fix bugs when you run it yourself: it patches the code and re-runs the repo's tests to verify the fix (97% on its 36-case benchmark).</li>
      </ul>

      <h4>What it can't do (yet)</h4>
      <ul className="modal-list bad">
        <li>Run the bug-fixing loop on this hosted demo. It executes a repo's tests, which needs a real sandbox, so it is switched off here for safety. Run it locally with Docker.</li>
        <li>Push, deploy, or open a pull request. Patches are proposed, never shipped.</li>
        <li>Guarantee a fix on the first try. Repair retries automatically, but it isn't perfect.</li>
      </ul>

      <div className="modal-foot">
        Source, benchmark results, and the write-up:{" "}
        <a href="https://github.com/kuruva-radhakrishna/devpilot" target="_blank" rel="noreferrer">
          github.com/kuruva-radhakrishna/devpilot
        </a>
      </div>
    </Modal>
  );
}
