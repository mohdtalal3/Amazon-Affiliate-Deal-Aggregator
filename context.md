# Amazon Affiliate Deal Aggregator — Project Context

## Overview

This project is an **automated Amazon affiliate deal aggregation pipeline**. It scrapes Amazon deals from multiple deal-aggregator websites, filters out coupon-only deals, downloads product images, categorizes products via AI, and publishes everything as a single carousel-style page on WordPress. A Flask dashboard provides a UI to manage scrapers, schedules, and manual runs.

## Architecture

```
sites_config.json          ← Central config: scraper registry, schedule, WP page ID
        │
        ▼
    app.py (Flask dashboard, port 5008)
        │  APScheduler triggers on cron schedule
        ▼
    main.py (aggregate pipeline)
        │
        ├─ Phase 1: Scrape all enabled sites (sequential)
        │   ├─ daveandsissy_scraper.py
        │   ├─ jungledeals_scraper.py
        │   ├─ taylorsdailydeals_scraper.py
        │   └─ slickdeals_scraper.py
        │
        ├─ Phase 1.5: AI categorization (categorize.py → xAI Grok)
        │
        ├─ Phase 2: Upload images to WordPress (wordpress_publisher.py)
        │
        ├─ Phase 3: Build carousel HTML (main.py → build_page_html)
        │
        └─ Phase 4: Publish to WordPress page (draft or live)
```

## File-by-File Reference

### Core Pipeline

- **`main.py`** — Aggregate pipeline orchestrator. Dynamically loads scrapers via `importlib.import_module()`, runs each scraper's `main()`, reads their `OUTPUT_FILE` JSON, aggregates all products, runs AI categorization per-site, uploads images to WordPress, builds carousel HTML grouped by category, and publishes to a single WordPress page.
  - Key functions: `run_pipeline()`, `scrape_all_sites()`, `scrape_single_site()`, `build_page_html()`, `render_carousel_card()`
  - CLI args: `--publish`, `--skip-scrape`, `--site-id <id>`, `--page-id <int>`
  - Product dict schema (post-scrape): `{name, description, amazon_url, image_url, discount, price, image_path, category, _source_id, _source_name, _img_url}`

- **`app.py`** — Flask web dashboard on port 5008. PIN-based auth (`FLASK_PIN` env var). Shows all configured scrapers, their enabled/disabled status, last run time/status, and a runs log. Allows manual run of all sites or a single site, editing the schedule (days of week, time, timezone), toggling scrapers, and updating the WordPress page ID. Uses APScheduler `BackgroundScheduler` with a single aggregate cron job.

- **`categorize.py`** — AI product categorization using xAI Grok models. Sends product names to Grok in a single batched API call per site, asks it to assign each product to one of 15 fixed categories (defined in `CATEGORIES` list). Uses Pydantic schemas for structured JSON output. Falls back to "Other" on any failure. Requires `XAI_API_KEY` env var.

- **`wordpress_publisher.py`** — WordPress REST API client. Handles image uploads (with per-domain caching to avoid re-uploads), page/post updates, post creation, and category management. Image cache stored in `.image_cache.json` (retailshout) or `.image_cache_{domain}.json` (other domains). Supports dry-run mode.

### Scrapers

Each scraper follows a common pattern:
- Has a `main()` function that fetches deals, filters coupons, builds product dicts, downloads images, and saves JSON to `OUTPUT_FILE`.
- Has module-level constants: `DATA_DIR`, `OUTPUT_FILE`, `HEADERS`.
- Product dict output: `{name, description, amazon_url, image_url, discount, price, image_path?}`.
- Skips coupon/promo code deals.
- Downloads images to `scraping data/{site}_data/` with sanitized filenames.

- **`daveandsissy_scraper.py`** — Scrapes `daveandsissydailydeals.com/amazon-deals` via HTML parsing (BeautifulSoup). Parses funnel cards, extracts Amazon URL from links, detects coupon products by "Code" keyword in discount text. Uses `requests` library.

- **`jungledeals_scraper.py`** — Scrapes `jungle.deals` via internal JSON API (`/api/get-page/best-selling-deals`). Filters products with `promoCode` field. Extracts ASIN directly from API response. Uses `requests` library.

- **`taylorsdailydeals_scraper.py`** — Scrapes `taylorsdailydeals.com` via paginated JSON API (`/api/deals` with limit/offset). Filters products with `coupon_code` field. Cleans titles by stripping leading discount percentages. Uses `requests` library.

