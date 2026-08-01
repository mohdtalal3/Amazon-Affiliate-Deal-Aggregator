#!/usr/bin/env python3
"""Scrape Amazon deals from taylorsdailydeals.com via its internal JSON API and save to JSON."""

import json
import re
import shutil
from pathlib import Path
from urllib.parse import urlparse

import requests

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


def fetch_all_deals() -> list[dict]:
    """Paginate through all deals via offset, return raw items."""
    all_items = []
    offset = 0
    while True:
        resp = requests.get(
            API_URL,
            params={"limit": PAGE_SIZE, "offset": offset},
            headers=HEADERS,
            timeout=30,
        )
        resp.raise_for_status()
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


def scrape_products(items: list[dict]) -> list[dict]:
    products = []
    for item in items:
        # Skip coupon products
        if item.get("coupon_code"):
            continue

        asin = item.get("asin")
        amazon_url = item.get("canonical_product_url") or item.get("product_url")
        if not amazon_url and asin:
            amazon_url = f"https://www.amazon.com/dp/{asin}"
        if not amazon_url:
            continue

        name = clean_product_name(item.get("clean_title") or item.get("title") or "")
        image_url = (item.get("image_url") or "").strip()
        discount = (item.get("discount_text") or "").strip()
        price = format_price(item)

        products.append(
            {
                "name": name,
                "description": "",
                "amazon_url": amazon_url,
                "image_url": image_url,
                "discount": discount,
                "price": price,
            }
        )
    return products


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
            resp = requests.get(img_url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            img_path.write_bytes(resp.content)
            product["image_path"] = str(img_path.resolve())
            print(f"  [{i}] Downloaded: {img_path.name}")
        except Exception as e:
            print(f"  [{i}] Failed to download {img_url}: {e}")


def main() -> None:
    print(f"Fetching deals from {API_URL} ...")
    items = fetch_all_deals()
    print(f"Found {len(items)} total deals")

    products = scrape_products(items)
    print(f"After filtering coupons: {len(products)} products")

    # Testing limit — remove or increase for production
    ##products = products[:10]
    print(f"Limited to {len(products)} products for testing")

    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\nDownloading images to {DATA_DIR} ...")
    #download_images(products, DATA_DIR)

    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(products, f, indent=2, ensure_ascii=False)
    print(f"Saved JSON to {OUTPUT_FILE}")

    print(f"\nDone — all data in {DATA_DIR}")


if __name__ == "__main__":
    main()
