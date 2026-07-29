#!/usr/bin/env python3
"""Scrape Amazon deals from daveandsissydailydeals.com and save to JSON."""

import json
import re
import shutil
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

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


def clean_amazon_url(href: str) -> str | None:
    """Keep only https://www.amazon.com/dp/ASIN."""
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


def scrape_products(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    products = []

    # Each product lives in a funnel card
    cards = soup.select("div.funnel")

    for card in cards:
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

        products.append(
            {
                "name": name,
                "description": description,
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
            resp = requests.get(img_url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            img_path.write_bytes(resp.content)
            product["image_path"] = str(img_path.resolve())
            print(f"  [{i}] Downloaded: {img_path.name}")
        except Exception as e:
            print(f"  [{i}] Failed to download {img_url}: {e}")


def main() -> None:
    print(f"Fetching {URL} ...")
    resp = requests.get(URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()

    products = scrape_products(resp.text)
    print(f"Found {len(products)} products")

    # Testing limit — remove or increase for production
    products = products[:10]
    print(f"Limited to {len(products)} products for testing")

    # Recreate data directory (replace existing)
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Download images (sets image_path on each product)
    print(f"\nDownloading images to {DATA_DIR} ...")
    download_images(products, DATA_DIR)

    # Save JSON (after images so image_path is included)
    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(products, f, indent=2, ensure_ascii=False)
    print(f"Saved JSON to {OUTPUT_FILE}")

    print(f"\nDone — all data in {DATA_DIR}")


if __name__ == "__main__":
    main()
