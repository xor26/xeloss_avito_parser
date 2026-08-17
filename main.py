from __future__ import annotations

import asyncio
import csv
import logging
import os
import random
from pathlib import Path

from dotenv import load_dotenv

from bot.config import Source, load_sources
from bot.scraper import AvitoScraper, BlockedError, Listing
from bot.telegram_bot import TelegramNotifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

POLL_INTERVAL_MIN = 110.0
POLL_INTERVAL_MIN = 30.0
POLL_INTERVAL_MAX = 130.0
POLL_INTERVAL_MAX = 30.0
BLOCKED_COOLDOWN_MIN = 300.0
BLOCKED_COOLDOWN_MAX = 900.0

CSV_PATH = Path("listings.csv")
CSV_FIELDS = ["name", "link"]


def extract_id_from_url(url: str) -> str | None:
    """The listing ID is the trailing digit run in the URL's path, right
    before the `?context=...` query string (e.g. ..._8303087378?context=...)."""
    tail = url.split("?", 1)[0].rsplit("_", 1)[-1]
    return tail if tail.isdigit() else None


def load_seen_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as f:
        # Pass fieldnames explicitly rather than letting DictReader treat the
        # first line as a header — if the file is missing its header row
        # (e.g. from an interrupted write), that would silently swallow the
        # first listing as column names instead of data. A genuine header
        # row just parses as a non-numeric "link" value and gets filtered out.
        ids = (extract_id_from_url(row["link"]) for row in csv.DictReader(f, fieldnames=CSV_FIELDS))
        return {listing_id for listing_id in ids if listing_id is not None}


def append_listing(path: Path, listing: Listing) -> None:
    is_new_file = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if is_new_file:
            writer.writeheader()
        writer.writerow({"name": listing.title, "link": listing.url})


async def poll_source(
    scraper: AvitoScraper, source: Source, seen_ids: set[str], notifier: TelegramNotifier
) -> None:
    loop = asyncio.get_event_loop()
    cooldown_until = 0.0
    while True:
        now = loop.time()
        if now < cooldown_until:
            await asyncio.sleep(cooldown_until - now)
            continue

        try:
            listings = await scraper.fetch_listings(source.url)
            new_listings = [listing for listing in listings if listing.id not in seen_ids]
            for listing in new_listings:
                seen_ids.add(listing.id)
                append_listing(CSV_PATH, listing)
                await notifier.notify_new_listing(listing.title, listing.url)
                logger.info(
                    "NEW [%s] %s | %s | %s | %s",
                    source.id, listing.title, listing.price, listing.location, listing.url,
                )
            logger.info("[%s] fetched %d listings (%d new)", source.id, len(listings), len(new_listings))
        except BlockedError as exc:
            asyncio.sleep(30)
            cooldown = random.uniform(BLOCKED_COOLDOWN_MIN, BLOCKED_COOLDOWN_MAX)
            logger.warning("[%s] blocked (%s) — backing off for %.0fs", source.id, exc, cooldown)
            cooldown_until = loop.time() + cooldown
            continue
        except Exception:
            asyncio.sleep(30)
            logger.exception("[%s] unexpected error while fetching", source.id)

        await asyncio.sleep(random.uniform(POLL_INTERVAL_MIN, POLL_INTERVAL_MAX))


async def main() -> None:
    load_dotenv()
    notifier = TelegramNotifier(os.environ["BOT_TOKEN"])
    await notifier.start()

    sources = load_sources(Path("sources.yaml"))
    seen_ids = load_seen_ids(CSV_PATH)
    scraper = AvitoScraper()
    await scraper.start()
    try:
        await asyncio.gather(*(poll_source(scraper, source, seen_ids, notifier) for source in sources))
    finally:
        asyncio.sleep(30)
        await scraper.stop()
        await notifier.stop()


if __name__ == "__main__":
    asyncio.run(main())
