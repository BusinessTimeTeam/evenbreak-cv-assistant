"""Tests for the knowledge-base web scraper. Network calls are stubbed."""

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import scrape

FIXTURE = Path(__file__).parent / "fixtures" / "sample.html"
CAPTURED = date(2026, 10, 28)


def test_extract_keeps_article_and_drops_boilerplate():
    markdown = scrape.extract_markdown(FIXTURE.read_text(), "https://example.org/adjustments/")

    assert "Asking for adjustments at interview" in markdown
    assert "any stage of the recruitment process" in markdown
    assert "option of a video interview" in markdown
    for boilerplate in ("NAVLINK", "COOKIEBANNER", "FOOTERTEXT"):
        assert boilerplate not in markdown


def test_output_filename():
    assert scrape.output_filename("https://www.evenbreak.com/") == "www-evenbreak-com.md"
    assert (
        scrape.output_filename("https://www.evenbreak.com/article/why-x/")
        == "www-evenbreak-com-article-why-x.md"
    )


def test_render_document_header():
    document = scrape.render_document("https://www.evenbreak.com/", "# Body", CAPTURED)

    lines = document.splitlines()
    assert lines[0] == "> Source: https://www.evenbreak.com/ — captured 2026-10-28"
    assert lines[1] == ""
    assert lines[2] == "# Body"


def test_fetch_uses_honest_user_agent_and_reports_403(monkeypatch, tmp_path):
    seen = {}

    def fake_fetch_response(url, decode=True, config=None):
        seen["user_agent"] = config.get("DEFAULT", "USER_AGENTS")
        return SimpleNamespace(status=403, html="<html>Access Denied</html>")

    monkeypatch.setattr(scrape, "fetch_response", fake_fetch_response)
    monkeypatch.setattr(scrape, "robots_allows", lambda url: True)

    failures = scrape.scrape_urls(["https://www.evenbreak.com/"], tmp_path, delay=0, captured=CAPTURED)

    assert seen["user_agent"] == scrape.USER_AGENT
    assert scrape.USER_AGENT == "EvenbreakCVAssistant/1.0 (+contact: mail@mayortech.co.uk)"
    assert len(failures) == 1
    assert "403" in failures[0]
    assert scrape.USER_AGENT in failures[0]
    assert list(tmp_path.iterdir()) == []


def test_robots_disallowed_url_is_skipped(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(scrape, "fetch_response", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(scrape, "robots_allows", lambda url: False)

    failures = scrape.scrape_urls(["https://example.org/private/"], tmp_path, delay=0, captured=CAPTURED)

    assert calls == []
    assert "robots.txt" in failures[0]


def test_successful_fetch_writes_markdown_file(monkeypatch, tmp_path):
    monkeypatch.setattr(
        scrape,
        "fetch_response",
        lambda url, decode=True, config=None: SimpleNamespace(status=200, html=FIXTURE.read_text()),
    )
    monkeypatch.setattr(scrape, "robots_allows", lambda url: True)

    failures = scrape.scrape_urls(["https://example.org/adjustments/"], tmp_path, delay=0, captured=CAPTURED)

    assert failures == []
    written = (tmp_path / "example-org-adjustments.md").read_text()
    assert written.startswith("> Source: https://example.org/adjustments/ — captured 2026-10-28")
    assert "reasonable adjustments" in written


def test_robots_txt_forbidden_means_disallow(monkeypatch):
    # Matches urllib.robotparser: a 401/403 on robots.txt disallows everything.
    monkeypatch.setattr(
        scrape,
        "fetch_response",
        lambda url, decode=True, config=None: SimpleNamespace(status=403, html="denied"),
    )

    assert scrape.robots_allows("https://www.evenbreak.com/") is False


def test_robots_txt_missing_means_allow(monkeypatch):
    monkeypatch.setattr(
        scrape,
        "fetch_response",
        lambda url, decode=True, config=None: SimpleNamespace(status=404, html=""),
    )

    assert scrape.robots_allows("https://example.org/page/") is True


def test_robots_txt_is_fetched_once_per_site(monkeypatch, tmp_path):
    fetched = []

    def fake_fetch_response(url, decode=True, config=None):
        fetched.append(url)
        if url.endswith("/robots.txt"):
            return SimpleNamespace(status=200, html="User-agent: *\nAllow: /\n")
        return SimpleNamespace(status=200, html=FIXTURE.read_text())

    monkeypatch.setattr(scrape, "fetch_response", fake_fetch_response)
    urls = [f"https://example.org/page-{i}/" for i in range(3)]

    scrape.scrape_urls(urls, tmp_path, delay=0, captured=CAPTURED)

    assert fetched.count("https://example.org/robots.txt") == 1
    assert sum(not u.endswith("robots.txt") for u in fetched) == 3


def test_sitemap_urls_are_read_from_loc_entries(monkeypatch):
    sitemap = (
        '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://example.org/a/</loc></url><url><loc>https://example.org/b/</loc></url>"
        "</urlset>"
    )
    monkeypatch.setattr(
        scrape,
        "fetch_response",
        lambda url, decode=True, config=None: SimpleNamespace(status=200, html=sitemap),
    )

    assert scrape.sitemap_urls("https://example.org/sitemap.xml") == [
        "https://example.org/a/",
        "https://example.org/b/",
    ]
