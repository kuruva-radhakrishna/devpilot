/** Bundled demo repos: each has one real bug and a failing pytest. They are the same
 * fixtures the 36-case benchmark uses, and are indexed on the server as "sample:<name>". */
export interface SampleBug {
  source: string;
  title: string;
  note: string;
  bug: string;
  testPath: string;
}

export const SAMPLE_BUGS: SampleBug[] = [
  {
    source: "sample:bank_app",
    title: "Overdraft allowed",
    note: "A withdrawal can push the balance negative",
    bug: "Withdrawing more than the balance succeeds and returns a negative balance; it should raise a ValueError.",
    testPath: "tests/test_account.py",
  },
  {
    source: "sample:sample_app",
    title: "Crash on a missing address",
    note: "An order without an address raises KeyError",
    bug: "Orders without an address crash with a KeyError; they should ship to 'unknown' instead.",
    testPath: "tests/test_orders.py",
  },
  {
    source: "sample:cart_app",
    title: "Discount applied twice",
    note: "A 10% discount on 100 comes out as 80",
    bug: "Cart totals with a discount are too low: a 10% discount on 100 returns 80 instead of 90; the discount is applied twice.",
    testPath: "tests/test_cart.py",
  },
  {
    source: "sample:auth_app",
    title: "Expired tokens accepted",
    note: "The cause is in a different file than the symptom",
    bug: "Expired tokens are still authorized (authorize returns 200 for an expired token); token validation should reject expired tokens.",
    testPath: "tests/test_auth.py",
  },
];
