"""
London Drugs TCG Stock Monitor (headless browser version)
---------------------------------------------------------------
London Drugs' product pages render stock status entirely client-side
(there's no plain JSON feed like the Shopify stores), and availability
is shown per specific store location. This script uses Playwright to
load real pages in a headless browser, attempt to select the target
store by postal code, and read the rendered stock text.

Disney Lorcana is tracked from a fixed list of product URLs (London
Drugs only carries a handful of SKUs for it). Pokemon is tracked by
auto-discovering products from London Drugs' Pokemon category page,
filtered down to sealed product (boosters, ETBs, bundles, etc) so new
sets get picked up automatically without manually adding URLs.

KNOWN LIMITATIONS (none of this was verified against the live site):
- The store-selection click flow is best-effort.
- The category page pagination/load-more handling is best-effort.
- The sealed-product keyword filter may need tuning.
A full-page screenshot is saved for every product/store check, and the
discovered Pokemon product list is logged, so failures can be
diagnosed and fixed from real evidence rather than more guesswork.
"""

import asyncio
import json
import os
import re
from pathlib import Path
from urllib.parse import urljoin

import requests
from playwright.async_api import async_playwright

STATE_FILE = Path(__file__).parent / "london_drugs_state.json"
SCREENSHOT_DIR = Path(__file__).parent / "screenshots"
NTFY_TOPIC = os.environ.get("NTFY_TOPIC")
BASE_URL = "https://www.londondrugs.com"

STORES = [
    {"name": "Peninsula Village", "postal_code": "V4A 2H9"},
    {"name": "Morgan Crossing", "postal_code": "V3Z 2N6"},
]

# Disney Lorcana: fixed list (London Drugs carries very few SKUs for it).
LORCANA_PRODUCTS = [
    {"name": "Lorcana: Starter Deck", "url": f"{BASE_URL}/products/disney-lorcana-trading-card-game-starter-deck/p/L2554282"},
    {"name": "Lorcana: Shimmering Skies - Assorted", "url": f"{BASE_URL}/products/disney-lorcana-trading-card-game-shimmering-skies-assorted/p/L2715717"},
    {"name": "Lorcana: Archazia's Island - Starter Deck", "url": f"{BASE_URL}/products/disney-lorcana-trading-card-game-s7-archazias-island-starter-deck/p/L2984661"},
    {"name": "Lorcana: Ursula's Return - Starter Deck", "url": f"{BASE_URL}/products/disney-lorcana-trading-card-game-ursulas-return-starter-deck/p/L2664792"},
    {"name": "Lorcana: Shimmering Skies", "url": f"{BASE_URL}/products/disney-lorcana-trading-card-game-shimmering-skies/p/L2715725"},
]

# Pokemon: auto-discovered from this category page instead of a fixed list.
POKEMON_CATEGORY_URL = f"{BASE_URL}/category/pokemon/c/1622"
MAX_DISCOVERED_PRODUCTS = 40  # safety cap so a run can't balloon forever
MAX_LOAD_MORE_CLICKS = 10

SEALED_KEYWORDS = [
    "booster", "elite trainer box", "etb", "bundle", "blister", "tin",
    "premium collection", "collection box", "build & battle", "battle deck",
    "trainer box", "starter deck", "gift set", "trove", "case", "display",
]
EXCLUDE_KEYWORDS = [
    "plush", "figure", "video game", "switch", "backpack", "shirt", "hat",
    "mug", "book", "puzzle", "binder", "sleeve", "playmat", "poster",
]

OUT_OF_STOCK_PATTERNS = ["not in stock", "out of stock", "sold out", "unavailable"]
IN_STOCK_PATTERNS = ["add to cart", "ready for pickup", "in stock"]


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
        print(f"Failed to send notification: {e}")


def looks_like_sealed_pokemon_product(title: str) -> bool:
    title_lower = title.lower()
    if any(bad in title_lower for bad in EXCLUDE_KEYWORDS):
        return False
    return any(good in title_lower for good in SEALED_KEYWORDS)


async def dismiss_cookie_banner(page) -> None:
    for text in ["Accept All Cookies", "Accept All", "Accept", "I Agree", "Got it", "Close"]:
        try:
            btn = page.get_by_text(text, exact=False).first
            if await btn.is_visible(timeout=1500):
                await btn.click(timeout=2000)
                await page.wait_for_timeout(500)
                return
        except Exception:
            continue


