"""Real, disabled-by-default public-read capability dispatch for Agent Reach.

Hermes add-on Phase F. Builds the real dispatch layer that
``sigil.agent_reach_adapter`` deliberately does not (that module is a
Stage 8A capability *selector and evidence contract*, not a transport; its
``AgentReachConfig.can_dispatch`` remains hardcoded ``False`` and its own
certification claims are untouched by this module).

Scope, per ``docs/beta/post-phase9/HERMES_ECOSYSTEM_DECISIONS.md`` D-005
("Agent Reach is a capability selector, not an authority") and D-006
("Agent Reach begins with public reads only"):

- Public webpage reads (GET only).
- RSS/Atom feed reads (GET only).
- Public, unauthenticated GitHub REST reads (GET only, no token used).
- Public web search via DuckDuckGo's unauthenticated HTML endpoint.
- Best-effort YouTube transcript reads via YouTube's public, unauthenticated
  timedtext endpoint (explicitly best-effort: this is not a documented,
  versioned API and YouTube may change it without notice; failures degrade
  to an ``unavailable`` result rather than raising).

Every function in this module performs a real network request when
``config.enabled`` is ``True``. Every function is fail-closed against:
authentication of any kind, non-GET methods, non-HTTP(S) schemes, and
Server-Side Request Forgery (SSRF) into private, loopback, link-local, or
otherwise non-public address space -- including DNS-rebinding, by resolving
the hostname once, validating every resolved address, and connecting
directly to the validated address with the original hostname pinned in the
``Host`` header (so a second, different resolution never happens at connect
time).
"""

from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit

AGENT_REACH_PUBLIC_READS_SCHEMA_VERSION = 1

_MAX_RESPONSE_BYTES_DEFAULT = 2_000_000
_MAX_ITEMS_DEFAULT = 20
_USER_AGENT = "HermesAgentReach/1.0 (+https://github.com/firecattechllc/hermes-agent; public-read-only)"

_GITHUB_API_HOST = "api.github.com"
_DUCKDUCKGO_HOST = "html.duckduckgo.com"
_YOUTUBE_HOST = "www.youtube.com"

_ALLOWED_GITHUB_PATH = re.compile(
    r"^/repos/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+"
    r"(/(readme|contents(/[A-Za-z0-9._/\-]*)?|releases/latest|issues|pulls|commits|languages|topics))?$"
)

_TAG_STRIP = re.compile(r"(?is)<(script|style)[^>]*>.*?</\1>")
_TAG = re.compile(r"(?s)<[^>]+>")
_WHITESPACE = re.compile(r"[ \t\f\v]+")
_BLANK_LINES = re.compile(r"\n{3,}")

_RSS_ITEM = re.compile(r"(?is)<item\b.*?</item>|<entry\b.*?</entry>")
_RSS_TITLE = re.compile(r"(?is)<title\b[^>]*>(.*?)</title>")
_RSS_LINK = re.compile(r"(?is)<link\b[^>]*>(.*?)</link>|<link\b[^>]*href=[\"']([^\"']+)[\"']")
_RSS_DESC = re.compile(r"(?is)<(?:description|summary)\b[^>]*>(.*?)</(?:description|summary)>")

_DUCKDUCKGO_RESULT = re.compile(
    r'(?is)<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>'
)
_DUCKDUCKGO_SNIPPET = re.compile(
    r'(?is)<a[^>]+class="result__snippet"[^>]*>(.*?)</a>'
)

_CAPTION_TRACK_URL = re.compile(r'"captionTracks":\s*(\[[^\]]*\])')
_BASE_URL_FIELD = re.compile(r'"baseUrl":\s*"([^"]+)"')
_TEXT_TAG = re.compile(r"(?is)<text[^>]*>(.*?)</text>")


class PublicReadValidationError(ValueError):
    """A public-read request failed closed."""


