"""
Lorcana Restock Monitor
-----------------------
Polls Shopify-based Canadian game store collections for Disney Lorcana
stock, and sends a push notification (via ntfy.sh) the moment something
goes from "out of stock" to "in stock".
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
        "products_json": "https://store.401games.ca/collections/disney-lorcana-trading-card-game/products.json?limit=250",
    },
    {
        "name": "Face to Face Games",
        "domain": "https://facetofacegames.com",
        "products_json": "https://facetofacegames.com/en-us/collections/lorcana/products.json?limit=250",
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
]

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
    try:
        resp = requests.get(store["products_json"], headers=HEADERS, timeout=20)
        resp.raise_for_status()
        products = resp.json().get("products", [])
    except (requests.RequestException, ValueError) as e:
        print(f"[{store['name']}] fetch failed: {e}", file=sys.stderr)
        return {}

    new_items = {}
    for product in products:
        handle = product.get("handle", "")
        product_url = f"{store['domain']}/products/{handle}"
        title = product.get("title", "Unknown item")

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
