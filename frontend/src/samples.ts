/** Bundled demo repos: each has one real bug and a failing test suite. The Python ones are
 * the fixtures the 36-case benchmark uses; the JavaScript, Go and dependency ones exercise the
 * multi-language runner and are not part of that measured benchmark. They are indexed on the
 * server as "sample:<name>". */
export interface SampleBug {
  source: string;
  title: string;
  note: string;
  bug: string;
  testPath: string;
  /** Shown as a small badge on the card. */
  lang: string;
}

export const SAMPLE_BUGS: SampleBug[] = [
  {
    source: "sample:bank_app",
    title: "Overdraft allowed",
    note: "A withdrawal can push the balance negative",
    bug: "Withdrawing more than the balance succeeds and returns a negative balance; it should raise a ValueError.",
    testPath: "tests/test_account.py",
    lang: "Python",
  },
  {
    source: "sample:sample_app",
    title: "Crash on a missing address",
    note: "An order without an address raises KeyError",
    bug: "Orders without an address crash with a KeyError; they should ship to 'unknown' instead.",
    testPath: "tests/test_orders.py",
    lang: "Python",
  },
  {
    source: "sample:cart_app",
    title: "Discount applied twice",
    note: "A 10% discount on 100 comes out as 80",
    bug: "Cart totals with a discount are too low: a 10% discount on 100 returns 80 instead of 90; the discount is applied twice.",
    testPath: "tests/test_cart.py",
    lang: "Python",
  },
  {
    source: "sample:auth_app",
    title: "Expired tokens accepted",
    note: "The cause is in a different file than the symptom",
    bug: "Expired tokens are still authorized (authorize returns 200 for an expired token); token validation should reject expired tokens.",
    testPath: "tests/test_auth.py",
    lang: "Python",
  },
  {
    source: "sample:js_cart",
    title: "Cart total ignores quantity",
    note: "Two classes share a method name; the fix must pick the right one",
    bug: "A cart with 3 items at 10 and 1 item at 5 totals 15 instead of 35; the quantity of each line is ignored.",
    testPath: "test/cart.test.js",
    lang: "JavaScript",
  },
  {
    source: "sample:go_stack",
    title: "Pop panics on an empty stack",
    note: "Go methods on a pointer receiver; runs go test",
    bug: "Calling Pop on an empty Stack panics with an index out of range; it should return 0 and false.",
    testPath: "./stack",
    lang: "Go",
  },
  {
    source: "sample:py_deps",
    title: "Needs a package installed",
    note: "The tests import PyYAML, so the runner installs it first",
    bug: "settings.get raises a TypeError for a dotted path whose parent is a number, such as db.port.number; it should return the default.",
    testPath: "tests/test_settings.py",
    lang: "Python + pip",
  },
];
