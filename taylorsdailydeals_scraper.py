#!/usr/bin/env python3
"""Scrape Amazon deals from taylorsdailydeals.com via its internal JSON API and save to JSON."""

import json
import os
import re
import shutil
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

load_dotenv()

PROXY = os.environ.get("STATIC_PROXY", "")
_PROXIES = {"http": PROXY, "https": PROXY} if PROXY else None

API_URL = "https://www.taylorsdailydeals.com/api/deals"
PAGE_URL = "https://www.taylorsdailydeals.com/deals"
DATA_DIR = Path(__file__).resolve().parent / "scraping data" / "taylorsdailydeals_data"
OUTPUT_FILE = DATA_DIR / "taylorsdailydeals_deals.json"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "referer": PAGE_URL,
}

PAGE_SIZE = 50
MAX_RETRIES = 3


def _fetch_with_retry(url: str, use_proxy: bool = True, **kwargs) -> requests.Response:
    """GET request with up to MAX_RETRIES attempts and progressive backoff."""
    if use_proxy and _PROXIES and "proxies" not in kwargs:
        kwargs["proxies"] = _PROXIES
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
    """Strip leading '58% off ', '$5 off ', etc. from the title."""
    return re.sub(r"^\d+%?\s+off\s+", "", title, flags=re.I).strip()


def format_price(item: dict) -> str:
    """e.g. '$7.99 (reg. $18.99)'."""
    deal_price = item.get("deal_price_min")
    reg_price = item.get("regular_price_min")
    if deal_price is None:
        return ""
    price = f"${deal_price:g}"
    if reg_price is not None and reg_price != deal_price:
        price += f" (reg. ${reg_price:g})"
    return price


def discount_pct(item: dict) -> float | None:
    """Calculate discount percentage from deal and regular price. Returns None if not computable."""
    deal_price = item.get("deal_price_min")
    reg_price = item.get("regular_price_min")
    if deal_price is None or reg_price is None or reg_price <= 0:
        return None
    return (1 - deal_price / reg_price) * 100


def fetch_all_deals() -> list[dict]:
    """Paginate through all deals via offset, return raw items."""
    all_items = []
    offset = 0
    while True:
        resp = _fetch_with_retry(
            API_URL,
            params={"limit": PAGE_SIZE, "offset": offset},
            headers=HEADERS,
            timeout=30,
        )
        data = resp.json()
        items = data.get("items", [])
        all_items.extend(items)
        print(f"  Fetched {len(items)} deals (offset={offset}, total so far={len(all_items)})")

        if not data.get("hasMore"):
            break
        offset = data.get("nextOffset", offset + PAGE_SIZE)
        if offset >= data.get("count", 0):
            break
    return all_items


def scrape_products(items: list[dict]) -> tuple[int, list[dict]]:
    """Filter and format deal items, return (total_items, filtered_products)."""
    products = []
    total_items = len(items)
    for item in items:
        asin = item.get("asin")
        amazon_url = item.get("canonical_product_url") or item.get("product_url")
        if not amazon_url and asin:
            amazon_url = f"https://www.amazon.com/dp/{asin}"
        if not amazon_url:
            continue

        pct = discount_pct(item)
        if pct is None or pct < 60:
            continue

        name = clean_product_name(item.get("clean_title") or item.get("title") or "")
        image_url = (item.get("image_url") or "").strip()
        discount = (item.get("discount_text") or "").strip()
        price = format_price(item)
        coupon_code = (item.get("coupon_code") or "").strip()

        products.append(
            {
                "name": name,
                "description": "",
                "amazon_url": amazon_url,
                "image_url": image_url,
                "discount": discount,
                "price": price,
                "coupon_code": coupon_code,
            }
        )
    return total_items, products


def sanitize_filename(name: str) -> str:
    """Make a safe filename from a product name."""
    safe = re.sub(r'[<>:"/\\|?*\n\r\t]', '_', name).strip().strip('.')
    return safe or "unnamed"


def download_images(products: list[dict], dest_dir: Path) -> None:
    """Download each product image, named by product name.
    Sets 'image_path' (full local path) on each product dict."""
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
            resp = _fetch_with_retry(img_url, use_proxy=False, headers=HEADERS, timeout=30)
            img_path.write_bytes(resp.content)
            product["image_path"] = str(img_path.resolve())
            print(f"  [{i}] Downloaded: {img_path.name}")
        except Exception as e:
            print(f"  [{i}] Failed to download {img_url}: {e}")


def main() -> None:
    print(f"Fetching deals from {API_URL} ...")
    items = fetch_all_deals()
    print(f"Found {len(items)} total deals")

    total_found, products = scrape_products(items)
    filtered_low_discount = total_found - len(products)
    with_coupon = sum(1 for p in products if p.get("coupon_code"))

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
    print(f"  SUMMARY — taylorsdailydeals.com")
    print(f"{'='*50}")
    print(f"  Total products found      : {total_found}")
    print(f"  Filtered (<60% discount)  : {filtered_low_discount}")
    print(f"  After discount filter     : {len(products)}")
    print(f"  Products with coupon code : {with_coupon}")
    print(f"  Products without coupon   : {len(products) - with_coupon}")
    print(f"  With images downloaded    : {with_images}")
    print(f"  Without images            : {len(products) - with_images}")
    print(f"{'='*50}")
    print(f"Done — all data in {DATA_DIR}")


if __name__ == "__main__":
    main()
