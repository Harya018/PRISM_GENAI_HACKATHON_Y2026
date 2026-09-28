#!/usr/bin/env python3
"""Captures real screenshots of the dashboard's tabs for the README, using a headless Chromium
(Playwright) -- not part of the core pipeline, just a documentation convenience.

Setup (Playwright isn't in requirements.txt -- it's dev/doc tooling, not a runtime dependency):
    pip install playwright
    playwright install chromium

Requires web_client.py already running on :8450 (python extension/web_client.py).

Usage: python scripts/capture_screenshots.py
Writes: docs/screenshots/{live_empty,results,explorer,architecture}.png
"""
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
OUT_DIR.mkdir(parents=True, exist_ok=True)
URL = "http://localhost:8450/"

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.goto(URL, wait_until="networkidle")
    time.sleep(1)

    # Live tab (default) -- empty state, before joining
    page.screenshot(path=str(OUT_DIR / "live_empty.png"))

    # Results tab
    page.click('button[data-view="results"]')
    page.wait_for_timeout(1200)
    page.screenshot(path=str(OUT_DIR / "results.png"), full_page=True)

    # Benchmark Explorer tab
    page.click('button[data-view="explorer"]')
    page.wait_for_timeout(1200)
    page.screenshot(path=str(OUT_DIR / "explorer.png"), full_page=True)

    # Architecture tab
    page.click('button[data-view="architecture"]')
    page.wait_for_timeout(500)
    page.screenshot(path=str(OUT_DIR / "architecture.png"), full_page=True)

    browser.close()

print("Screenshots written to", OUT_DIR)