@dataclass(frozen=True, slots=True)
class PublicReadConfig:
    enabled: bool = False
    timeout_seconds: float = 10.0
    max_response_bytes: int = _MAX_RESPONSE_BYTES_DEFAULT
    schema_version: int = AGENT_REACH_PUBLIC_READS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != AGENT_REACH_PUBLIC_READS_SCHEMA_VERSION:
            raise PublicReadValidationError(
                "unsupported Agent Reach public-read config schema"
            )
        if not 0 < self.timeout_seconds <= 30:
            raise PublicReadValidationError(
                "public-read timeout is outside bounds"
            )
        if not 1_000 <= self.max_response_bytes <= 10_000_000:
            raise PublicReadValidationError(
                "public-read response size cap is outside bounds"
            )

    @property
    def can_authenticate(self) -> bool:
        return False

    @property
    def can_mutate(self) -> bool:
        return False


def _require_enabled(config: PublicReadConfig) -> None:
    if not config.enabled:
        raise PublicReadValidationError(
            "Agent Reach public-read dispatch is disabled by policy"
        )


def _validate_public_address(address: str) -> None:
    parsed = ipaddress.ip_address(address)
    if (
        parsed.is_private
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_multicast
        or parsed.is_reserved
        or parsed.is_unspecified
        or parsed in ipaddress.ip_network("100.64.0.0/10")  # CGNAT / Tailscale range
    ):
        raise PublicReadValidationError(
            f"refusing to dispatch to non-public address: {address}"
        )


def _resolve_public_host(hostname: str) -> str:
    """Resolve ``hostname`` once, validate every result, return one safe IP.

    Resolving once and reusing the validated address for the actual
    connection (with ``Host`` pinned) closes the classic SSRF
    DNS-rebinding gap where a second resolution at connect time returns a
    different, private address.
    """

    try:
        results = socket.getaddrinfo(hostname, None)
    except OSError as error:
        raise PublicReadValidationError(f"DNS resolution failed: {error}") from error

    if not results:
        raise PublicReadValidationError("DNS resolution returned no addresses")

    addresses = {result[4][0] for result in results}
    for address in addresses:
        _validate_public_address(address)

    # Prefer IPv4 when both families resolve: purely a routability choice
    # (some local networks lack a working IPv6 default route), not a
    # security one -- every candidate address was already validated above.
    ipv4_addresses = [addr for addr in addresses if ":" not in addr]
    return sorted(ipv4_addresses or addresses)[0]


def _safe_get(
    url: str,
    config: PublicReadConfig,
    *,
    allowed_host: str | None = None,
) -> tuple[int, bytes, str]:
    """Perform one bounded, SSRF-safe, unauthenticated GET.

    Returns ``(status, body, final_host)``. Never sends credentials, never
    follows a redirect (each capability's own fetch is a single request),
    and truncates the body at ``config.max_response_bytes``.

    The TCP connection is dialed directly to the pre-validated, resolved IP
    address (closing the DNS-rebinding gap: a second, different resolution
    at connect time never happens), while the ``Host`` header and TLS SNI
    still use the original hostname, so certificate validation for HTTPS
    targets remains correct -- unlike naively rewriting the URL to an IP
    literal, which breaks TLS hostname verification.
    """

    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise PublicReadValidationError("only http/https URLs are permitted")
    if not parsed.hostname:
        raise PublicReadValidationError("URL is missing a hostname")
    if parsed.username or parsed.password:
        raise PublicReadValidationError("URLs with embedded credentials are prohibited")
    if allowed_host is not None and parsed.hostname.lower() != allowed_host:
        raise PublicReadValidationError(
            f"host is not allowlisted for this capability: {parsed.hostname}"
        )

    hostname = parsed.hostname
    resolved_ip = _resolve_public_host(hostname)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    connection: http.client.HTTPConnection | http.client.HTTPSConnection

    try:
        raw_sock = socket.create_connection((resolved_ip, port), timeout=config.timeout_seconds)
        if parsed.scheme == "https":
            context = ssl.create_default_context()
            wrapped = context.wrap_socket(raw_sock, server_hostname=hostname)
            connection = http.client.HTTPSConnection(hostname, timeout=config.timeout_seconds)
            connection.sock = wrapped
        else:
            connection = http.client.HTTPConnection(hostname, timeout=config.timeout_seconds)
            connection.sock = raw_sock

        connection.putrequest("GET", path, skip_host=False)
        connection.putheader("Accept", "*/*")
        connection.putheader("User-Agent", _USER_AGENT)
        connection.endheaders()

        response = connection.getresponse()
        body = response.read(config.max_response_bytes)
        status = response.status
        connection.close()
    except (OSError, ssl.SSLError, http.client.HTTPException) as error:
        raise PublicReadValidationError(f"public-read request failed: {error}") from error

    return status, body, hostname


