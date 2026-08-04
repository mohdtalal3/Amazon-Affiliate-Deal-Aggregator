"""Slack notification helper for scraper pipeline events."""

import os
from curl_cffi import requests

try:
    from dotenv import load_dotenv
    from pathlib import Path
    load_dotenv(Path(__file__).parent / ".env")
except Exception:
    pass

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")


def send_slack_message(text: str) -> None:
    """Send a simple text message to Slack via webhook."""
    if not SLACK_WEBHOOK_URL:
        return
    try:
        requests.post(
            SLACK_WEBHOOK_URL,
            json={"text": text},
            timeout=10,
            impersonate="chrome",
        )
    except Exception:
        pass


def notify_scraper_start(site_name: str) -> None:
    """Send a Slack notification when a scraper starts."""
    send_slack_message(f"🔄 Scraper started: *{site_name}*")


def notify_scraper_complete(
    site_name: str,
    total_found: int,
    product_count: int,
    with_coupon: int = 0,
    filtered_count: int = 0,
) -> None:
    """Send a Slack notification when a scraper completes.

    Args:
        site_name: Display name of the site.
        total_found: Total items found before any filtering.
        product_count: Final number of products after filtering.
        with_coupon: Number of products that have a coupon code.
        filtered_count: Number of items filtered out (e.g. by coupon or discount threshold).
    """
    lines = [f"✅ Scraper complete: *{site_name}*"]
    lines.append(f"   Total found: {total_found}")
    if filtered_count:
        lines.append(f"   Filtered out: {filtered_count}")
    lines.append(f"   Final products: {product_count}")
    if with_coupon:
        lines.append(f"   With coupon code: {with_coupon}")
    send_slack_message("\n".join(lines))


def notify_scraper_error(site_name: str, error: str) -> None:
    """Send a Slack notification when a scraper fails."""
    send_slack_message(f"❌ Scraper failed: *{site_name}*\n   Error: {error}")