async def discover_pokemon_products(page) -> list:
    """Best-effort scan of the Pokemon category page for sealed product
    links. Tries clicking a 'load more' style button repeatedly, then
    falls back to scrolling, before extracting and filtering links."""
    try:
        await page.goto(POKEMON_CATEGORY_URL, wait_until="networkidle", timeout=30000)
    except Exception as e:
        print(f"Could not load Pokemon category page: {e}")
        return []

    await dismiss_cookie_banner(page)

    for _ in range(MAX_LOAD_MORE_CLICKS):
        clicked = False
        for text in ["Load More", "Show More", "View More", "See More"]:
            try:
                btn = page.get_by_text(text, exact=False).first
                if await btn.is_visible(timeout=1500):
                    await btn.click(timeout=2000)
                    await page.wait_for_timeout(1500)
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            try:
                await page.mouse.wheel(0, 4000)
                await page.wait_for_timeout(1000)
            except Exception:
                pass
            break

    try:
        await page.screenshot(path=str(SCREENSHOT_DIR / "pokemon_category_page.png"), full_page=True)
    except Exception:
        pass

    try:
        links = await page.eval_on_selector_all(
            "a[href*='/products/'][href*='/p/']",
            "els => els.map(e => ({href: e.getAttribute('href'), text: e.innerText}))",
        )
    except Exception as e:
        print(f"Could not extract product links: {e}")
        return []

    seen_urls = set()
    discovered = []
    for link in links:
        href = link.get("href") or ""
        text = (link.get("text") or "").strip()
        if not href or not text:
            continue
        full_url = urljoin(BASE_URL, href)
        if full_url in seen_urls:
            continue
        if not looks_like_sealed_pokemon_product(text):
            continue
        seen_urls.add(full_url)
        discovered.append({"name": f"Pokemon: {text}", "url": full_url})
        if len(discovered) >= MAX_DISCOVERED_PRODUCTS:
            break

    print(f"Discovered {len(discovered)} Pokemon sealed product(s) from category page:")
    for item in discovered:
        print(f"  - {item['name']} -> {item['url']}")

    return discovered


async def set_store(page, postal_code: str, label: str) -> bool:
    """Best-effort attempt to select a specific store by postal code.
    Returns True if it believes it succeeded, False otherwise (the
    caller still proceeds and reads whatever state is on screen)."""
    for trigger_text in ["Set your store", "Select a store", "Find a Store", "Change Store", "My Store"]:
        try:
            trigger = page.get_by_text(trigger_text, exact=False).first
            if await trigger.is_visible(timeout=2000):
                await trigger.click(timeout=3000)
                await page.wait_for_timeout(1000)
                break
        except Exception:
            continue

    try:
        postal_input = page.get_by_placeholder(re.compile("postal", re.I)).first
        await postal_input.fill(postal_code, timeout=3000)
        await postal_input.press("Enter")
        await page.wait_for_timeout(2000)
    except Exception as e:
        print(f"[{label}] could not enter postal code: {e}")
        return False

    try:
        set_btn = page.get_by_text("Set as My Store", exact=False).first
        await set_btn.click(timeout=3000)
        await page.wait_for_timeout(1500)
        return True
    except Exception as e:
        print(f"[{label}] could not confirm store selection: {e}")
        return False


async def check_product_at_store(page, product: dict, store: dict, state: dict) -> dict:
    key = f"{store['name']}::{product['name']}"
    safe_name = key.replace("::", "__").replace("/", "-").replace(" ", "_").replace("'", "").replace(":", "")
    screenshot_path = SCREENSHOT_DIR / f"{safe_name}.png"

    try:
        await page.goto(product["url"], wait_until="networkidle", timeout=30000)
    except Exception as e:
        print(f"[{key}] failed to load page: {e}")
        return {key: state.get(key, False)}

    await dismiss_cookie_banner(page)
    store_set = await set_store(page, store["postal_code"], key)
    if not store_set:
        print(f"[{key}] WARNING: store selection may have failed, reading whatever is on screen by default")

    try:
        await page.wait_for_timeout(2000)
        body_text = (await page.inner_text("body")).lower()
    except Exception as e:
        print(f"[{key}] could not read page text: {e}")
        body_text = ""

    try:
        await page.screenshot(path=str(screenshot_path), full_page=True)
    except Exception as e:
        print(f"[{key}] could not save screenshot: {e}")

    available = any(p in body_text for p in IN_STOCK_PATTERNS) and not any(
        p in body_text for p in OUT_OF_STOCK_PATTERNS
    )

    was_available = state.get(key)
    if available and not was_available:
        send_notification(
            title=f"Restock: London Drugs {store['name']}",
            message=f"{product['name']} looks available!",
            url=product["url"],
        )
        print(f"RESTOCK (tentative, verify against screenshot): {key} -> {product['url']}")

    return {key: available}


async def main() -> None:
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    state = load_state()
    new_state = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")

        pokemon_products = await discover_pokemon_products(page)
        all_products = LORCANA_PRODUCTS + pokemon_products

        for store in STORES:
            for product in all_products:
                result = await check_product_at_store(page, product, store, state)
                new_state.update(result)

        await browser.close()

    save_state(new_state)
    print(f"Checked {len(all_products)} product(s) across {len(STORES)} store(s) "
          f"({len(LORCANA_PRODUCTS)} fixed Lorcana + {len(pokemon_products)} discovered Pokemon).")


if __name__ == "__main__":
    asyncio.run(main())
