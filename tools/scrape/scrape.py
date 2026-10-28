"""Turn web pages into knowledge-base documents.

Extracts each page's main content as clean markdown with trafilatura (dropping
navigation, cookie banners and footers) and writes one file per page, headed
with its source URL and capture date.

Usage (normally through docker compose; see the README):
    python scrape.py URL [URL ...]
    python scrape.py --sitemap SITEMAP_URL
    python scrape.py --html SAVED_FILE --url ORIGINAL_URL

The scraper identifies itself honestly (USER_AGENT), respects robots.txt and
pauses between requests. Sites that block automated requests (e.g. a 403 from
bot protection) are reported, not worked around: ask the site owner to
whitelist USER_AGENT, or save the page from a browser and use --html.
"""

import argparse
import re
import sys
import time
from datetime import date
from functools import lru_cache
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import trafilatura
from trafilatura.downloads import fetch_response
from trafilatura.settings import use_config

USER_AGENT = "EvenbreakCVAssistant/1.0 (+contact: mail@mayortech.co.uk)"

_LOC = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.IGNORECASE | re.DOTALL)


def _config():
    config = use_config()
    config.set("DEFAULT", "USER_AGENTS", USER_AGENT)
    return config


def _get(url: str) -> tuple[int | None, str]:
    response = fetch_response(url, decode=True, config=_config())
    if response is None:
        return None, ""
    return response.status, response.html or ""


def extract_markdown(html: str, url: str) -> str | None:
    return trafilatura.extract(
        html, url=url, output_format="markdown", include_links=False, include_tables=True
    )


def output_filename(url: str) -> str:
    parts = urlsplit(url)
    slug = re.sub(r"[^a-z0-9]+", "-", f"{parts.netloc}{parts.path}".lower()).strip("-")
    return f"{slug}.md"


def render_document(url: str, markdown: str, captured: date) -> str:
    return f"> Source: {url} — captured {captured.isoformat()}\n\n{markdown.strip()}\n"


@lru_cache(maxsize=None)
def _robots_rules(robots_url: str) -> RobotFileParser | bool:
    """Parsed robots.txt for a site, or True/False for allow-all/disallow-all.

    Cached so a site's robots.txt is fetched once per run, not once per page;
    scrape_urls clears the cache at the start of each run.
    """
    status, body = _get(robots_url)
    if status in (401, 403):
        return False  # same rule as urllib.robotparser
    if status is None or status >= 400:
        return True  # no robots.txt: nothing is disallowed
    parser = RobotFileParser()
    parser.parse(body.splitlines())
    return parser


def robots_allows(url: str) -> bool:
    rules = _robots_rules(urljoin(url, "/robots.txt"))
    if isinstance(rules, bool):
        return rules
    return rules.can_fetch(USER_AGENT, url)


def sitemap_urls(sitemap_url: str) -> list[str]:
    """Page URLs from a sitemap; a sitemap index is followed one level down."""
    status, body = _get(sitemap_url)
    if status != 200:
        raise RuntimeError(f"could not fetch sitemap {sitemap_url} (status {status})")
    locs = _LOC.findall(body)
    if "<sitemapindex" in body.lower():
        return [url for child in locs for url in sitemap_urls(child)]
    return locs


def _write(out_dir: Path, url: str, markdown: str, captured: date) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / output_filename(url)
    path.write_text(render_document(url, markdown, captured))
    return path


def scrape_html_file(html_path: Path, url: str, out_dir: Path, captured: date) -> Path:
    markdown = extract_markdown(html_path.read_text(errors="replace"), url)
    if not markdown:
        raise RuntimeError(f"no main content found in {html_path}")
    return _write(out_dir, url, markdown, captured)


def scrape_urls(urls: list[str], out_dir: Path, *, delay: float, captured: date) -> list[str]:
    """Scrape each URL; return a description of every failure (empty if none)."""
    _robots_rules.cache_clear()
    failures = []
    for i, url in enumerate(urls):
        if i and delay:
            time.sleep(delay)
        if not robots_allows(url):
            failures.append(f"{url}: disallowed by robots.txt for {USER_AGENT}")
            continue
        status, html = _get(url)
        if status != 200:
            failures.append(
                f"{url}: HTTP {status}. If the site is blocking automated requests, "
                f"ask the owner to whitelist the user agent {USER_AGENT!r}, "
                "or save the page from a browser and use --html."
            )
            continue
        markdown = extract_markdown(html, url)
        if not markdown:
            failures.append(f"{url}: no main content found")
            continue
        print(f"wrote {_write(out_dir, url, markdown, captured)}")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Turn web pages into knowledge-base documents.")
    parser.add_argument("urls", nargs="*", help="page URLs to scrape")
    parser.add_argument("--sitemap", help="scrape every page listed in this sitemap")
    parser.add_argument("--html", type=Path, help="a page saved from a browser")
    parser.add_argument("--url", help="the original URL of the --html page")
    parser.add_argument("--out", type=Path, default=Path("/out"), help="output directory")
    parser.add_argument("--delay", type=float, default=2.0, help="seconds between requests")
    args = parser.parse_args(argv)
    captured = date.today()

    if args.html:
        if not args.url:
            parser.error("--html needs --url (the page's original address)")
        print(f"wrote {scrape_html_file(args.html, args.url, args.out, captured)}")
        return 0

    urls = list(args.urls)
    if args.sitemap:
        urls += sitemap_urls(args.sitemap)
    if not urls:
        parser.error("give at least one URL, --sitemap, or --html")

    failures = scrape_urls(urls, args.out, delay=args.delay, captured=captured)
    for failure in failures:
        print(f"failed: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