def fetch_public_webpage(url: str, config: PublicReadConfig) -> dict[str, Any]:
    """Fetch one public webpage and return sanitized plain text.

    SSRF-guarded: any hostname resolving to a private, loopback,
    link-local, CGNAT/Tailscale, or otherwise non-public address is
    rejected before any connection is attempted.
    """

    _require_enabled(config)

    status, body, host = _safe_get(url, config)
    text = body.decode("utf-8", errors="replace")
    text = _TAG_STRIP.sub(" ", text)
    text = _TAG.sub(" ", text)
    text = _WHITESPACE.sub(" ", text)
    text = _BLANK_LINES.sub("\n\n", text)
    text = text.strip()

    return {
        "url": url,
        "host": host,
        "status": status,
        "responding": 200 <= status < 300,
        "text": text[:20_000],
        "truncated": len(body) >= config.max_response_bytes,
    }


def fetch_rss_feed(url: str, config: PublicReadConfig) -> dict[str, Any]:
    """Fetch one RSS/Atom feed and return its items as plain-text entries.

    Uses a bounded regex extractor rather than an XML parser so this
    module carries no XML-entity-expansion (XXE / billion-laughs) attack
    surface at all -- there is no XML parser in this path.
    """

    _require_enabled(config)

    status, body, host = _safe_get(url, config)
    text = body.decode("utf-8", errors="replace")

    items: list[dict[str, str]] = []
    for match in _RSS_ITEM.finditer(text):
        block = match.group(0)
        title_match = _RSS_TITLE.search(block)
        link_match = _RSS_LINK.search(block)
        desc_match = _RSS_DESC.search(block)

        link = ""
        if link_match:
            link = (link_match.group(1) or link_match.group(2) or "").strip()

        items.append(
            {
                "title": _TAG.sub("", title_match.group(1)).strip() if title_match else "",
                "link": link,
                "summary": _TAG.sub("", desc_match.group(1)).strip()[:1_000] if desc_match else "",
            }
        )
        if len(items) >= _MAX_ITEMS_DEFAULT:
            break

    return {
        "url": url,
        "host": host,
        "status": status,
        "responding": 200 <= status < 300,
        "item_count": len(items),
        "items": items,
    }


def fetch_public_github_read(path: str, config: PublicReadConfig) -> dict[str, Any]:
    """Perform one unauthenticated GET against a public GitHub REST read endpoint.

    ``path`` must match the closed allowlist of read-only endpoint shapes
    (repo contents/readme/releases/issues/pulls/commits/languages/topics);
    no token is ever attached, so this only ever sees what an anonymous
    visitor could see, and only ever GETs.
    """

    _require_enabled(config)

    if not path.startswith("/") or _ALLOWED_GITHUB_PATH.fullmatch(path) is None:
        raise PublicReadValidationError(
            "GitHub read path is not on the public-read allowlist"
        )

    url = f"https://{_GITHUB_API_HOST}{path}"
    status, body, host = _safe_get(url, config, allowed_host=_GITHUB_API_HOST)

    return {
        "path": path,
        "host": host,
        "status": status,
        "responding": 200 <= status < 300,
        "body": body.decode("utf-8", errors="replace")[:50_000],
    }


