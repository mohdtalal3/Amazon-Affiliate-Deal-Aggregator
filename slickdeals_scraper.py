#!/usr/bin/env python3
"""Scrape Amazon deals from slickdeals.net via its internal JSON API and save to JSON.

Flow:
1. Fetch deals from the slickdeals content-block API.
2. For each deal, fetch the thread page HTML and extract the ASIN from
   the data-aps-asin attribute on the "Get Deal at Amazon" button.
3. Build the Amazon URL from the ASIN.
"""

import json
import os
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

from curl_cffi import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

PROXY = os.environ.get("STATIC_PROXY", "")  # e.g. http://user:pass@host:port

PAGE_URL = "https://slickdeals.net/browse/top-retail-deals/?src=trendingbar"
API_BASE = "https://slickdeals.net/web-api/dxp/content-block/"
DATA_DIR = Path(__file__).resolve().parent / "scraping data" / "slickdeals_data"
OUTPUT_FILE = DATA_DIR / "slickdeals_deals.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": PAGE_URL,
}


MAX_RETRIES = 3


def _fetch_with_retry(url: str, **kwargs) -> requests.Response:
    """GET request with up to MAX_RETRIES attempts."""
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, **kwargs)
            resp.raise_for_status()
            return resp
        except Exception as e:
            last_exc = e
            if attempt < MAX_RETRIES:
                print(f"    Retry {attempt}/{MAX_RETRIES} for {url[:70]}... ({e})")
                time.sleep(1 * attempt)
    raise last_exc


def clean_product_name(title: str) -> str:
    """Clean slickdeals deal title to extract just the product name.

    Examples:
      '[NEW] $88.56 | Homestyles Mobile Kitchen Island Cart ... at Amazon'
      -> 'Homestyles Mobile Kitchen Island Cart ...'
    """
    # Remove leading [TAG] like [NEW], [HOT], etc.
    title = re.sub(r"^\[[^\]]+\]\s*", "", title)
    # Remove price prefix before | separator
    if "|" in title:
        title = title.split("|", 1)[1].strip()
    # Remove trailing "at Amazon" / "at Amazon.com"
    title = re.sub(r"\s+at\s+Amazon(?:\.com)?\s*$", "", title, flags=re.I)
    return title.strip()


def format_price(item: dict) -> str:
    """Build a price string like '$89 (reg. $316)'."""
    deal_price = (item.get("finalPriceText") or "").strip()
    reg_price = (item.get("listPriceText") or "").strip()
    if not deal_price:
        return reg_price
    if reg_price and reg_price != deal_price:
        return f"{deal_price} (reg. {reg_price})"
    return deal_price


def format_discount(item: dict) -> str:
    """Build a discount string like '71% off'."""
    discount = item.get("discount")
    if discount:
        return f"{discount}% off"
    return ""


def discover_api_endpoint() -> tuple[str, dict]:
    """Fetch the top-retail-deals page and extract the Amazon content-block
    feed ID and block ID from the raw HTML using the known JS pattern:
    "topretaildeals-amazon","{hash}","/web-api/dxp/content-block/{feedId}/{blockId}"

    Returns (api_url, params) for the deals API call.
    """
    print(f"Fetching page {PAGE_URL} ...")
    resp = _fetch_with_retry(PAGE_URL, headers=HEADERS, timeout=30, impersonate="chrome", proxy=PROXY or None)

    html = resp.text

    # Slickdeals now JSON-escapes slashes as \u002F in the embedded config,
    # so match both escaped and literal slashes.
    pattern = re.compile(
        r'"topretaildeals-amazon"\s*,\s*'
        r'"[a-f0-9]+"\s*,\s*'
        r'"(?:\\u002F|/)web-api(?:\\u002F|/)dxp(?:\\u002F|/)content-block(?:\\u002F|/)'
        r'([a-f0-9]+)(?:\\u002F|/)([a-f0-9]+)',
        re.IGNORECASE,
    )
    match = pattern.search(html)
    if not match:
        raise RuntimeError("Could not find Amazon content block IDs in page HTML")

    feed_id, block_id = match.group(1), match.group(2)
    print(f"  → Feed ID: {feed_id}, Block ID: {block_id}")

    api_url = f"{API_BASE}{feed_id}/{block_id}/"
    params = {"src": "topretaildeals-amazon"}
    return api_url, params


def fetch_deals() -> list[dict]:
    """Fetch deals from the slickdeals content-block API (dynamically discovered)."""
    api_url, params = discover_api_endpoint()
    print(f"Fetching deals from {api_url} ...")
    resp = _fetch_with_retry(api_url, params=params, headers=HEADERS, timeout=30, impersonate="chrome", proxy=PROXY or None)
    data = resp.json()
    deals = data.get("deals", [])
    print(f"Found {len(deals)} deals")
    return deals


