#!/usr/bin/env python3
"""
Flask dashboard for Amazon affiliate scraper pipeline.

Features:
  - PIN-based authentication (FLASK_PIN in .env)
  - View all configured sites, their schedule, last run, status
  - Edit schedule (days of week, time, timezone)
  - Enable / pause sites
  - Set page ID per site
  - Run a site manually (scrape + publish)
  - APScheduler runs every second checking if any site is due

Usage:
  python app.py
  # then open http://localhost:5000
"""

import json
import os
import sys
import threading
from pathlib import Path
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, request, session, redirect, url_for, render_template, jsonify, flash

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except Exception:
    pass

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# ── CONFIG ────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent
SITES_FILE = BASE_DIR / "sites_config.json"
RUNS_LOG_FILE = BASE_DIR / "runs_log.json"

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "amazon-affiliate-secret-key-change-me")
FLASK_PIN = os.environ.get("FLASK_PIN", "1234")

# ── DATA HELPERS ──────────────────────────────────────────────────────────────

DAYS_MAP = {
    "mon": "Monday", "tue": "Tuesday", "wed": "Wednesday",
    "thu": "Thursday", "fri": "Friday", "sat": "Saturday", "sun": "Sunday",
}
ALL_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def load_sites() -> dict:
    if not SITES_FILE.exists():
        return {"sites": []}
    with open(SITES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_sites(data: dict) -> None:
    with open(SITES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_runs_log() -> list:
    if not RUNS_LOG_FILE.exists():
        return []
    with open(RUNS_LOG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_runs_log(runs: list) -> None:
    with open(RUNS_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(runs, f, indent=2, ensure_ascii=False)


def add_run_log(site_id: str, status: str, message: str = "") -> None:
    runs = load_runs_log()
    runs.append({
        "site_id": site_id,
        "timestamp": datetime.now().isoformat(),
        "status": status,
        "message": message,
    })
    # Keep last 200 runs
    runs = runs[-200:]
    save_runs_log(runs)


def update_global_field(field: str, value) -> None:
    data = load_sites()
    data[field] = value
    save_sites(data)


def update_site_field(site_id: str, field: str, value) -> None:
    data = load_sites()
    for site in data.get("sites", []):
        if site["id"] == site_id:
            site[field] = value
            break
    save_sites(data)


# ── AUTH ──────────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function


# ── SCHEDULER ─────────────────────────────────────────────────────────────────

scheduler = BackgroundScheduler()
scheduler.start()

# Track which sites have active jobs
active_jobs: dict = {}


def run_site_pipeline(site_id: str = None) -> None:
    """Run the aggregate pipeline — scrapes all enabled sites, publishes to one page.

    If site_id is provided, only that site is scraped (still publishes to aggregate page).
    """
    print(f"\n{'='*60}")
    if site_id:
        print(f"[SCHEDULER] Running pipeline for single site: {site_id}")
    else:
        print(f"[SCHEDULER] Running aggregate pipeline for ALL sites")
    print(f"{'='*60}")

    add_run_log("aggregate", "started", f"Pipeline started{' for '+site_id if site_id else ' for all sites'}")

    try:
        import main as pipeline
        success = pipeline.run_pipeline(
            publish=True,
            skip_scrape=False,
            site_id=site_id,
        )

        now = datetime.now().isoformat()
        if success:
            update_global_field("last_run", now)
            update_global_field("last_run_status", "success")
            add_run_log("aggregate", "success", "Pipeline completed successfully")
        else:
            update_global_field("last_run", now)
            update_global_field("last_run_status", "failed")
            add_run_log("aggregate", "failed", "Pipeline returned failure")
    except Exception as e:
        now = datetime.now().isoformat()
        update_global_field("last_run", now)
        update_global_field("last_run_status", "error")
        add_run_log("aggregate", "error", str(e))
        print(f"[SCHEDULER] Error: {e}")


def rebuild_schedule() -> None:
    """Rebuild the single aggregate scheduler job from config."""
    job_id = "aggregate_pipeline"

    # Remove existing job
    if job_id in active_jobs:
        try:
            scheduler.remove_job(job_id)
        except Exception:
            pass
        del active_jobs[job_id]

    data = load_sites()

    if not data.get("enabled", True):
        print("[SCHEDULER] Pipeline disabled — no job scheduled")
        return

    sched = data.get("schedule", {})
    days = sched.get("days_of_week", [])
    hour = sched.get("hour", 5)
    minute = sched.get("minute", 0)
    tz = sched.get("timezone", "America/Chicago")

    if not days:
        print("[SCHEDULER] No days configured — no job scheduled")
        return

    day_str = ",".join(days)
    trigger = CronTrigger(day_of_week=day_str, hour=hour, minute=minute, timezone=tz)

    scheduler.add_job(
        func=run_site_pipeline,
        trigger=trigger,
        id=job_id,
        replace_existing=True,
    )
    active_jobs[job_id] = True
    print(f"[SCHEDULER] Scheduled aggregate pipeline: days={day_str} hour={hour} min={minute} tz={tz}")


# ── ROUTES ────────────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        pin = request.form.get("pin", "")
        if pin == FLASK_PIN:
            session["authenticated"] = True
            return redirect(url_for("dashboard"))
        flash("Incorrect PIN", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("authenticated", None)
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    data = load_sites()
    runs = load_runs_log()[-20:]
    return render_template("dashboard.html", config=data, sites=data.get("sites", []), runs=runs, days_map=DAYS_MAP)


@app.route("/run-all", methods=["POST"])
@login_required
def run_all():
    """Run the aggregate pipeline (all enabled scrapers → one page)."""
    thread = threading.Thread(target=run_site_pipeline, args=(None,))
    thread.daemon = True
    thread.start()
    flash("Aggregate pipeline started — check runs log for status", "info")
    return redirect(url_for("dashboard"))


@app.route("/site/<site_id>/run", methods=["POST"])
@login_required
def run_single(site_id):
    """Run pipeline for a single site only (still publishes to aggregate page)."""
    thread = threading.Thread(target=run_site_pipeline, args=(site_id,))
    thread.daemon = True
    thread.start()
    flash(f"Pipeline started for '{site_id}' — check runs log", "info")
    return redirect(url_for("dashboard"))


@app.route("/site/<site_id>/toggle", methods=["POST"])
@login_required
def toggle_site(site_id):
    data = load_sites()
    for site in data.get("sites", []):
        if site["id"] == site_id:
            site["enabled"] = not site.get("enabled", True)
            save_sites(data)
            status = "enabled" if site["enabled"] else "paused"
            flash(f"Scraper '{site['name']}' {status}", "success")
            break
    return redirect(url_for("dashboard"))


@app.route("/schedule", methods=["POST"])
@login_required
def update_schedule():
    """Update the global aggregate schedule."""
    data = load_sites()
    days = request.form.getlist("days_of_week")
    hour = int(request.form.get("hour_24", 5))
    minute = int(request.form.get("minute", 0))
    tz = request.form.get("timezone", "America/Chicago")

    data["schedule"] = {
        "days_of_week": days,
        "hour": hour,
        "minute": minute,
        "timezone": tz,
    }
    save_sites(data)
    rebuild_schedule()
    flash("Schedule updated", "success")
    return redirect(url_for("dashboard"))


@app.route("/toggle-pipeline", methods=["POST"])
@login_required
def toggle_pipeline():
    """Enable/pause the entire aggregate pipeline."""
    data = load_sites()
    data["enabled"] = not data.get("enabled", True)
    save_sites(data)
    rebuild_schedule()
    status = "enabled" if data["enabled"] else "paused"
    flash(f"Aggregate pipeline {status}", "success")
    return redirect(url_for("dashboard"))


@app.route("/page-id", methods=["POST"])
@login_required
def update_page_id():
    """Update the aggregate page ID."""
    page_id = request.form.get("page_id", type=int)
    if page_id:
        update_global_field("aggregate_page_id", page_id)
        flash(f"Page ID updated to {page_id}", "success")
    return redirect(url_for("dashboard"))


@app.route("/api/sites")
@login_required
def api_sites():
    data = load_sites()
    return jsonify(data)


@app.route("/api/runs")
@login_required
def api_runs():
    runs = load_runs_log()[-50:]
    return jsonify(runs)


# ── MAIN ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Initialize scheduler
    rebuild_schedule()

    print("\n" + "=" * 60)
    print("Amazon Affiliate Dashboard")
    print("=" * 60)
    print(f"  Config : {SITES_FILE}")
    print(f"  PIN    : {FLASK_PIN}")
    print(f"  URL    : http://localhost:5000")
    print("=" * 60 + "\n")

    app.run(host="0.0.0.0", port=5008, debug=False, use_reloader=False)