def web_search(query: str, config: PublicReadConfig) -> dict[str, Any]:
    """Public, unauthenticated web search via DuckDuckGo's HTML-only endpoint.

    No API key exists or is used; this is the same unauthenticated HTML
    surface a browser without JavaScript would see.
    """

    _require_enabled(config)

    if not query.strip():
        raise PublicReadValidationError("search query cannot be blank")

    url = f"https://{_DUCKDUCKGO_HOST}/html/?q={quote(query.strip()[:400])}"
    status, body, host = _safe_get(url, config, allowed_host=_DUCKDUCKGO_HOST)
    text = body.decode("utf-8", errors="replace")

    results: list[dict[str, str]] = []
    snippets = _DUCKDUCKGO_SNIPPET.findall(text)
    for index, match in enumerate(_DUCKDUCKGO_RESULT.finditer(text)):
        href = match.group(1)
        title = _TAG.sub("", match.group(2)).strip()
        snippet = _TAG.sub("", snippets[index]).strip() if index < len(snippets) else ""
        results.append({"title": title, "url": href, "snippet": snippet[:500]})
        if len(results) >= _MAX_ITEMS_DEFAULT:
            break

    return {
        "query": query,
        "host": host,
        "status": status,
        "responding": 200 <= status < 300,
        "result_count": len(results),
        "results": results,
    }


def fetch_youtube_transcript(
    video_id: str, config: PublicReadConfig, *, language: str = "en"
) -> dict[str, Any]:
    """Best-effort read of a YouTube video's public auto/manual transcript.

    Explicitly best-effort: this walks YouTube's public video page for an
    embedded ``captionTracks`` list and then reads the matching
    ``timedtext`` URL, both of which are undocumented, unversioned surfaces
    that YouTube can change without notice. A failure at any step degrades
    to ``{"available": False, ...}`` rather than raising, so a caller never
    has to treat "YouTube changed its page format" as a crash.
    """

    _require_enabled(config)

    if not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", video_id):
        raise PublicReadValidationError("malformed YouTube video ID")

    watch_url = f"https://{_YOUTUBE_HOST}/watch?v={video_id}"

    try:
        status, body, _host = _safe_get(watch_url, config, allowed_host=_YOUTUBE_HOST)
    except PublicReadValidationError:
        raise
    except Exception:  # noqa: BLE001 - best-effort surface, never crash the caller
        return {"video_id": video_id, "available": False, "reason": "page fetch failed"}

    if status != 200:
        return {"video_id": video_id, "available": False, "reason": f"HTTP {status}"}

    page = body.decode("utf-8", errors="replace")
    tracks_match = _CAPTION_TRACK_URL.search(page)
    if not tracks_match:
        return {
            "video_id": video_id,
            "available": False,
            "reason": "no caption tracks found (video may have none, or YouTube's page format changed)",
        }

    base_url_match = _BASE_URL_FIELD.search(tracks_match.group(1))
    if not base_url_match:
        return {"video_id": video_id, "available": False, "reason": "no caption track URL found"}

    caption_url = base_url_match.group(1).encode("utf-8").decode("unicode_escape")
    caption_url = caption_url.replace("\\u0026", "&")

    try:
        status, body, _host = _safe_get(caption_url, config, allowed_host=_YOUTUBE_HOST)
    except PublicReadValidationError:
        return {
            "video_id": video_id,
            "available": False,
            "reason": "caption track resolved off the youtube.com host; refused",
        }

    if status != 200 or not body:
        return {"video_id": video_id, "available": False, "reason": f"caption fetch HTTP {status}"}

    text_body = body.decode("utf-8", errors="replace")
    segments = [_TAG.sub("", segment).strip() for segment in _TEXT_TAG.findall(text_body)]
    segments = [segment for segment in segments if segment]

    return {
        "video_id": video_id,
        "available": bool(segments),
        "language_requested": language,
        "segment_count": len(segments),
        "transcript": " ".join(segments)[:20_000],
    }
