"""Watch store items and send a Telegram message when a size is in stock.

Usage:
  python3 watch.py --once                    # check everything once and exit
  python3 watch.py --loop 60 --duration 290  # check every 60s for ~290s
  python3 watch.py --once --dry-run          # print what would be sent
  python3 watch.py --test-telegram           # send a test message

Environment:
  CHAT_BOT_TOKEN, CHAT_ID
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

WRAPPERS = {"ShallowReactive", "Reactive", "Ref", "ShallowRef", "EmptyRef", "EmptyShallowRef"}


def fetch_limestore(url):
    """Fast, standard request for Limestore."""
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept-Language": "ru,en;q=0.8"}
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read().decode("utf-8", "replace")
    except Exception as e:
        print(f"[FETCH ERROR] Limestore fetch failed for {url}: {e}", file=sys.stderr)
        return ""


def fetch_ekonika(url):
    """Dynamically switch between automated browser environments and urllib."""
    from playwright.sync_api import sync_playwright

    session_cookie_str = os.environ.get("SESSION_COOKIE", "")
    if not session_cookie_str:
        raise RuntimeError("SESSION_COOKIE environment variable is missing or empty.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 800},
            locale="ru-RU"
        )

        parsed_cookies = []
        for item in session_cookie_str.split(";"):
            if "=" in item:
                k, v = item.strip().split("=", 1)
                parsed_cookies.append({
                    "name": k,
                    "value": v,
                    "domain": ".ekonika.ru",
                    "path": "/"
                })

        context.add_cookies(parsed_cookies)
        page = context.new_page()
        page.add_init_script("delete navigator.__proto__.webdriver;")

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)  # Wait for scripts to populate layout
            html = page.content()
            if "__NUXT_DATA__" not in html:
                raise RuntimeError("Bypass signature verification failed (Nuxt layout missing).")
            return html
        finally:
            browser.close()


def revive(arr, index, memo):
    """Resolve one entry of a Nuxt/devalue payload into plain Python objects."""
    if index in memo:
        return memo[index]
    value = arr[index]
    if isinstance(value, list):
        if value and isinstance(value[0], str) and value[0] in WRAPPERS:
            memo[index] = revive(arr, value[1], memo)
            return memo[index]
        out = []
        memo[index] = out
        out.extend(revive(arr, i, memo) if isinstance(i, int) and i >= 0 else None for i in value)
        return out
    if isinstance(value, dict):
        out = {}
        memo[index] = out
        for key, i in value.items():
            out[key] = revive(arr, i, memo) if isinstance(i, int) and i >= 0 else None
        return out
    memo[index] = value
    return value


def find_products_in_nuxt(html, url):
    """Extract and revive all product data from the Nuxt payload."""
    match = re.search(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not match:
        raise RuntimeError(f"Page has no __NUXT_DATA__ payload (blocked by Qrator or layout changed): {url}")

    arr = json.loads(match.group(1))
    memo = {}
    products = []

    for i, entry in enumerate(arr):
        if isinstance(entry, dict) and "code" in entry and ("skus" in entry or "offers" in entry):
            products.append(revive(arr, i, memo))

    return products


def sku_in_stock(sku, mode):
    stock = sku.get("stock") or {}
    online, offline = stock.get("online") or 0, stock.get("offline") or 0
    return {"online": online, "offline": offline, "any": online + offline}[mode] > 0


def product_sizes(product, mode):
    """Yield (size, in_stock) for each size from either skus (Lime) or offers (Ekonika)."""
    for sku in product.get("skus") or []:
        yield str((sku.get("size") or {}).get("value", "")), sku_in_stock(sku, mode)
    for offer in product.get("offers") or []:
        # Ekonika uses offer -> title for size, and quantity for stock
        yield str(offer.get("title", "")), (offer.get("quantity") or 0) > 0


def matches_limestore(product, watch):
    """Original Limestore matching logic."""
    code = (watch.get("code") or "").casefold()
    if code and code != (product.get("code") or "").casefold():
        return False
    words = (watch.get("name") or "").casefold().split()
    name = (product.get("name") or "").casefold()
    return all(w in name for w in words)


def matches_ekonika(product, watch):
    """Stricter Ekonika matching logic to filter out recommended products/accessories."""
    prod_code = (product.get("code") or "").casefold()
    watch_code = (watch.get("code") or "").casefold()
    url = (watch.get("url") or "").casefold()

    if watch_code:
        if watch_code != prod_code:
            return False
    elif prod_code and prod_code not in url:
        return False

    words = (watch.get("name") or "").casefold().split()
    name = (product.get("name") or "").casefold()
    return all(w in name for w in words)


def parse_nuxt_data(html, watch, store_name):
    """Universal parser for stores using Nuxt.js."""
    results = []
    mode = watch.get("stock", "online")
    sizes = watch["size"] if isinstance(watch["size"], list) else [watch["size"]]

    for product in find_products_in_nuxt(html, watch["url"]):
        # Route to the correct matching logic based on the store
        if store_name == "Ekonika":
            if not matches_ekonika(product, watch):
                continue
        else:
            if not matches_limestore(product, watch):
                continue

        color = product.get("color") or {}
        if isinstance(color, dict):
            if color.get("value") and isinstance(color["value"], list):
                color_name = color["value"][0].get("name", "")
            else:
                color_name = color.get("name", "")
        else:
            color_name = str(color)

        price = product.get("price_formatted") or (product.get("price") or {}).get("price") or "Price Hidden"
        stocks = list(product_sizes(product, mode))

        for size in sizes:
            wanted = str(size).casefold()
            available = any(ok for s, ok in stocks if s.casefold() == wanted)

            title = product.get("title") or product.get("name")
            desc_color = f" ({color_name})" if color_name else ""
            desc = f"{store_name}: {title}{desc_color}, size {size}, {price}"
            results.append((f"{product.get('code')}|{wanted}", available, desc))

    return results


def check_watch(watch):
    """Fetch URL and process its Nuxt payload."""
    url = watch["url"]

    if "ekonika.ru" in url.lower():
        html = fetch_ekonika(url)
        if not html:
            return []
        return parse_nuxt_data(html, watch, "Ekonika")
    else:
        html = fetch_limestore(url)
        if not html:
            return []
        return parse_nuxt_data(html, watch, "Limestore")


def telegram(text):
    """Dispatches real-time inventory notifications via the Telegram API."""
    token = os.environ.get("CHAT_BOT_TOKEN")
    chat = os.environ.get("CHAT_ID")
    if not token or not chat:
        raise RuntimeError("set CHAT_BOT_TOKEN and CHAT_ID environment variables")

    data = urllib.parse.urlencode({"chat_id": chat, "text": text, "disable_web_page_preview": "true"})
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    with urllib.request.urlopen(url, data=data.encode(), timeout=20) as resp:
        resp.read()


def load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def run_once(watches, state, dry_run):
    overall_success = True

    for watch in watches:
        print(f"\n[EXECUTION] Checking URL: {watch['url']}")
        print(f"[EXECUTION] Target Sizes: {watch['size']}")

        try:
            results = check_watch(watch)

            if not results:
                print(f"[WARNING] No products/sizes matched criteria on the page.")
                continue

            for key, available, desc in results:
                status_label = "🟢 IN STOCK" if available else "🔴 OUT OF STOCK"
                print(f"[RESULT] {status_label} -> {desc}")

                if available and state.get(key) is not True:
                    message = f"In stock: {desc}\n{watch['url']}"
                    if dry_run:
                        print(f"[DRY-RUN ALERT] Would dispatch Telegram: {message}")
                    else:
                        telegram(message)
                        print(f"[NOTIFICATION] Alert successfully sent to Telegram.")

                state[key] = available

        except Exception as exc:
            print(f"[FATAL ITEM ERROR] Failed parsing {watch['url']}: {exc}", file=sys.stderr)
            overall_success = False
            continue

    return overall_success


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="watches.json")
    ap.add_argument("--state", default="state.json")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", type=int, metavar="SECONDS", help="seconds between checks")
    ap.add_argument("--duration", type=int, default=0, help="stop looping after this many seconds")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--test-telegram", action="store_true")
    args = ap.parse_args()

    if args.test_telegram:
        telegram("lime-watch: Telegram connection verified successfully.")
        print("Test confirmation notice sent to Telegram.")
        return 0

    with open(args.config, encoding="utf-8") as f:
        watches = json.load(f)["watches"]
    state = load_state(args.state)

    started = time.time()
    ok = True
    while True:
        ok = run_once(watches, state, args.dry_run) and ok
        with open(args.state, "w", encoding="utf-8") as f:
            json.dump(state, f)
        if args.once or not args.loop or time.time() - started + args.loop >= args.duration:
            break
        time.sleep(args.loop)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())