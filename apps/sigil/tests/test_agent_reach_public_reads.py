from __future__ import annotations

from unittest.mock import patch

import pytest

from sigil.agent_reach_public_reads import (
    PublicReadConfig,
    PublicReadValidationError,
    _resolve_public_host,
    _validate_public_address,
    fetch_public_github_read,
    fetch_public_webpage,
    fetch_rss_feed,
    fetch_youtube_transcript,
    web_search,
)

ENABLED = PublicReadConfig(enabled=True)
DISABLED = PublicReadConfig(enabled=False)


# --- config -----------------------------------------------------------------


def test_config_rejects_out_of_bounds_timeout() -> None:
    with pytest.raises(PublicReadValidationError, match="timeout"):
        PublicReadConfig(timeout_seconds=0)
    with pytest.raises(PublicReadValidationError, match="timeout"):
        PublicReadConfig(timeout_seconds=31)


def test_config_never_authenticates_or_mutates() -> None:
    assert ENABLED.can_authenticate is False
    assert ENABLED.can_mutate is False


# --- SSRF guard ---------------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.5",
        "192.168.1.1",
        "172.16.0.1",
        "169.254.1.1",
        "100.64.0.1",  # CGNAT / Tailscale
        "0.0.0.0",
        "::1",
        "224.0.0.1",  # multicast
    ],
)
def test_rejects_non_public_addresses(address: str) -> None:
    with pytest.raises(PublicReadValidationError, match="non-public"):
        _validate_public_address(address)


def test_accepts_public_address() -> None:
    _validate_public_address("93.184.216.34")  # example.com's long-standing public IP


def test_resolve_public_host_rejects_when_any_result_is_private() -> None:
    with patch(
        "socket.getaddrinfo",
        return_value=[
            (2, 1, 6, "", ("93.184.216.34", 0)),
            (2, 1, 6, "", ("127.0.0.1", 0)),
        ],
    ):
        with pytest.raises(PublicReadValidationError, match="non-public"):
            _resolve_public_host("attacker-controlled.example")


def test_resolve_public_host_fails_closed_on_dns_error() -> None:
    with patch("socket.getaddrinfo", side_effect=OSError("no such host")):
        with pytest.raises(PublicReadValidationError, match="DNS resolution failed"):
            _resolve_public_host("does-not-resolve.invalid")


def test_disabled_config_rejects_every_capability() -> None:
    for fn, args in (
        (fetch_public_webpage, ("https://example.com",)),
        (fetch_rss_feed, ("https://example.com/feed",)),
        (fetch_public_github_read, ("/repos/foo/bar/readme",)),
        (web_search, ("test query",)),
        (fetch_youtube_transcript, ("dQw4w9WgXcQ",)),
    ):
        with pytest.raises(PublicReadValidationError, match="disabled"):
            fn(*args, DISABLED)


def test_non_http_scheme_rejected() -> None:
    with pytest.raises(PublicReadValidationError, match="http/https"):
        fetch_public_webpage("file:///etc/passwd", ENABLED)


def test_embedded_credentials_rejected() -> None:
    with pytest.raises(PublicReadValidationError, match="credentials"):
        fetch_public_webpage("https://user:pass@example.com", ENABLED)


# --- parsing correctness (network mocked) ------------------------------------


def test_fetch_public_webpage_strips_tags_and_scripts() -> None:
    html = b"<html><head><script>evil()</script></head><body><p>Hello <b>world</b></p></body></html>"
    with patch(
        "sigil.agent_reach_public_reads._safe_get",
        return_value=(200, html, "example.com"),
    ):
        result = fetch_public_webpage("https://example.com", ENABLED)

    assert result["responding"] is True
    assert "evil()" not in result["text"]
    assert "Hello" in result["text"] and "world" in result["text"]


def test_fetch_rss_feed_extracts_items() -> None:
    feed = b"""<rss><channel>
      <item><title>First</title><link>https://example.com/1</link><description>One</description></item>
      <item><title>Second</title><link>https://example.com/2</link><description>Two</description></item>
    </channel></rss>"""
    with patch(
        "sigil.agent_reach_public_reads._safe_get",
        return_value=(200, feed, "example.com"),
    ):
        result = fetch_rss_feed("https://example.com/feed", ENABLED)

    assert result["item_count"] == 2
    assert result["items"][0]["title"] == "First"
    assert result["items"][1]["link"] == "https://example.com/2"


def test_fetch_public_github_read_enforces_allowlist() -> None:
    with pytest.raises(PublicReadValidationError, match="allowlist"):
        fetch_public_github_read("/user/repos", ENABLED)  # not a read-only repo endpoint

    with pytest.raises(PublicReadValidationError, match="allowlist"):
        fetch_public_github_read("/repos/foo/bar/git/refs/heads/main", ENABLED)  # mutation-adjacent


def test_fetch_public_github_read_accepts_allowlisted_path() -> None:
    with patch(
        "sigil.agent_reach_public_reads._safe_get",
        return_value=(200, b'{"name": "hermes-agent"}', "api.github.com"),
    ):
        result = fetch_public_github_read("/repos/firecattechllc/hermes-agent", ENABLED)

    assert result["responding"] is True
    assert "hermes-agent" in result["body"]


def test_web_search_rejects_blank_query() -> None:
    with pytest.raises(PublicReadValidationError, match="blank"):
        web_search("   ", ENABLED)


def test_web_search_parses_results() -> None:
    html = b"""
    <a class="result__a" href="https://example.com/a">Example A</a>
    <a class="result__snippet">Snippet A</a>
    """
    with patch(
        "sigil.agent_reach_public_reads._safe_get",
        return_value=(200, html, "html.duckduckgo.com"),
    ):
        result = web_search("example", ENABLED)

    assert result["result_count"] == 1
    assert result["results"][0]["title"] == "Example A"
    assert result["results"][0]["url"] == "https://example.com/a"


def test_fetch_youtube_transcript_degrades_when_no_captions_found() -> None:
    with patch(
        "sigil.agent_reach_public_reads._safe_get",
        return_value=(200, b"<html>no captions here</html>", "www.youtube.com"),
    ):
        result = fetch_youtube_transcript("dQw4w9WgXcQ", ENABLED)

    assert result["available"] is False
    assert "reason" in result


def test_fetch_youtube_transcript_rejects_malformed_video_id() -> None:
    with pytest.raises(PublicReadValidationError, match="malformed"):
        fetch_youtube_transcript("../../etc/passwd", ENABLED)
