import test from "node:test";
import assert from "node:assert";
import { slugify, truncate, capitalize } from "../src/text.js";

test("slugify joins words with dashes", () => {
  assert.strictEqual(slugify("Hello World"), "hello-world");
});

test("slugify strips punctuation at both ends", () => {
  assert.strictEqual(slugify("Hello, World!"), "hello-world");
  assert.strictEqual(slugify("  --Already Slugged--  "), "already-slugged");
});

test("truncate keeps the result within the limit", () => {
  assert.strictEqual(truncate("abcdefghij", 5), "abcd…");
  assert.strictEqual(truncate("abc", 5), "abc");
});

test("capitalize upper-cases the first letter", () => {
  assert.strictEqual(capitalize("hello"), "Hello");
  assert.strictEqual(capitalize(""), "");
});
