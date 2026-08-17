# Avito Telegram Notifier

Watches an Avito.ru search page and sends you a Telegram message whenever a new listing
appears, so you can react before someone else does.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Google Chrome installed on the machine (the scraper drives your real Chrome, not a bundled one)
- A Telegram bot token — get one from [@BotFather](https://t.me/BotFather)

## Setup

```bash
uv sync
cp .env.example .env
```

Open `.env` and set `BOT_TOKEN` to the token you got from @BotFather.

(Optional) Edit `sources.yaml` if you want to watch a different category/city — it's just a
name + Avito search URL.

## Run

```bash
uv run python main.py
```

Then open your bot in Telegram and send `/start`. From then on, you'll get a message here
whenever a new listing shows up (checked roughly every 2 minutes).

**Run only one instance at a time** — Telegram doesn't allow two processes to poll for
messages with the same bot token at once.

## Notes

- Already-seen listings are tracked in `listings.csv`. Deleting it doesn't just "reset"
  things — the next check will treat everything currently on the page as new and message you
  about all of it at once.
