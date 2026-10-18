#!/usr/bin/env python3
"""
Email Scraper — 80 Days to Stay
================================
Scrapes publicly listed email addresses from company websites using Playwright.

Takes a CSV of companies as input, visits each company's website and common
contact pages, and writes found emails back to a new CSV in real time.

Usage:
    python -m scrapers.email_scraper.scraper --input data/companies.csv
    python -m scrapers.email_scraper.scraper --input data/companies.csv --output data/emails.csv
    python -m scrapers.email_scraper.scraper --input data/companies.csv --limit 50 --delay 1.5

Input CSV must have columns: company_name, website
  (Compatible with 80_Days_CSV/mapped_student_employment_targets_v3.csv)

Output CSV columns: company_name, website, emails_found, pages_scraped, status, scraped_at
"""

import argparse
import csv
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

# Allow running as `python scraper.py` from this directory
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scrapers.common.logger import get_logger
from scrapers.common.rate_limiter import RateLimiter

from .config import (
    BROWSER_TIMEOUT_MS,
    CONTACT_PATHS,
    DELAY_BETWEEN_COMPANIES,
    DOMAIN_BLACKLIST,
    EMAIL_BLACKLIST_SUBSTRINGS,
    MAX_PAGES_PER_COMPANY,
    MIN_TLD_LENGTH,
    OUTPUT_COLUMNS,
)

logger = get_logger("email_scraper")

# Regex: matches most RFC-5321-valid addresses visible in page text or href
_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{" + str(MIN_TLD_LENGTH) + r",}"
)


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def _normalize_url(raw: str) -> str | None:
    """Add https:// if missing; return None for clearly invalid inputs."""
    raw = raw.strip().rstrip("/")
    if not raw or raw in {"nan", "N/A", "n/a", "-"}:
        return None
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    parsed = urlparse(raw)
    if not parsed.netloc:
        return None
    return raw


def _build_contact_urls(base_url: str) -> list[str]:
    """Return the homepage + common contact page URLs for a domain."""
    urls = [base_url]
    for path in CONTACT_PATHS:
        urls.append(urljoin(base_url, path))
    return urls[:MAX_PAGES_PER_COMPANY]


# ---------------------------------------------------------------------------
# Email extraction
# ---------------------------------------------------------------------------

def _extract_emails_from_page(page) -> set[str]:
    """Pull emails from mailto hrefs and visible page text."""
    found: set[str] = set()

    # 1. mailto: links — most reliable signal
    try:
        hrefs = page.eval_on_selector_all(
            "a[href^='mailto:']",
            "els => els.map(e => e.getAttribute('href'))",
        )
        for href in hrefs:
            addr = href.replace("mailto:", "").split("?")[0].strip().lower()
            if _EMAIL_RE.match(addr):
                found.add(addr)
    except Exception:
        pass

    # 2. Regex over full page text (catches emails in plain text, spans, etc.)
    try:
        text = page.inner_text("body")
        for match in _EMAIL_RE.findall(text):
            found.add(match.lower())
    except Exception:
        pass

    return found


def _is_valid_email(addr: str) -> bool:
    """Filter out known false positives."""
    local, _, domain = addr.partition("@")
    if domain in DOMAIN_BLACKLIST:
        return False
    if any(bl in local for bl in EMAIL_BLACKLIST_SUBSTRINGS):
        return False
    # Skip addresses that look like file paths or JS artifacts
    if any(addr.endswith(ext) for ext in (".js", ".css", ".png", ".jpg", ".svg")):
        return False
    return True


# ---------------------------------------------------------------------------
# Per-company scrape
# ---------------------------------------------------------------------------

def _scrape_company(browser, base_url: str) -> dict:
    """
    Visit homepage + contact pages for one company.
    Returns dict with keys: emails (set), pages_scraped (list), status (str).
    """
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        java_script_enabled=True,
        ignore_https_errors=True,
    )
    page = context.new_page()
    page.set_default_timeout(BROWSER_TIMEOUT_MS)

    all_emails: set[str] = set()
    pages_scraped: list[str] = []
    status = "success"

    try:
        urls_to_try = _build_contact_urls(base_url)
        for url in urls_to_try:
            try:
                response = page.goto(url, wait_until="domcontentloaded")
                if response and response.status < 400:
                    emails = _extract_emails_from_page(page)
                    all_emails.update(emails)
                    pages_scraped.append(url)
                    logger.debug("  %s → %d email(s)", url, len(emails))
            except PlaywrightTimeout:
                logger.debug("  timeout: %s", url)
            except Exception as exc:
                logger.debug("  error on %s: %s", url, exc)

        if not pages_scraped:
            status = "unreachable"
        elif not all_emails:
            status = "no_emails"

    except Exception as exc:
        logger.warning("  fatal error for %s: %s", base_url, exc)
        status = "error"
    finally:
        context.close()

    valid_emails = {e for e in all_emails if _is_valid_email(e)}
    return {
        "emails": valid_emails,
        "pages_scraped": pages_scraped,
        "status": status,
    }


# ---------------------------------------------------------------------------
# CSV I/O
# ---------------------------------------------------------------------------

