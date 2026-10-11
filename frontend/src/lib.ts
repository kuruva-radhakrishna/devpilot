import { AuthError } from "./api";

export function describeError(e: unknown): string {
  if (e instanceof AuthError) return "Your session ended — please log in again.";
  if (e instanceof TypeError) {
    // fetch() throws a bare TypeError (not an HTTP error) when the connection
    // itself failed — e.g. a host request-duration limit killing a long
    // request. There's no server response to show.
    return (
      "Network error — the request was interrupted before it finished. " +
      "Large repos (or free-tier rate-limit backoffs) can take a while and may " +
      "exceed the hosting timeout; try again, a smaller repo, or your own API key."
    );
  }
  return (e as Error).message || "Something went wrong.";
}

export function initials(name: string, email: string): string {
  const src = (name || "").trim() || email || "?";
  const parts = src.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return src.slice(0, 2).toUpperCase();
}

/** Error details (network messages, raw exception text) rarely end in
 * punctuation — ensure one so it doesn't run into the following sentence. */
export function withPeriod(s: string): string {
  const t = s.trim();
  return t && !/[.!?]$/.test(t) ? `${t}.` : t;
}
