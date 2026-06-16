"""
Lorcana Restock Monitor
-----------------------
Polls Shopify-based Canadian game store collections for Disney Lorcana
stock, and sends a push notification (via ntfy.sh) the moment something
goes from "out of stock" to "in stock".

This watches each store's PUBLISHED ONLINE stock. Many local game stores
explicitly say their website stock does not always match what's on the
physical shelf -- this is the best automatable signal available short of
someone calling the shop.

Add more stores by adding an entry to STORES below. To find a store's feed:
  1. Find their Lorcana collection page, e.g. https://example.ca/collections/lorcana
  2. Append /products.json, e.g. https://example.ca/collections/lorcana/products.json
  3. Open it in a browser -- if you see JSON with a "products" list, it works.
(Only works for stores running Shopify. Not all LGS do.)
"""

import json
import os
import sys
from pathlib import Path

import requests

STATE_FILE = Path(__file__).parent / "state.json"

STORES = [
    {
        "name": "401 Games",
        "domain": "https://store.401games.ca",
        # Switched to the sealed-only collection (the general one mixes in singles).
        "products_json": "https://store.401games.ca/collections/disney-lorcana-sealed-product/products.json?limit=250",
    },
    {
        "name": "Face to Face Games",
        "domain": "https://facetofacegames.com",
        # Switched to the sealed-only collection.
        "products_json": "https://facetofacegames.com/en-us/collections/lorcana-sealed/products.json?limit=250",
    },
    {
        "name": "Hobbiesville",
        "domain": "https://hobbiesville.com",
        "products_json": "https://hobbiesville.com/collections/disney-lorcana/products.json?limit=250",
    },
    {
        "name": "Remi Card Trader",
        "domain": "https://remicardtrader.ca",
        "products_json": "https://remicardtrader.ca/en/collections/disney-lorcana/products.json?limit=250",
    },
    {
        "name": "Draw For Turn Games",
        "domain": "https://drawforturn.ca",
        "products_json": "https://drawforturn.ca/collections/lorcana-products/products.json?limit=250",
    },
    {
        "name": "House of Cards",
        "domain": "https://houseofcards.ca",
        "products_json": "https://houseofcards.ca/collections/disney-lorcana-sealed-product/products.json?limit=250",
    },
    {
        "name": "UBE Card",
        "domain": "https://ubecard.com",
        # UBE has no dedicated Lorcana collection, so this tracks one
        # specific product directly. Add more the same way if you find
        # other UBE Lorcana product URLs (use .../products/HANDLE.json).
        "products_json": "https://ubecard.com/products/disney-lorcana-set-8-reign-of-jafar-booster.json",
    },
    # Add more Canadian Shopify-based stores here, same shape as above.
]

# Only notify for products that look like sealed product (boxes, packs, sets,
# bundles, etc). This is a safety net even for the "sealed-only" collections
# above, in case a store's feed still mixes in singles, accessories, etc.
SEALED_KEYWORDS = [
    "booster box", "booster pack", "booster bundle", "boosters",
    "starter deck", "challenge deck", "deck box",
    "gift set", "gift box", "collection starter", "collector's set", "collector set",
    "trove", "bundle", "blister", "tin", "display", "case",
    "two-player", "gateway", "quest", "fat pack", "value pack",
]


def is_sealed_product(title: str) -> bool:
    title_lower = title.lower()
    return any(keyword in title_lower for keyword in SEALED_KEYWORDS)


NTFY_TOPIC = os.environ.get("NTFY_TOPIC")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; LorcanaRestockMonitor/1.0)"}


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def send_notification(title: str, message: str, url: str) -> None:
    if not NTFY_TOPIC:
        print(f"[no NTFY_TOPIC set] {title}: {message}")
        return
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": "high",
                "Tags": "tada",
                "Click": url,
            },
            timeout=10,
        )
    except requests.RequestException as e:
        print(f"Failed to send notification: {e}", file=sys.stderr)


def check_store(store: dict, state: dict) -> dict:
    """Returns the updated availability state for this store's items.
    Handles both a collection feed (.../collections/x/products.json,
    shaped as {"products": [...]}) and a single product feed
    (.../products/x.json, shaped as {"product": {...}})."""
    try:
        resp = requests.get(store["products_json"], headers=HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        if "products" in data:
            products = data["products"]
        elif "product" in data:
            products = [data["product"]]
        else:
            products = []
    except (requests.RequestException, ValueError) as e:
        print(f"[{store['name']}] fetch failed: {e}", file=sys.stderr)
        return {}

    new_items = {}
    for product in products:
        title = product.get("title", "Unknown item")
        if not is_sealed_product(title):
            continue

        handle = product.get("handle", "")
        product_url = f"{store['domain']}/products/{handle}"

        for variant in product.get("variants", []):
            key = f"{store['name']}::{title}::{variant.get('title', 'default')}"
            available = bool(variant.get("available"))
            new_items[key] = available

            was_available = state.get(key)
            if available and not was_available:
                variant_label = variant.get("title", "")
                label = title if variant_label in ("Default Title", "") else f"{title} ({variant_label})"
                send_notification(
                    title=f"Lorcana restock: {store['name']}",
                    message=f"{label} is back in stock!",
                    url=product_url,
                )
                print(f"RESTOCK: [{store['name']}] {label} -> {product_url}")

    return new_items


def main() -> None:
    state = load_state()
    new_state = {}

    for store in STORES:
        store_items = check_store(store, state)
        new_state.update(store_items)

    save_state(new_state)
    print(f"Checked {len(STORES)} store(s), tracking {len(new_state)} item variants.")


if __name__ == "__main__":
    main()
