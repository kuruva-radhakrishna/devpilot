const test = require("node:test");
const assert = require("node:assert");
const { Cart, Wishlist } = require("../src/cart");

test("count sums the quantities", () => {
  const cart = new Cart();
  cart.add({ price: 10, qty: 3 });
  cart.add({ price: 5, qty: 1 });
  assert.strictEqual(cart.count(), 4);
});

test("total multiplies each price by its quantity", () => {
  const cart = new Cart();
  cart.add({ price: 10, qty: 3 });
  cart.add({ price: 5, qty: 1 });
  assert.strictEqual(cart.total(), 35);
});

test("an empty cart totals zero", () => {
  assert.strictEqual(new Cart().total(), 0);
});

test("a wishlist total is just the sum of the prices", () => {
  const list = new Wishlist();
  list.add({ price: 10 });
  list.add({ price: 5 });
  assert.strictEqual(list.total(), 15);
});
