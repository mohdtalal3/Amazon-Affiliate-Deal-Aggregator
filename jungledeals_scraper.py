#!/usr/bin/env python3
"""Scrape Amazon deals from jungle.deals (Jungle Deals Blog) via its internal JSON API and save to JSON."""

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

API_URL = "https://www.jungle.deals/api/get-page/best-selling-deals"
PAGE_URL = "https://www.jungle.deals/p/best-selling-deals"
DATA_DIR = Path(__file__).resolve().parent / "scraping data" / "jungledeals_data"
OUTPUT_FILE = DATA_DIR / "jungledeals_deals.json"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "x-origin": "frontend",
    "referer": PAGE_URL,
}

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


def amazon_url_from_asin(asin: str) -> str | None:
    if not asin:
        return None
    return f"https://www.amazon.com/dp/{asin.upper()}"


def format_price(deal: dict) -> str:
    """e.g. '$14.38 (reg. $14.38)' or '$25.40 (reg. $27.99)'."""
    current = deal.get("currentPrice")
    list_price = deal.get("listPrice")
    if current is None:
        return ""
    price = f"${current:g}"
    if list_price is not None and list_price != current:
        price += f" (reg. ${list_price:g})"
    return price


def format_discount(deal: dict) -> str:
    """Build discount/promo lines similar to the site's on-page text."""
    lines = []

    # coupon_pct = deal.get("couponPercentage")
    # if coupon_pct:
    #     lines.append(f"Click to instantly clip the {coupon_pct}% off coupon")

    # max_ss = deal.get("maxSs")
    # if deal.get("ss") and max_ss:
    #     lines.append(f"Subscribe & Save to get up to an extra {max_ss}% off")

    final_price = deal.get("finalPrice")
    if final_price is not None:
        lines.append(f"Final price as low as ${final_price:g} shipped!")

    extra_deal = deal.get("extraDeal")
    if extra_deal:
        lines.append(f"Also eligible for promotion: {extra_deal}")

    return "\n".join(lines)


def scrape_products(payload: dict) -> tuple[int, list[dict]]:
    """Parse API payload, return (total_deals_found, filtered_products)."""
    deals = payload.get("deals", [])
    products = []
    total_deals = len(deals)

    for deal in deals:
        if deal.get("promoCode"):
            continue

        asin = deal.get("ASIN")
        amazon_url = amazon_url_from_asin(asin)
        if not amazon_url:
            continue

        name = (deal.get("title") or "").strip()
        image_url = (
            deal.get("highResImage")
            or deal.get("image600Url")
            or deal.get("image")
            or ""
        ).strip()

        products.append(
            {
                "name": name,
                "description": "",
                "amazon_url": amazon_url,
                "image_url": image_url,
                "discount": format_discount(deal),
                "price": format_price(deal),
            }
        )

    return total_deals, products


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
    print(f"Fetching {API_URL} ...")
    resp = _fetch_with_retry(API_URL, headers=HEADERS, timeout=30)
    payload = resp.json()

    total_found, products = scrape_products(payload)
    coupon_filtered = total_found - len(products)

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
    print(f"  SUMMARY — jungle.deals")
    print(f"{'='*50}")
    print(f"  Total products found      : {total_found}")
    print(f"  Coupon products filtered  : {coupon_filtered}")
    print(f"  After coupon filtering    : {len(products)}")
    print(f"  With images downloaded    : {with_images}")
    print(f"  Without images            : {len(products) - with_images}")
    print(f"{'='*50}")
    print(f"Done — all data in {DATA_DIR}")


if __name__ == "__main__":
    main()
