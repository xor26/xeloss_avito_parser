from __future__ import annotations

import logging
import random
from dataclasses import dataclass

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

logger = logging.getLogger(__name__)

LOCAL_PRIORITY_SELECTOR = '[data-marker="filters/localPriority/localPriority"]'
ITEM_SELECTOR = '[data-marker="item"]'

# How often to re-check for an element that isn't there yet, and how long to
# keep trying before giving up — the page can take longer than any single
# fixed timeout to render, so we poll patiently instead of failing outright.
POLL_CHECK_INTERVAL_SECONDS = 10.0
POLL_MAX_WAIT_SECONDS = 90.0

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

ACCEPT_LANGUAGE_VARIANTS = [
    "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "ru,en-US;q=0.9,en;q=0.8",
]


class BlockedError(Exception):
    """Raised when Avito serves a captcha/anti-bot wall instead of listings."""


@dataclass(frozen=True)
class Listing:
    id: str
    title: str
    price: int | None
    location: str
    published_at: str
    url: str


class AvitoScraper:
    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    async def start(self) -> None:
        self._playwright = await async_playwright().start()
        # Use the system-installed Chrome instead of Playwright's bundled
        # Chromium — a real Chrome build is less fingerprintable as automation.
        # self._browser = await self._playwright.chromium.launch(headless=True, channel="chrome")
        self._browser = await self._playwright.chromium.launch(headless=False, channel="chrome")
        await self._new_context()

    async def _new_context(self) -> None:
        assert self._browser is not None
        if self._context is not None:
            await self._context.close()
        self._context = await self._browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            locale="ru-RU",
            extra_http_headers={"Accept-Language": random.choice(ACCEPT_LANGUAGE_VARIANTS)},
        )

    async def stop(self) -> None:
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def fetch_listings(self, url: str) -> list[Listing]:
        assert self._context is not None, "call start() first"
        page = await self._context.new_page()
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            if response is not None and response.status == 403:
                raise BlockedError(f"HTTP 403 for {url}")
            if await self._looks_like_captcha(page):
                raise BlockedError(f"Captcha/anti-bot wall detected for {url}")

            await self._wait_until_loaded(page, url)
            await self._apply_local_priority(page, url)

            cards = await page.query_selector_all(ITEM_SELECTOR)
            if not cards:
                raise BlockedError(f"No listing cards found for {url} (page structure changed, or blocked)")

            listings = []
            for card in cards:
                listing = await self._parse_card(card, url)
                if listing is not None:
                    listings.append(listing)
            return listings
        finally:
            await page.close()

    async def _looks_like_captcha(self, page: Page) -> bool:
        title = (await page.title()).lower()
        return "доступ ограничен" in title or "captcha" in title

    async def _poll_for_selector(self, page: Page, selector: str):
        """Check for `selector` right away, and keep re-checking every
        POLL_CHECK_INTERVAL_SECONDS until it shows up or we give up — so a
        slow-to-render page doesn't fail outright, and a fast one is acted
        on within one query, not after a fixed wait."""
        elapsed = 0.0
        while True:
            element = await page.query_selector(selector)
            if element is not None:
                return element
            if elapsed >= POLL_MAX_WAIT_SECONDS:
                return None
            await page.wait_for_timeout(POLL_CHECK_INTERVAL_SECONDS * 1000)
            elapsed += POLL_CHECK_INTERVAL_SECONDS

    async def _wait_until_loaded(self, page: Page, url: str) -> None:
        """Confirm the listing cards are actually rendered before we touch the page."""
        cards = await self._poll_for_selector(page, ITEM_SELECTOR)
        if cards is None:
            raise BlockedError(f"Listing cards never appeared for {url} within {POLL_MAX_WAIT_SECONDS:.0f}s")
        try:
            await page.wait_for_load_state("networkidle", timeout=5_000)
        except PlaywrightTimeoutError:
            logger.debug("networkidle wait timed out for %s, continuing anyway", url)

    async def _apply_local_priority(self, page: Page, url: str) -> None:
        """Click 'Сначала из Ростова-на-Дону' so results aren't diluted with
        out-of-region listings (Avito otherwise mixes in nearby regions).
        The toggle can appear well after the rest of the page — poll for it
        so it gets clicked the moment it shows up, instead of giving up."""
        toggle = await self._poll_for_selector(page, LOCAL_PRIORITY_SELECTOR)
        if toggle is None:
            logger.warning("localPriority toggle never appeared on %s within %.0fs", url, POLL_MAX_WAIT_SECONDS)
            return
        if (await toggle.get_attribute("aria-checked")) == "true":
            return

        await toggle.click()
        try:
            await page.wait_for_function(
                "(sel) => document.querySelector(sel)?.getAttribute('aria-checked') === 'true'",
                arg=LOCAL_PRIORITY_SELECTOR,
                timeout=5_000,
            )
        except PlaywrightTimeoutError:
            logger.warning("localPriority toggle did not confirm as checked on %s", url)
        try:
            await page.wait_for_load_state("networkidle", timeout=5_000)
        except PlaywrightTimeoutError:
            logger.debug("networkidle wait after toggling localPriority timed out for %s", url)

    # Selectors verified against a saved Avito search-results page
    # (example.html). Location/date are sometimes blank in the raw DOM —
    # Avito virtualizes the list and only fully hydrates cards once they've
    # scrolled into view, and promoted ("Продвинуто") listings show the
    # seller name in that slot instead — so both are treated as optional.
    async def _parse_card(self, card, source_url: str) -> Listing | None:
        try:
            item_id = await card.get_attribute("data-item-id")

            title_el = await card.query_selector('[data-marker="item-title"]')
            title = (await title_el.inner_text()).strip() if title_el else ""
            href = await title_el.get_attribute("href") if title_el else None
            link = f"https://www.avito.ru{href}" if href and href.startswith("/") else href

            price_el = await card.query_selector('meta[itemprop="price"]')
            price_content = await price_el.get_attribute("content") if price_el else None
            price = int(price_content) if price_content and price_content.isdigit() else None

            location_el = await card.query_selector('[data-marker="item-location"]')
            location = (await location_el.inner_text()).strip() if location_el else ""

            date_el = await card.query_selector('[data-marker="item-date"]')
            published_at = (await date_el.inner_text()).strip() if date_el else ""

            if not item_id or not title or not link:
                logger.warning("Skipping card with missing required fields on %s", source_url)
                return None

            return Listing(
                id=item_id,
                title=title,
                price=price,
                location=location,
                published_at=published_at,
                url=link,
            )
        except Exception:
            logger.exception("Failed to parse a listing card on %s", source_url)
            return None
