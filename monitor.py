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
        "products_json": "https://store.401games.ca/collections/disney-lorcana-sealed-product/products.json?limit=250",
    },
    {
        "name": "401 Games (Palworld)",
        "domain": "https://store.401games.ca",
        "products_json": "https://store.401games.ca/collections/all-palworld-card-game/products.json?limit=250",
    },
    {
        "name": "Face to Face Games",
        "domain": "https://facetofacegames.com",
        "products_json": "https://facetofacegames.com/en-us/collections/lorcana-sealed/products.json?limit=250",
    },
    {
        "name": "Hobbiesville",
        "domain": "https://hobbiesville.com",
        "products_json": "https://hobbiesville.com/collections/disney-lorcana/products.json?limit=250",
    },
    {
        "name": "Hobbiesville (Palworld)",
        "domain": "https://hobbiesville.com",
        "products_json": "https://hobbiesville.com/collections/palworld/products.json?limit=250",
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
        "products_json": "https://ubecard.com/products/disney-lorcana-set-8-reign-of-jafar-booster.json",
    },
]

SEALED_KEYWORDS = [
    "booster box", "booster pack", "booster bundle", "boosters",
    "starter deck", "challenge deck", "trial deck", "deck box",
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
    """Handles both collection feeds and single product feeds."""
    try:
        resp = requests.get(store["products_json"], headers=HEADERS, timeout=20)
        resp.raise_for_status()
        data
