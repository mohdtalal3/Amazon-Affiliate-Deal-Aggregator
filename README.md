# Amazon Affiliate Deal Aggregator

Automated pipeline that scrapes Amazon affiliate deal sites, uses AI (xAI Grok) to categorize products, and publishes them to a WordPress page as interactive category carousels.

## Pipeline Overview

```
Scrape all sites → AI categorize per site → Aggregate → Upload images → Build carousel HTML → Publish to WordPress
```

1. **Scrape** — Each enabled site's scraper runs sequentially, fetching products (name, price, discount, Amazon URL, image).
2. **AI Categorize** — Product names from each site are sent to Grok in a single batched call. The model groups them into a fixed category set.
3. **Aggregate** — All categorized products from all sites are merged into one list.
4. **Upload Images** — Product images are uploaded to WordPress media library (cached to avoid re-uploads).
5. **Build HTML** — Products are grouped by category, rendered as one carousel per category with arrow navigation and a category nav bar.
6. **Publish** — The final HTML is pushed to a single WordPress page.

## Project Structure

```
amazon_affilate/
├── app.py                    # Flask dashboard (scheduler, config UI, manual run trigger)
├── main.py                   # Aggregate pipeline orchestrator + HTML carousel builder
├── categorize.py             # AI categorization via xAI Grok (streaming, grouped schema)
├── daveandsissy_scraper.py   # Scraper for daveandsissydailydeals.com
├── wordpress_publisher.py    # WordPress REST API client (auth, upload, publish)
├── sites_config.json         # Site list + schedule + WordPress page ID
├── .env                      # API keys (XAI_API_KEY, WP_URL_RS, WP_USERNAME_RS, WP_PASSWORD_RS)
├── .image_cache.json         # Maps local image paths → WordPress media IDs (auto-generated)
├── runs_log.json             # Pipeline run history (auto-generated)
└── scraping data/            # Scraped JSON + downloaded images per site
```

## Setup

### Prerequisites

- Python 3.11+
- WordPress site with REST API enabled + Application Password
- xAI API key (for Grok categorization)

### Install Dependencies

```bash
pip install flask requests beautifulsoup4 openai pydantic python-dotenv httpx APScheduler
```

### Environment Variables (`.env`)

```env
XAI_API_KEY=xai-xxxxxxxxxxxxxxxxxxxx
WP_URL_RS=https://your-wordpress-site.com
WP_USERNAME_RS=your_wp_username
WP_PASSWORD_RS=your_wp_application_password
```

The `RS` suffix is the `wp_env_prefix` set in `sites_config.json`. This lets you manage multiple WordPress targets by changing the prefix.

### Configuration (`sites_config.json`)

```json
{
  "aggregate_page_id": 365850,
  "wp_env_prefix": "RS",
  "page_title": "Best Amazon Deals",
  "schedule": {
    "days_of_week": ["sat"],
    "hour": 5,
    "minute": 0,
    "timezone": "America/Chicago"
  },
  "sites": [
    {
      "id": "daveandsissy",
      "name": "Dave & Sissy Daily Deals",
      "scraper": "daveandsissy_scraper",
      "source_url": "https://daveandsissydailydeals.com/amazon-deals",
      "enabled": true
    }
  ]
}
```

- **`aggregate_page_id`** — WordPress page ID where all deals are published.
- **`schedule`** — Cron-like schedule for automatic runs (via APScheduler in `app.py`).
- **`sites`** — List of scraper sites. Each needs a corresponding `<scraper_name>.py` with a `main()` function and `OUTPUT_FILE` path.

## Running

### Dashboard (recommended)

```bash
python3 app.py
```

Opens a web dashboard at `http://localhost:5000` (PIN: `1234`) where you can:
- View/edit site config and schedule
- Manually trigger the pipeline
- See run history and last-run status

### Command Line

```bash
# Scrape all sites + publish as draft
python3 main.py

# Scrape all sites + publish live
python3 main.py --publish

# Use existing scraped data (skip scraping)
python3 main.py --skip-scrape --publish

# Scrape only one site (still publishes to aggregate page)
python3 main.py --site-id daveandsissy --publish

# Override WordPress page ID
python3 main.py --page-id 304402 --publish
```

## AI Categorization (`categorize.py`)

- **Model:** Grok-4.5 via xAI API (`https://api.x.a/v1`)
- **Streaming:** Responses are streamed for real-time visibility of reasoning output.
- **Schema:** Grouped format — one entry per category with all matching product indexes listed together (not one entry per product), keeping token usage low for large batches.
- **Fixed category set** ensures consistent grouping across different scraper sites.

### Current Categories

```
Clothing & Apparel, Health & Wellness, Beauty & Personal Care, Household,
Home & Kitchen, Food & Snacks, Baby & Kids, Pet, Toys & Games,
Sports & Outdoors, Office & School Supplies, Tools & Automotive,
Seasonal & Cards, Electronics & Photo, Other
```

To change categories, edit the `CATEGORIES` list in `categorize.py`. The carousel ordering in `main.py` reads from this list automatically.

### Fallback Behavior

If `XAI_API_KEY` is missing or the API call fails, all products default to `"Other"` — the pipeline never breaks.

## WordPress Output

The published page includes:

- **Category nav bar** at top — pill-style links with product counts. Clicking smooth-scrolls to that category's carousel.
- **One carousel per category** — horizontal scroll with left/right arrow buttons (SVG chevrons).
- **Product cards** — image, title, price, discount lines (deduplicated against title), and a red "See Deal on Amazon" button.

All CSS and JS are inline in the generated HTML — no external dependencies or theme-specific assumptions.

## Adding a New Scraper

1. Create `<site_id>_scraper.py` with:
   - `def main()` — scrapes the site, downloads images, saves JSON.
   - `OUTPUT_FILE` — `Path` pointing to the output JSON location.
2. Add the site to `sites_config.json` under `sites`.
3. Each product in the JSON should have: `name`, `price`, `discount`, `description`, `amazon_url`, `image_url`, `image_path`.

## Image Caching

`.image_cache.json` maps local image file paths to WordPress media IDs. On subsequent runs, cached images are reused (no re-upload). Delete this file to force fresh uploads.

## Notes

- The scraper currently limits to 10 products for testing. Remove the `products = products[:10]` line in the scraper to fetch all.
- WordPress uploads may occasionally fail with transient network errors (`RemoteDisconnected`). The pipeline skips products without images and continues.
- Cloudflare-protected WordPress sites may block the REST API — whitelist your server IP or configure a bypass rule.
