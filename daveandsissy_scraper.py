#!/usr/bin/env python3
"""Scrape Amazon deals from daveandsissydailydeals.com and save to JSON."""

import json
import os
import re
import shutil
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

PROXY = os.environ.get("STATIC_PROXY", "")
_PROXIES = {"http": PROXY, "https": PROXY} if PROXY else None

URL = "https://daveandsissydailydeals.com/amazon-deals"
DATA_DIR = Path(__file__).resolve().parent / "scraping data" / "daveandsissy_data"
OUTPUT_FILE = DATA_DIR / "daveandsissy_deals.json"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
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


def clean_amazon_url(href: str) -> str | None:
    """Keep only https://www.amazon.com/dp/ASIN or /promocode/CODE URLs."""
    if not href:
        return None
    match = re.search(r"(https?://(?:www\.)?amazon\.com/dp/[A-Z0-9]{10})", href, re.I)
    if match:
        return match.group(1)
    # Fallback: parse path for /dp/ASIN
    parsed = urlparse(href)
    dp_match = re.search(r"/dp/([A-Z0-9]{10})", parsed.path, re.I)
    if dp_match:
        return f"https://www.amazon.com/dp/{dp_match.group(1).upper()}"
    # Promocode links: keep full URL (contains the promo code in path)
    promo_match = re.search(r"(https?://(?:www\.)?amazon\.com/promocode/[A-Z0-9]+)", href, re.I)
    if promo_match:
        return promo_match.group(1)
    return None


def parse_details(details_el) -> tuple[str, str]:
    """
    details text looks like:
      50% Off Cooling Towels
      50% Off Code 48OUJGYG
      $5.00 (Reg. $9.99) Prime
    First N-1 lines -> discount (joined by \\n), last line -> price.
    """
    if not details_el:
        return "", ""

    # Replace <br> with newlines, drop <hr>
    for hr in details_el.find_all("hr"):
        hr.decompose()
    for br in details_el.find_all("br"):
        br.replace_with("\n")

    text = details_el.get_text("\n", strip=True)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return "", ""
    if len(lines) == 1:
        # Single line: treat as price if it looks like price, else discount
        if lines[0].startswith("$"):
            return "", lines[0]
        return lines[0], ""

    discount = "\n".join(lines[:-1])
    price = lines[-1]
    return discount, price


def scrape_products(html: str) -> tuple[int, list[dict]]:
    """Parse HTML, return (total_cards_found, filtered_products)."""
    soup = BeautifulSoup(html, "html.parser")
    products = []
    total_cards = 0

    # Each product lives in a funnel card
    cards = soup.select("div.funnel")

    for card in cards:
        total_cards += 1
        # Amazon URL from image link or SEE DEAL button
        link = card.select_one("a.image-link[href], a.btn-aqua[href]")
        href = link["href"] if link and link.has_attr("href") else ""
        amazon_url = clean_amazon_url(href)
        if not amazon_url:
            continue

        img = card.select_one("img.image-custom, a.image-link img")
        name = (img.get("alt") or "").strip() if img else ""
        image_url = (img.get("src") or "").strip() if img else ""

        commentary = card.select_one("div.commentary")
        description = commentary.get_text(" ", strip=True) if commentary else ""

        details = card.select_one("div.details")
        discount, price = parse_details(details)

        # Extract coupon code if present (e.g. "50% Off Code 48OUJGYG")
        coupon_code = ""
        if discount:
            code_match = re.search(r"\bCode\s+([A-Z0-9]+)", discount, re.I)
            if code_match:
                coupon_code = code_match.group(1)

        products.append(
            {
                "name": name,
                "description": description,
                "amazon_url": amazon_url,
                "image_url": image_url,
                "discount": discount,
                "price": price,
                "coupon_code": coupon_code,
            }
        )

    return total_cards, products


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
        # Determine extension from URL or default to .jpg
        ext = Path(urlparse(img_url).path).suffix or ".jpg"
        if ext.lower() not in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"):
            ext = ".jpg"

        img_path = dest_dir / f"{name}{ext}"

        # Avoid collision
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
    print(f"Fetching {URL} ...")
    resp = _fetch_with_retry(URL, headers=HEADERS, timeout=30)  # uses proxy by default

    total_found, products = scrape_products(resp.text)
    with_coupon = sum(1 for p in products if p.get("coupon_code"))

    # Recreate data directory (replace existing)
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    #products = products[:5]
    # Download images (sets image_path on each product)
    print(f"\nDownloading images to {DATA_DIR} ...")
    download_images(products, DATA_DIR)

    # Save JSON (after images so image_path is included)
    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(products, f, indent=2, ensure_ascii=False)
    print(f"Saved JSON to {OUTPUT_FILE}")

    with_images = sum(1 for p in products if p.get("image_path"))

    print(f"\n{'='*50}")
    print(f"  SUMMARY — daveandsissydailydeals")
    print(f"{'='*50}")
    print(f"  Total products found      : {total_found}")
    print(f"  Products with coupon code : {with_coupon}")
    print(f"  Products without coupon   : {len(products) - with_coupon}")
    print(f"  With images downloaded    : {with_images}")
    print(f"  Without images            : {len(products) - with_images}")
    print(f"{'='*50}")
    print(f"Done — all data in {DATA_DIR}")


if __name__ == "__main__":
    main()
