import Modal from "./Modal";

export default function HelpModal({ onClose }: { onClose: () => void }) {
  return (
    <Modal title="How DevPilot works" subtitle="An AI agent that reads, explains and fixes a real codebase." onClose={onClose}>
      <h4>Quick start</h4>
      <ol className="modal-steps">
        <li><span>Paste a GitHub URL and hit <b>Add repo</b>, or click one of the sample bugs. It indexes the repo for retrieval.</span></li>
        <li><span>Pick the repo in the sidebar. Use <b>Ask</b> to question the code, or <b>Fix a bug</b> to describe a failing behavior.</span></li>
        <li><span>Answers cite files it actually read. Fixes come with a diff and the test results before and after.</span></li>
      </ol>

      <h4>What it can do</h4>
      <ul className="modal-list good">
        <li>Answer questions about a codebase using hybrid semantic + keyword search and real file reads.</li>
        <li>Fix a bug in Python, JavaScript/TypeScript or Go: reproduce the failing test, find the code, patch it, and re-run the tests to prove the fix. The tests run in an isolated, secret-free runner, after it installs the repo's dependencies (pip wheels, npm without install scripts, Go modules).</li>
        <li>The 97% result is from the 36-case Python benchmark. The JavaScript, Go and dependency support is newer and has no measured pass rate yet.</li>
        <li>Use your own Gemini key (<code>AIza…</code> or the newer <code>AQ…</code> format), so you are never limited by the shared demo quota.</li>
        <li>Remember every repo and conversation. Come back later and pick up where you left off.</li>
      </ul>

      <h4>What it can't do (yet)</h4>
      <ul className="modal-list bad">
        <li>Run repos that need system libraries, native builds, databases or a browser. Packages that only ship source code (no wheel) are not installed, and full React or Next.js apps can exceed the free runner's memory.</li>
        <li>Block network access during a test run on free hosting. The runner holds no secrets, but run only code you'd be fine executing.</li>
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
