"use strict";

/**
 * A shopping cart, plus a wishlist that has a method with the same name.
 *
 * Deliberate bug for the DevPilot benchmark: Cart.total() counts every line once,
 * whatever its quantity. (Wishlist.total() is correct, so a repair has to name the
 * method as Cart.total, not just "total".)
 */
class Cart {
  constructor() {
    this.items = [];
  }

  add(item) {
    this.items.push(item);
  }

  total() {
    // BUG: ignores item.qty.
    return this.items.reduce((sum, item) => sum + item.price, 0);
  }

  count() {
    return this.items.reduce((n, item) => n + item.qty, 0);
  }
}

class Wishlist {
  constructor() {
    this.items = [];
  }

  add(item) {
    this.items.push(item);
  }

  total() {
    return this.items.reduce((sum, item) => sum + item.price, 0);
  }
}

module.exports = { Cart, Wishlist };