def fetch_asin(thread_url: str) -> str | None:
    """Fetch a slickdeals thread page and extract the Amazon ASIN.

    The ASIN is in the data-aps-asin attribute on the 'Get Deal at Amazon'
    button element.
    """
    # Build full URL and strip query params
    if thread_url.startswith("/"):
        full_url = f"https://slickdeals.net{thread_url}"
    else:
        full_url = thread_url
    parsed = urlparse(full_url)
    clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

    try:
        resp = _fetch_with_retry(clean_url, headers=HEADERS, timeout=30, impersonate="chrome", proxy=PROXY or None)
    except Exception as e:
        print(f"    Request failed after retries: {e}")
        return None

    html = resp.text

    # Try BeautifulSoup parse first
    soup = BeautifulSoup(html, "html.parser")
    el = soup.find(attrs={"data-aps-asin": True})
    if el and el.get("data-aps-asin"):
        return el["data-aps-asin"]

    # Fallback: regex search in raw HTML
    match = re.search(r'data-aps-asin="([A-Z0-9]{10})"', html)
    if match:
        return match.group(1)

    return None


def _process_deal(i: int, deal: dict) -> dict | None:
    """Process a single deal: fetch ASIN and build product dict. Returns None if skipped."""
    if deal.get("storeName", "").lower() != "amazon":
        return None
    if deal.get("isExpired"):
        return None

    thread_url = deal.get("dealThreadUrl", "")
    if not thread_url:
        return None

    raw_title = deal.get("dealTitle") or ""
    print(f"  [{i}] Fetching ASIN: {raw_title[:60]}...")

    asin = fetch_asin(thread_url)
    if not asin:
        print(f"      No ASIN found — skipping")
        return None
    print(f"      ASIN: {asin}")

    amazon_url = f"https://www.amazon.com/dp/{asin}"
    name = clean_product_name(raw_title)
    image_url = (deal.get("dealImageUrl") or "").strip()
    discount = format_discount(deal)
    price = format_price(deal)

    return {
        "name": name,
        "description": "",
        "amazon_url": amazon_url,
        "image_url": image_url,
        "discount": discount,
        "price": price,
    }


def scrape_products(deals: list[dict]) -> list[dict]:
    """Build product dicts from slickdeals deals, fetching ASIN per deal (threaded)."""
    products = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(_process_deal, i, deal): i
            for i, deal in enumerate(deals, start=1)
        }
        for future in as_completed(futures):
            result = future.result()
            if result:
                products.append(result)
    return products


def sanitize_filename(name: str) -> str:
    """Make a safe filename from a product name."""
    safe = re.sub(r'[<>:"/\\|?*\n\r\t]', "_", name).strip().strip(".")
    return safe or "unnamed"


def download_images(products: list[dict], dest_dir: Path) -> None:
    """Download each product image, named by product name."""
    for i, product in enumerate(products, start=1):
        img_url = product.get("image_url", "")
        if not img_url:
            print(f"  [{i}] No image URL — skipping download")
            continue

        name = sanitize_filename(product.get("name", f"product_{i}"))
        ext = Path(urlparse(img_url).path).suffix or ".jpg"
        if ext.lower() not in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"):
            ext = ".jpg"

        img_path = dest_dir / f"{name}{ext}"

        counter = 1
        while img_path.exists():
            img_path = dest_dir / f"{name}_{counter}{ext}"
            counter += 1

        try:
            resp = _fetch_with_retry(img_url, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=30, impersonate="chrome", proxy=None)
            img_path.write_bytes(resp.content)
            product["image_path"] = str(img_path.resolve())
            print(f"  [{i}] Downloaded: {img_path.name}")
        except Exception as e:
            print(f"  [{i}] Failed to download {img_url}: {e}")


def main() -> None:
    deals = fetch_deals()
    total_deals = len(deals)
    amazon_deals = sum(1 for d in deals if d.get("storeName", "").lower() == "amazon" and not d.get("isExpired"))

    products = scrape_products(deals)

    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\nDownloading images to {DATA_DIR} ...")
    download_images(products, DATA_DIR)

    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(products, f, indent=2, ensure_ascii=False)
    print(f"Saved JSON to {OUTPUT_FILE}")

    with_images = sum(1 for p in products if p.get("image_path"))

    print(f"\n{'='*50}")
    print(f"  SUMMARY — slickdeals.net")
    print(f"{'='*50}")
    print(f"  Total deals fetched       : {total_deals}")
    print(f"  Amazon deals (non-expired): {amazon_deals}")
    print(f"  With ASIN extracted       : {len(products)}")
    print(f"  With images downloaded    : {with_images}")
    print(f"  Without images            : {len(products) - with_images}")
    print(f"{'='*50}")
    print(f"Done — all data in {DATA_DIR}")


if __name__ == "__main__":
    main()