- **`slickdeals_scraper.py`** — Scrapes `slickdeals.net` top retail deals. Most complex scraper:
  - Uses `curl_cffi` with `impersonate="chrome"` to bypass Cloudflare protection.
  - Proxy support via `STATIC_PROXY` env var.
  - Dynamically discovers API endpoint by fetching the page HTML and regex-extracting feed ID and block ID from JS hydration URLs.
  - Fetches deals from content-block API, then for each deal fetches the thread page HTML to extract ASIN from `data-aps-asin` attribute.
  - Uses `ThreadPoolExecutor(max_workers=5)` for concurrent ASIN fetching.
  - Retry mechanism with 3 retries and progressive backoff on all HTTP requests.
  - Skips non-Amazon and expired deals.

### Configuration

- **`sites_config.json`** — Central configuration file. Structure:
  ```json
  {
    "aggregate_page_id": 365850,
    "wp_env_prefix": "RS",
    "page_title": "Best Amazon Deals Today",
    "enabled": true,
    "schedule": {"days_of_week": ["sat"], "hour": 5, "minute": 0, "timezone": "America/Chicago"},
    "sites": [
      {"id": "daveandsissy", "name": "...", "scraper": "daveandsissy_scraper", "source_url": "...", "enabled": false, ...},
      ...
    ]
  }
  ```
  - `wp_env_prefix` determines which WP env vars to use (e.g., `WP_URL_RS`, `WP_USERNAME_RS`, `WP_PASSWORD_RS`).
  - Each site has `id`, `name`, `scraper` (module name), `source_url`, `enabled`, `last_run`, `last_run_status`.

### Templates

- **`templates/dashboard.html`** — Admin dashboard UI showing scraper status, schedule editor, run buttons, and runs log.
- **`templates/login.html`** — PIN login form.

### Other Files

- **`.env`** — Environment variables (gitignored). Contains: `FLASK_PIN`, `FLASK_SECRET_KEY`, `WP_URL_RS`, `WP_USERNAME_RS`, `WP_PASSWORD_RS`, `XAI_API_KEY`, `STATIC_PROXY`.
- **`runs_log.json`** — Log of pipeline runs (gitignored, last 200 entries).
- **`.image_cache.json`** — Cache of uploaded WordPress media IDs (gitignored).
- **`test.py`** — Saved HTML from slickdeals page for debugging/inspection.
- **`scraping data/`** — Output directory for all scraped data (gitignored). Subdirectories per site: `daveandsissy_data/`, `jungledeals_data/`, `taylorsdailydeals_data/`, `slickdeals_data/`. Also contains `aggregated_products.json`.

## Key Patterns

- **Dynamic scraper loading**: `main.py` uses `importlib.import_module(scraper_module_name)` to load scrapers by module name from `sites_config.json`. Each scraper must expose `main()` and `OUTPUT_FILE`.
- **Coupon filtering**: All scrapers skip coupon/promo code deals. Detection varies by site: `promoCode` field (jungledeals), `coupon_code` field (taylorsdailydeals), "Code" keyword in discount text (daveandsissy).
- **Image handling**: Scrapers download images locally → pipeline uploads to WordPress → products without uploaded images are filtered out before publishing.
- **Category carousel UI**: Products grouped by AI-assigned category, rendered as horizontally-scrolling carousels with left/right arrow buttons and a category navigation bar.
- **Title formatting**: Product titles are capitalized (first letter) and rendered bold in the carousel cards.

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `FLASK_PIN` | PIN for dashboard login |
| `FLASK_SECRET_KEY` | Flask session secret |
| `WP_URL_RS` | WordPress site URL (prefix `RS`) |
| `WP_USERNAME_RS` | WordPress username (prefix `RS`) |
| `WP_PASSWORD_RS` | WordPress application password (prefix `RS`) |
| `XAI_API_KEY` | xAI Grok API key for categorization |
| `STATIC_PROXY` | HTTP proxy for slickdeals scraper |

## Running

```bash
# Run full pipeline (scrape all + publish as draft)
python main.py

# Run full pipeline and publish live
python main.py --publish

# Scrape only one site (still publishes to aggregate page)
python main.py --site-id slickdeals

# Use existing scraped data (skip scraping)
python main.py --skip-scrape --publish

# Start Flask dashboard
python app.py
# → http://localhost:5008

# Run a single scraper standalone
python slickdeals_scraper.py
python taylorsdailydeals_scraper.py
```

## Dependencies

- `requests` — HTTP client for most scrapers and WordPress API
- `curl_cffi` — HTTP client with browser impersonation (slickdeals only, for Cloudflare bypass)
- `beautifulsoup4` — HTML parsing (daveandsissy, slickdeals)
- `flask` — Web dashboard
- `apscheduler` — Cron-based job scheduling
- `pydantic` — Structured JSON schema for AI categorization
- `openai` — xAI Grok API client (uses OpenAI-compatible SDK)
- `python-dotenv` — `.env` file loading
