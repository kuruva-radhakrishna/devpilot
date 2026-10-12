/**
 * Text helpers (ES module, arrow functions).
 *
 * Deliberate bug for the DevPilot benchmark: slugify() leaves a dash on each end when the
 * input starts or ends with punctuation ("Hello, World!" -> "hello-world-").
 */
export const slugify = (text) => {
  return text
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-");
};

export const truncate = (text, max) => {
  if (text.length <= max) return text;
  return text.slice(0, max - 1) + "…";
};

export const capitalize = (text) => (text ? text[0].toUpperCase() + text.slice(1) : text);
