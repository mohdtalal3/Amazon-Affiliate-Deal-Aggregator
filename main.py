#!/usr/bin/env python3
"""
Aggregate pipeline: run all scrapers sequentially → aggregate all products →
upload all images → publish everything to ONE WordPress page.

Usage:
  python main.py                          # scrape all + publish (draft)
  python main.py --publish                # scrape all + publish live
  python main.py --page-id 304402         # override aggregate page ID
  python main.py --skip-scrape            # use existing scraped data
  python main.py --site-id daveandsissy   # scrape only one site (still publishes to aggregate page)
"""

import importlib
import json
import os
import re
import sys
import argparse
from pathlib import Path
from typing import Optional
from datetime import datetime

import requests

# Load .env
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except Exception:
    pass

from wordpress_publisher import WordPressPublisher
from categorize import categorize_products, CATEGORIES

DEFAULT_PAGE_ID = 304402
SITES_FILE = Path(__file__).resolve().parent / "sites_config.json"
AGGREGATE_JSON = Path(__file__).resolve().parent / "scraping data" / "aggregated_products.json"


# ── CONFIG HELPERS ────────────────────────────────────────────────────────────

def load_config() -> dict:
    if not SITES_FILE.exists():
        return {"sites": [], "aggregate_page_id": DEFAULT_PAGE_ID, "wp_env_prefix": "RS"}
    with open(SITES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(data: dict) -> None:
    with open(SITES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_site_config(site_id: str) -> Optional[dict]:
    data = load_config()
    return next((s for s in data.get("sites", []) if s["id"] == site_id), None)


def load_scraper(scraper_module_name: str):
    """Dynamically import a scraper module by name."""
    return importlib.import_module(scraper_module_name)


# ── HTML BUILDERS ─────────────────────────────────────────────────────────────

def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "other"


def _parse_prices(price: str) -> tuple[float | None, float | None]:
    """Parse a price string like '$27.99 (reg. $54.99)' into (deal_price, reg_price)."""
    if not price:
        return None, None
    deal_match = re.search(r"\$\s*([\d,]+(?:\.\d+)?)", price)
    reg_match = re.search(r"reg\.?\s*\$\s*([\d,]+(?:\.\d+)?)", price, re.I)
    deal_price = float(deal_match.group(1).replace(",", "")) if deal_match else None
    reg_price = float(reg_match.group(1).replace(",", "")) if reg_match else None
    return deal_price, reg_price


def render_carousel_card(item_index: int, product: dict) -> str:
    """Render a single product as a compact carousel card."""
    name = product.get("name", "").strip()
    price = product.get("price", "")
    discount = product.get("discount", "").strip()
    amazon_url = product.get("amazon_url", "")
    img_url = product.get("_img_url")

    title = name if name else f"Item #{item_index}"
    title = title[0].upper() + title[1:] if title else title
    title_safe = title.replace('"', '&quot;')

    deal_price, reg_price = _parse_prices(price)
    discount_pct = None
    savings = None
    if deal_price is not None and reg_price is not None and reg_price > deal_price > 0:
        discount_pct = round((reg_price - deal_price) / reg_price * 100)
        savings = reg_price - deal_price

    html = '<div class="carousel-card">'

    if img_url:
        html += f'<div class="carousel-card-img"><img src="{img_url}" alt="{title_safe}" loading="lazy" />'
        if discount_pct:
            html += f'<span class="carousel-card-badge">-{discount_pct}% OFF</span>'
        html += '</div>'

    html += f'<div class="carousel-card-title">{title}</div>'

    if deal_price is not None:
        html += '<div class="carousel-card-price-row">'
        html += f'<span class="carousel-card-price">${deal_price:,.2f}</span>'
        if reg_price is not None and reg_price > deal_price:
            html += f'<span class="carousel-card-price-reg">${reg_price:,.2f}</span>'
        html += '</div>'
    elif price:
        html += f'<div class="carousel-card-price">{price}</div>'

    if savings is not None and discount_pct is not None:
        html += f'<div class="carousel-card-save">Save ${savings:,.2f} ({discount_pct}%)</div>'

    if discount:
        discount_lines = [line.strip() for line in discount.split("\n") if line.strip()]
        # Drop lines that just repeat the product name (redundant with the title above)
        if title:
            discount_lines = [line for line in discount_lines if title.lower() not in line.lower()]
        for line in discount_lines:
            html += f'<div class="carousel-card-discount">{line}</div>'

    if amazon_url:
        html += '<div class="carousel-card-spacer"></div>'
        html += (
            f'<a href="{amazon_url}" target="_blank" rel="noopener noreferrer" '
            f'class="carousel-card-btn">See Deal on Amazon &amp; Claim Offer →</a>'
        )

    html += '</div>'
    return html


CAROUSEL_STYLE_AND_SCRIPT = """
<style>
.deals-carousel-page .category-nav {
    display: flex; gap: 8px; flex-wrap: wrap; justify-content: center;
    margin: 20px 0 32px;
}
.deals-carousel-page .category-nav a {
    padding: 8px 16px; background: #f5f5f5; border-radius: 20px;
    text-decoration: none; color: #333; font-size: 13px; font-weight: 600;
    border: 1px solid #ddd; transition: all 0.15s;
}
.deals-carousel-page .category-nav a:hover {
    background: #e63c3c; color: #fff; border-color: #e63c3c;
}
.deals-carousel-page .deal-category-section { margin-bottom: 40px; scroll-margin-top: 80px; }
.deals-carousel-page .deal-category-section h2 {
    font-size: 21px; margin: 0 0 16px; padding-left: 12px;
    border-left: 4px solid #e63c3c;
}
.deals-carousel-page .carousel-wrapper { position: relative; }
.deals-carousel-page .carousel-track {
    display: flex; gap: 16px; overflow-x: auto; scroll-behavior: smooth;
    padding: 4px 4px 16px; scrollbar-width: none;
}
.deals-carousel-page .carousel-track::-webkit-scrollbar { display: none; }
.deals-carousel-page .carousel-arrow {
    position: absolute; top: 42%; transform: translateY(-50%);
    width: 36px; height: 36px; border-radius: 50%; border: 1px solid #eee;
    background: #fff; box-shadow: 0 2px 8px rgba(0,0,0,0.15);
    color: #333; cursor: pointer; z-index: 10;
    display: flex; align-items: center; justify-content: center;
    padding: 0;
}
.deals-carousel-page .carousel-arrow svg { width: 16px; height: 16px; display: block; }
.deals-carousel-page .carousel-arrow:hover { background: #e63c3c; color: #fff; }
.deals-carousel-page .carousel-arrow.left { left: -4px; }
.deals-carousel-page .carousel-arrow.right { right: -4px; }
.deals-carousel-page .carousel-card {
    flex: 0 0 240px; background: #fff; border: 1px solid #eee; border-radius: 10px;
    padding: 14px; box-shadow: 0 2px 6px rgba(0,0,0,0.05); display: flex;
    flex-direction: column;
}
.deals-carousel-page .carousel-card-img {
    height: 150px; display: flex; align-items: center; justify-content: center;
    margin-bottom: 10px; position: relative;
}
.deals-carousel-page .carousel-card-img img { max-height: 150px; max-width: 100%; object-fit: contain; }
.deals-carousel-page .carousel-card-badge {
    position: absolute; top: 0; right: 0;
    background: linear-gradient(135deg, #f44336, #e91e63);
    color: #fff; font-size: 12px; font-weight: 800;
    padding: 4px 10px; border-radius: 20px; line-height: 1.3;
    text-align: center; box-shadow: 0 2px 6px rgba(230,60,60,0.4);
}
.deals-carousel-page .carousel-card-title {
    font-size: 14px; font-weight: 800; color: #111; margin-bottom: 6px;
}
.deals-carousel-page .carousel-card-price-row { display: flex; align-items: baseline; gap: 8px; margin-bottom: 2px; }
.deals-carousel-page .carousel-card-price { font-size: 17px; font-weight: 800; color: #2e7d32; margin-bottom: 4px; }
.deals-carousel-page .carousel-card-price-reg { font-size: 13px; color: #999; text-decoration: line-through; }
.deals-carousel-page .carousel-card-save { font-size: 12px; color: #555; margin-bottom: 4px; }
.deals-carousel-page .carousel-card-discount { font-size: 11.5px; color: #e63c3c; font-weight: 600; }
.deals-carousel-page .carousel-card-spacer { flex: 1 0 14px; }
.deals-carousel-page .carousel-card-btn {
    display: block; text-align: center;
    background: linear-gradient(90deg, #f44336, #ff7043);
    color: #fff; text-decoration: none; font-weight: 800; padding: 11px 12px;
    border-radius: 25px; font-size: 13.5px; letter-spacing: 0.2px;
    box-shadow: 0 3px 8px rgba(244,67,54,0.35); transition: transform 0.15s;
}
.deals-carousel-page .carousel-card-btn:hover { transform: translateY(-1px); }
</style>
<script>
(function() {
    document.querySelectorAll('.deals-carousel-page .carousel-arrow').forEach(function(btn) {
        btn.addEventListener('click', function() {
            var track = document.getElementById(this.getAttribute('data-track'));
            if (!track) return;
            var dir = this.classList.contains('left') ? -1 : 1;
            track.scrollBy({ left: dir * track.clientWidth * 0.85, behavior: 'smooth' });
        });
    });
    document.querySelectorAll('.deals-carousel-page .cat-nav-link').forEach(function(link) {
        link.addEventListener('click', function(e) {
            e.preventDefault();
            var target = document.getElementById(this.getAttribute('data-target'));
            if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        });
    });
})();
</script>
"""


def build_page_html(products: list, page_title: str = "Best Amazon Deals") -> str:
    """Build the full WordPress page HTML: category nav + one carousel per category."""
    total = len(products)

    grouped: dict[str, list] = {}
    for p in products:
        cat = p.get("category", "Other")
        grouped.setdefault(cat, []).append(p)

    ordered_categories = [c for c in CATEGORIES if c in grouped]
    remaining = sorted(c for c in grouped if c not in CATEGORIES)
    ordered_categories += remaining

    html = '<div id="top" class="aos-finds deals-carousel-page">\n'
    html += f'  <h3 style="text-align: center;">{page_title}</h3>\n\n'
    html += (
        f'  <p style="text-align: center; font-style: italic;">'
        f'{total} deals found across {len(ordered_categories)} categories</p>\n\n'
    )

    # ── Category nav ──
    html += '  <div class="category-nav">\n'
    for cat in ordered_categories:
        slug = slugify(cat)
        html += f'    <a href="#cat-{slug}" class="cat-nav-link" data-target="cat-{slug}">{cat} ({len(grouped[cat])})</a>\n'
    html += '  </div>\n\n'

    # ── One carousel per category ──
    item_counter = 0
    for cat in ordered_categories:
        slug = slugify(cat)
        items = grouped[cat]

        html += f'  <div class="deal-category-section" id="cat-{slug}">\n'
        html += f'    <h2>{cat}</h2>\n'
        html += '    <div class="carousel-wrapper">\n'
        html += (
            f'      <button type="button" class="carousel-arrow left" data-track="track-{slug}" aria-label="Scroll left">'
            f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" '
            f'stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"></polyline></svg>'
            f'</button>\n'
        )
        html += f'      <div class="carousel-track" id="track-{slug}">\n'

        for product in items:
            item_counter += 1
            html += '        ' + render_carousel_card(item_counter, product) + '\n'

        html += '      </div>\n'
        html += (
            f'      <button type="button" class="carousel-arrow right" data-track="track-{slug}" aria-label="Scroll right">'
            f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" '
            f'stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"></polyline></svg>'
            f'</button>\n'
        )
        html += '    </div>\n'
        html += '  </div>\n\n'

    html += CAROUSEL_STYLE_AND_SCRIPT
    html += '</div>'
    return html


# ── IMAGE UPLOAD ──────────────────────────────────────────────────────────────

def upload_and_get_url(publisher: WordPressPublisher, img_path: Path, title: str) -> Optional[str]:
    """Upload image to WordPress and return its public source URL."""
    if not img_path or not img_path.exists():
        return None
    media_id = publisher.upload_image(img_path, title=title)
    if not media_id:
        return None
    try:
        resp = requests.get(
            f"{publisher.api_base}/media/{media_id}",
            auth=publisher.auth,
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json().get("source_url")
    except Exception as e:
        print(f"  ⚠️ Could not resolve URL for media {media_id}: {e}")
    return None


# ── SCRAPING ──────────────────────────────────────────────────────────────────

def scrape_single_site(site: dict) -> list[dict]:
    """Run a single scraper and return its products with source metadata."""
    site_id = site["id"]
    site_name = site.get("name", site_id)
    scraper_module_name = site.get("scraper", f"{site_id}_scraper")

    print(f"\n  → Scraping: {site_name}")

    try:
        scraper = load_scraper(scraper_module_name)
        scraper.main()

        json_file = scraper.OUTPUT_FILE
        if not json_file.exists():
            print(f"    ❌ No output at {json_file}")
            return []

        with open(json_file, "r", encoding="utf-8") as f:
            products = json.load(f)

        # Tag each product with its source
        for p in products:
            p["_source_id"] = site_id
            p["_source_name"] = site_name

        print(f"    ✅ Got {len(products)} products")
        return products

    except Exception as e:
        print(f"    ❌ Scraper error: {e}")
        return []


def scrape_all_sites(config: dict, only_site_id: Optional[str] = None) -> list[dict]:
    """Run all enabled scrapers sequentially and aggregate products."""
    sites = config.get("sites", [])
    all_products = []

    print("=" * 60)
    print("PHASE 1: SCRAPING ALL SITES")
    print("=" * 60)

    for site in sites:
        if not site.get("enabled", True):
            print(f"\n  ⏭ Skipping disabled: {site.get('name', site['id'])}")
            continue

        if only_site_id and site["id"] != only_site_id:
            continue

        products = scrape_single_site(site)

        # AI categorization — pass just this site's product names to the AI,
        # per site (batched), then tag each product with its category.
        if products:
            categorize_products(products, site_name=site.get("name", site["id"]))

        # Update site last_run
        now = datetime.now().isoformat()
        site["last_run"] = now
        site["last_run_status"] = "success" if products else "empty"

        all_products.extend(products)

    save_config(config)

    # Save aggregated JSON
    AGGREGATE_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(AGGREGATE_JSON, "w", encoding="utf-8") as f:
        json.dump(all_products, f, indent=2, ensure_ascii=False)
    print(f"\n📋 Aggregated {len(all_products)} total products from {len(sites)} sites")
    print(f"   Saved to {AGGREGATE_JSON}")

    return all_products


# ── FULL PIPELINE ─────────────────────────────────────────────────────────────

def run_pipeline(
    publish: bool = False,
    skip_scrape: bool = False,
    site_id: Optional[str] = None,
    page_id: Optional[int] = None,
) -> bool:
    """Full aggregate pipeline: scrape all → upload all images → publish to one page.

    Args:
        publish: If True, publish live; otherwise draft.
        skip_scrape: If True, use existing aggregated data.
        site_id: If set, only scrape that one site (still publishes to aggregate page).
        page_id: Override the aggregate page ID from config.
    """
    config = load_config()

    # ── Phase 1: Scrape all sites ──
    if not skip_scrape:
        all_products = scrape_all_sites(config, only_site_id=site_id)
    else:
        print("Skipping scrape — using existing aggregated data")
        if not AGGREGATE_JSON.exists():
            print(f"❌ No aggregated data found at {AGGREGATE_JSON}")
            return False
        with open(AGGREGATE_JSON, "r", encoding="utf-8") as f:
            all_products = json.load(f)

    if not all_products:
        print("❌ No products found from any scraper")
        return False

    print(f"\n📋 Total aggregated products: {len(all_products)}")

    # ── Phase 2: WordPress credentials ──
    wp_prefix = config.get("wp_env_prefix", "RS")
    wp_url = os.environ.get(f"WP_URL_{wp_prefix}")
    wp_username = os.environ.get(f"WP_USERNAME_{wp_prefix}")
    wp_password = os.environ.get(f"WP_PASSWORD_{wp_prefix}")

    if not all([wp_url, wp_username, wp_password]):
        print(f"❌ Missing WordPress credentials (prefix={wp_prefix})!")
        return False

    publisher = WordPressPublisher(wp_url, wp_username, wp_password)
    if not publisher.test_connection():
        return False

    # ── Phase 3: Upload all images ──
    print("\n" + "=" * 60)
    print("PHASE 2: UPLOADING ALL IMAGES")
    print("=" * 60)

    for i, product in enumerate(all_products):
        name = product.get("name", f"product_{i + 1}")
        img_path_str = product.get("image_path", "")

        if not img_path_str:
            print(f"  [{i + 1}] No image path for '{name}' — skipping")
            continue

        img_path = Path(img_path_str)
        if img_path.exists():
            url = upload_and_get_url(publisher, img_path, name)
            if url:
                product["_img_url"] = url
                print(f"  [{i + 1}] Uploaded '{name}'")
        else:
            print(f"  [{i + 1}] Image not found: {img_path} — skipping")

    # ── Phase 4: Filter products without images ──
    before = len(all_products)
    all_products = [p for p in all_products if p.get("_img_url")]
    skipped = before - len(all_products)
    if skipped:
        print(f"\n  ⚠️  Skipped {skipped} product(s) with no image — {len(all_products)} remaining")

    if not all_products:
        print("❌ No products with images to publish.")
        return False

    # ── Phase 5: Build HTML ──
    print("\n" + "=" * 60)
    print("PHASE 3: BUILDING HTML")
    print("=" * 60)

    page_title = config.get("page_title", "Best Amazon Deals")
    html = build_page_html(all_products, page_title=page_title)
    print(f"✅ HTML generated ({len(html)} chars)")

    # ── Phase 6: Publish ──
    final_page_id = page_id or config.get("aggregate_page_id", DEFAULT_PAGE_ID)
    status = "publish" if publish else "draft"

    print("\n" + "=" * 60)
    print(f"PHASE 4: PUBLISHING TO PAGE {final_page_id} AS {status.upper()}")
    print("=" * 60)

    success = publisher.update_page(
        page_id=final_page_id,
        content=html,
        status=status,
        try_page_first=True,
        update_date=publish,
    )

    if success:
        config["last_run"] = datetime.now().isoformat()
        config["last_run_status"] = "success"
        save_config(config)

        print("\n" + "=" * 60)
        print("✅ AGGREGATE PIPELINE COMPLETE!")
        print("=" * 60)
        print(f"   Products : {len(all_products)}")
        print(f"   Page ID  : {final_page_id}")
        print(f"   Status   : {status.upper()}")
        print("=" * 60)
        return True
    else:
        config["last_run"] = datetime.now().isoformat()
        config["last_run_status"] = "failed"
        save_config(config)
        print("\n❌ Publishing failed.")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Aggregate pipeline: scrape all sites → publish to one WordPress page"
    )
    parser.add_argument("--site-id", default=None, help="Only scrape this one site (still publishes to aggregate page)")
    parser.add_argument("--page-id", type=int, default=None, help="Override aggregate page ID")
    parser.add_argument("--publish", action="store_true", help="Publish live (default: draft)")
    parser.add_argument("--skip-scrape", action="store_true", help="Use existing aggregated data")
    args = parser.parse_args()

    success = run_pipeline(
        publish=args.publish,
        skip_scrape=args.skip_scrape,
        site_id=args.site_id,
        page_id=args.page_id,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
