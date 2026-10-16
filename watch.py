#!/usr/bin/env python3
"""Watch limestore.com items and send a Telegram message when a size is in stock.

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
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
WRAPPERS = {"ShallowReactive", "Reactive", "Ref", "ShallowRef", "EmptyRef", "EmptyShallowRef"}


def fetch(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept-Language": "ru,en;q=0.8"}
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", "replace")


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


def find_products(html):
    """Return every product in the page payload that has a list of skus."""
    match = re.search(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not match:
        raise RuntimeError("page has no __NUXT_DATA__ payload (site layout changed?)")
    arr = json.loads(match.group(1))
    memo = {}
    products = []
    for i, entry in enumerate(arr):
        if isinstance(entry, dict) and "skus" in entry and "code" in entry:
            products.append(revive(arr, i, memo))
    return products


def sku_in_stock(sku, mode):
    stock = sku.get("stock") or {}
    online, offline = stock.get("online") or 0, stock.get("offline") or 0
    return {"online": online, "offline": offline, "any": online + offline}[mode] > 0


def matches(product, watch):
    code = (watch.get("code") or "").casefold()
    if code and code != (product.get("code") or "").casefold():
        return False
    words = (watch.get("name") or "").casefold().split()
    name = (product.get("name") or "").casefold()
    return all(w in name for w in words)


def check_watch(watch):
    """Return a list of (key, available, description) for each matching product."""
    mode = watch.get("stock", "online")
    size = str(watch["size"]).casefold()
    results = []
    for product in find_products(fetch(watch["url"])):
        if not matches(product, watch):
            continue
        skus = [s for s in product["skus"] if str((s.get("size") or {}).get("value", "")).casefold() == size]
        available = any(sku_in_stock(s, mode) for s in skus)
        color = (product.get("color") or {}).get("name") or ""
        desc = f"{product.get('name')} ({color}), size {watch['size']}, {product.get('price_formatted')}"
        results.append((f"{product.get('code')}|{size}", available, desc))
    return results


def telegram(text):
    token, chat = os.environ.get("CHAT_BOT_TOKEN"), os.environ.get("CHAT_ID")
    if not token or not chat:
        raise RuntimeError("set CHAT_BOT_TOKEN and CHAT_ID")
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
    ok = True
    for watch in watches:
        try:
            results = check_watch(watch)
        except Exception as exc:  # network hiccup, layout change, ...
            print(f"error checking {watch['url']}: {exc}", file=sys.stderr)
            ok = False
            continue
        if not results:
            print(f"no product matched {watch}", file=sys.stderr)
            ok = False
        for key, available, desc in results:
            print(f"{'IN STOCK' if available else 'out'}  {desc}")
            if available and state.get(key) is not True:
                message = f"In stock: {desc}\n{watch['url']}"
                if dry_run:
                    print(f"[dry-run] would send: {message}")
                else:
                    telegram(message)
            state[key] = available
    return ok


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
        telegram("lime-watch: Telegram is set up correctly.")
        print("sent")
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
