"""
Email scraper configuration.
Overrides project-wide defaults from scrapers/common/config.py.
"""

# Pages to visit on each company domain (tried in order)
CONTACT_PATHS = [
    "/contact",
    "/contact-us",
    "/contacts",
    "/about",
    "/about-us",
    "/team",
    "/our-team",
    "/support",
    "/help",
    "/company",
]

# Maximum pages to visit per company (homepage + contact paths)
MAX_PAGES_PER_COMPANY = 6

# Playwright navigation timeout in milliseconds
BROWSER_TIMEOUT_MS = 15_000

# Seconds to wait between companies (on top of page load time)
DELAY_BETWEEN_COMPANIES = 1.0

# Save output CSV row by row (flush after each company)
REALTIME_WRITE = True

# Email patterns to discard (substring match against local-part)
EMAIL_BLACKLIST_SUBSTRINGS = {
    "noreply",
    "no-reply",
    "donotreply",
    "do-not-reply",
    "example",
    "test",
    "placeholder",
    "domain",
    "yourname",
    "username",
    "user@",
    "email@",
    "name@",
}

# Domains to discard entirely (false positives from HTML/CSS/JS)
DOMAIN_BLACKLIST = {
    "example.com",
    "example.org",
    "sentry.io",
    "w3.org",
    "schema.org",
    "google.com",
    "github.com",
    "amazonaws.com",
    "cloudfront.net",
}

# Minimum TLD length to accept (filters out ".js", ".css", ".png" etc.)
MIN_TLD_LENGTH = 2

# Output CSV columns
OUTPUT_COLUMNS = [
    "company_name",
    "website",
    "emails_found",
    "pages_scraped",
    "status",
    "scraped_at",
]
