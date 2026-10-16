# lime-watch

App that watches a specific item on lime store for a specific size, and sends a telegram message to a specific person.
Python 3 standard library only.

## 1. Telegram bot
1. In Telegram, message `@BotFather`, send `/newbot`, and copy the token.
2. Send any message to your new bot, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `message.chat.id`.
3. Test it locally:

   ```bash
   export CHAT_BOT_TOKEN=... CHAT_ID=...
   python3 watch.py --test-telegram
   ```

## 2. Choose what to watch
Edit `watches.json`. Each entry has:

| field   | meaning |
|---------|---------|
| `url`   | a product page or a catalog page that lists the item |
| `name`  | words that must all appear in the product name (optional) |
| `code`  | exact product code, e.g. `9443_325_001_cernyi` (optional) |
| `size`  | the size to watch, e.g. `S`, `M`, `42` |
| `stock` | `online` (default), `offline` (shops only) or `any` |

Colours are separate products with separate codes, so use `code` to pin one colour.
Try it without sending anything: `python3 watch.py --once --dry-run`

## 3. Run it
**Locally:** `python3 watch.py --loop 60 --duration 100000` (needs your Mac awake).

**GitHub Actions (free):**
1. Push this folder to a new **public** repo (unlimited free minutes).
2. Repo Settings → Secrets and variables → Actions: add `CHAT_BOT_TOKEN` and `CHAT_ID`.
3. Actions tab → `watch` → Run workflow once to check it works.

The workflow starts every 5 minutes and checks every 60 seconds while it runs.
GitHub may start runs late, and disables schedules after 60 days without repo activity.

## Notes
- You get one message when the size flips to in stock, not one per check.
- `robots.txt` disallows crawling `/product*` pages but allows catalog pages, so a catalog URL is the safer choice.
- If the site changes its layout the script exits with an error and logs why.