def _read_companies(input_path: Path, limit: int | None) -> list[dict]:
    """Read company rows from CSV. Accepts any encoding the project supports."""
    encodings = ["utf-8", "utf-8-sig", "utf-16", "latin-1"]
    rows = []
    for enc in encodings:
        try:
            with open(input_path, encoding=enc, newline="") as f:
                reader = csv.DictReader(f)
                if "company_name" not in (reader.fieldnames or []):
                    logger.error(
                        "Input CSV missing 'company_name' column. Got: %s",
                        reader.fieldnames,
                    )
                    sys.exit(1)
                if "website" not in (reader.fieldnames or []):
                    logger.error(
                        "Input CSV missing 'website' column. Got: %s",
                        reader.fieldnames,
                    )
                    sys.exit(1)
                for row in reader:
                    rows.append(row)
                    if limit and len(rows) >= limit:
                        break
            logger.info("Read %d companies from %s (encoding: %s)", len(rows), input_path, enc)
            return rows
        except (UnicodeDecodeError, UnicodeError):
            continue
    logger.error("Could not decode %s with any supported encoding", input_path)
    sys.exit(1)


def _open_output(output_path: Path, append: bool = False):
    """Open output CSV writer, writing header only when creating a new file."""
    mode = "a" if append and output_path.exists() else "w"
    write_header = mode == "w"
    f = open(output_path, mode=mode, encoding="utf-8", newline="")
    writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
    if write_header:
        writer.writeheader()
        f.flush()
    return f, writer


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(
    input_path: Path,
    output_path: Path,
    limit: int | None = None,
    delay: float = DELAY_BETWEEN_COMPANIES,
    headless: bool = True,
    resume: bool = False,
):
    companies = _read_companies(input_path, limit)

    # Resume: skip companies already in output CSV
    already_done: set[str] = set()
    if resume and output_path.exists():
        with open(output_path, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                already_done.add(row.get("company_name", "").strip())
        logger.info("Resuming — %d companies already done", len(already_done))

    out_file, writer = _open_output(output_path, append=resume)
    limiter = RateLimiter(min_delay=delay)

    total = len(companies)
    skipped = 0
    success = 0
    no_email = 0
    errors = 0

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=headless)
            logger.info("Browser launched (headless=%s)", headless)

            for i, row in enumerate(companies, start=1):
                name = row.get("company_name", "").strip()
                raw_url = row.get("website", "").strip()

                if resume and name in already_done:
                    skipped += 1
                    continue

                base_url = _normalize_url(raw_url)
                if not base_url:
                    logger.info("[%d/%d] SKIP  %-40s (no valid URL)", i, total, name)
                    writer.writerow({
                        "company_name": name,
                        "website": raw_url,
                        "emails_found": "",
                        "pages_scraped": "",
                        "status": "no_url",
                        "scraped_at": datetime.now(timezone.utc).isoformat(),
                    })
                    out_file.flush()
                    skipped += 1
                    continue

                logger.info("[%d/%d] Scraping %-40s %s", i, total, name, base_url)
                limiter.wait()

                result = _scrape_company(browser, base_url)

                emails_str = ", ".join(sorted(result["emails"]))
                pages_str = " | ".join(result["pages_scraped"])
                status = result["status"]

                writer.writerow({
                    "company_name": name,
                    "website": raw_url,
                    "emails_found": emails_str,
                    "pages_scraped": pages_str,
                    "status": status,
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                })
                out_file.flush()  # real-time write

                if status == "success" and result["emails"]:
                    success += 1
                    logger.info(
                        "  → found %d email(s): %s",
                        len(result["emails"]),
                        emails_str,
                    )
                elif status == "no_emails":
                    no_email += 1
                    logger.info("  → no emails found")
                else:
                    errors += 1
                    logger.info("  → status: %s", status)

            browser.close()

    finally:
        out_file.close()

    logger.info(
        "Done. success=%d  no_email=%d  errors=%d  skipped=%d  output=%s",
        success, no_email, errors, skipped, output_path,
    )


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Scrape publicly listed emails from company websites."
    )
    p.add_argument(
        "--input", "-i",
        required=True,
        type=Path,
        help="Input CSV with company_name and website columns.",
    )
    p.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        help="Output CSV path (default: <input_stem>_emails.csv next to input).",
    )
    p.add_argument(
        "--limit", "-n",
        type=int,
        default=None,
        help="Max number of companies to process (useful for testing).",
    )
    p.add_argument(
        "--delay",
        type=float,
        default=DELAY_BETWEEN_COMPANIES,
        help=f"Seconds between companies (default: {DELAY_BETWEEN_COMPANIES}).",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help="Skip companies already present in the output CSV.",
    )
    p.add_argument(
        "--no-headless",
        action="store_true",
        help="Show the browser window (useful for debugging).",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    output = args.output or args.input.parent / (args.input.stem + "_emails.csv")
    run(
        input_path=args.input,
        output_path=output,
        limit=args.limit,
        delay=args.delay,
        headless=not args.no_headless,
        resume=args.resume,
    )
