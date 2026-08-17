# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A personal Telegram bot that watches an Avito.ru search-results page (currently: desktop
PCs in Rostov-on-Don) and pushes a message for every listing it hasn't seen before, so the
owner can call sellers before competitors do. See [spec.md](spec.md) for the full original
requirements (in Russian) — not everything in it is implemented yet; treat it as the target,
not the current state.

## Commands

```bash
uv sync                    # install/sync dependencies (managed via pyproject.toml / uv.lock)
uv run python main.py      # run the bot
```

There is no test suite, linter, or build step configured in this project.

Requires the system's Google Chrome to be installed (`channel="chrome"` in
`bot/scraper.py` — Playwright launches the real browser, not its bundled Chromium, since a
genuine Chrome build is less fingerprintable as automation). Secrets live in `.env`
(`BOT_TOKEN`, gitignored) — copy `.env.example` and fill it in.

## Architecture

**`main.py`** owns orchestration — there is no separate scheduler module. `main()`:
starts the `TelegramNotifier`, loads `sources.yaml` and the seen-ID set from `listings.csv`,
starts the `AvitoScraper`, then runs one `poll_source` loop per configured source via
`asyncio.gather`. Each loop: fetch → diff against the in-memory `seen_ids` set → for anything
new, mark it seen, append `{name, link}` to `listings.csv`, and notify Telegram. On a
`BlockedError` it backs off 5–15 minutes instead of retrying immediately; other exceptions are
logged and the loop just continues on its normal ~2-minute interval (`POLL_INTERVAL_MIN/MAX`).

**Storage is a flat CSV, not a database, and only has two columns (`name`, `link`) —
deliberately** (per project owner's request; no SQLite, no extra metadata). Because there's no
`id` column, the listing ID needed for dedup is recovered from the `link` on load
(`extract_id_from_url` in `main.py`: the trailing digit run in the URL path, right before
`?context=...`). `load_seen_ids` passes `fieldnames=CSV_FIELDS` explicitly to `csv.DictReader`
rather than trusting the first line to be a header — the file has been found without one
before (e.g. after an interrupted write), which previously caused a `KeyError` by treating the
first listing as column names.

**`bot/scraper.py` (`AvitoScraper`)** drives one persistent Playwright browser context —
new pages are opened per fetch, but the browser/context itself is reused rather than
relaunched every poll. Two non-obvious things about the target site, discovered by inspecting
a saved copy of the page (see conversation history / spec.md's anti-block section for why
that was necessary — Avito's antibot wall blocks this sandbox intermittently):

- Avito virtualizes the results list — `item-location` and `item-date` are only populated in
  the DOM once a card has scrolled into view (or never, if promoted/"Продвинуто", which shows
  the seller name in that slot instead). Both fields are treated as optional; `id`, `title`,
  `price`, and `link` are always present and are what dedup/parsing relies on.
- There's a page toggle, `[data-marker="filters/localPriority/localPriority"]`
  ("Сначала из Ростова-на-Дону"), that must be clicked to stop out-of-region listings from
  leaking into results (confirmed empirically — Avito otherwise mixes in nearby regions
  despite the URL's region path). `_apply_local_priority` clicks it once per fetch if not
  already checked.

Both the listing cards and that toggle can take longer than any single fixed timeout to
appear, so `_poll_for_selector` checks immediately and then re-checks every
`POLL_CHECK_INTERVAL_SECONDS` (10s) up to `POLL_MAX_WAIT_SECONDS` (90s) rather than using a
one-shot `wait_for_selector(timeout=...)` — a prior version's unguarded fixed timeout was
throwing an unhandled `TimeoutError` out of `fetch_listings` on slow page loads, skipping the
toggle click entirely for that cycle.

**`bot/telegram_bot.py` (`TelegramNotifier`)** learns its target chat from whoever sends
`/start` (stored in memory only, not persisted — resets on restart) and exposes
`notify_new_listing(name, link)` for `main.py` to call. If no chat is known yet, it logs and
drops the notification rather than erroring.

**`bot/config.py`** just loads `sources.yaml` into `Source(id, name, url)` — sources are
config, not hardcoded, so adding another category/city is a YAML edit, not a code change.

## Known gotchas

- Only ever run one instance of `main.py` at a time — Telegram's `getUpdates` long-poll
  allows exactly one consumer per bot token; a second instance causes silent 409 Conflicts and
  the *other* instance ends up swallowing `/start` messages meant for the one you're watching.
- Deleting `listings.csv` doesn't just "reset" tracking — the next fetch will treat every
  listing currently on the page as new and fire a Telegram message for each one (~50 at once).
