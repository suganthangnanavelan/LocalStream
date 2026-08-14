"""
metadata/tmdb_client.py — M4

Thin wrapper around TMDB's v3 REST API. Only the endpoints M4 actually
needs: search (to find candidates for matcher.py) and details (to pull
poster/backdrop/synopsis/genres/rating/runtime/original_language/keywords
for a confirmed match). No `/similar` or `/recommendations` calls here —
those feed Section 4d's recommendation engine, which is M5a, not M4;
`Title.similar_title_ids` stays empty until then (Section 2).

Movie and TV responses use different field names for the same concepts
(`title` vs `name`, `release_date` vs `first_air_date`, keywords nested
differently) — everything here is normalized into one `TmdbDetails` shape
so matcher.py / classifier.py / enricher.py never need to branch on
content type.
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    # curl_cffi wraps real libcurl (the same engine curl.exe uses) and can
    # impersonate an actual browser's TLS fingerprint. Plain `requests`
    # goes through OpenSSL/urllib3, which some Cloudflare-backed APIs
    # (TMDB included) fingerprint and silently reset — no header, retry,
    # or timeout change can fix that, since the block happens during the
    # TLS handshake, before any HTTP header is ever sent. This is why
    # `curl.exe` reaches TMDB fine while plain Python `requests` gets
    # reset on every single attempt: two different, distinguishable TLS
    # stacks talking to a fingerprinting-aware host.
    from curl_cffi import requests as cffi_requests

    _HAVE_CURL_CFFI = True
except ImportError:
    cffi_requests = None
    _HAVE_CURL_CFFI = False

logger = logging.getLogger(__name__)

BASE_URL = "https://api.themoviedb.org/3"
# Section 4: posters/backdrops cached locally forever — pick one fixed
# size per asset type so re-runs always hit the same cached file (w780
# poster / w1280 backdrop are TMDB's "detail page" sizes, plenty sharp
# for a poster wall / backdrop hero without pulling the full-res original).
IMAGE_BASE_URL = "https://image.tmdb.org/t/p"
POSTER_SIZE = "w780"
BACKDROP_SIZE = "w1280"
STILL_SIZE = "w300"  # episode thumbnails (Section 2 Episode.thumbnail_path)


class TmdbError(RuntimeError):
    """Network failure, bad API key, or malformed response — caught by
    enricher.py per-title so one title's TMDB trouble (or no internet at
    all) doesn't abort the whole enrichment pass; see Section 4's "offline
    fallback art generated ... if no match/no internet"."""


@dataclass
class TmdbCandidate:
    """One row from a /search/movie or /search/tv result — enough for
    matcher.py to score candidates without a second round-trip."""
    tmdb_id: int
    title: str
    year: Optional[int]
    popularity: float


@dataclass
class TmdbDetails:
    """Normalized detail response, movie or TV alike."""
    tmdb_id: int
    title: str
    synopsis: Optional[str]
    genres: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    rating: Optional[float] = None
    runtime: Optional[int] = None            # minutes; movies only, None for TV
    original_language: Optional[str] = None  # ISO 639-1, e.g. "ja", "en"
    poster_path: Optional[str] = None        # TMDB's raw path, e.g. "/abc.jpg"
    backdrop_path: Optional[str] = None


def poster_url(poster_path: str) -> str:
    return f"{IMAGE_BASE_URL}/{POSTER_SIZE}{poster_path}"


def backdrop_url(backdrop_path: str) -> str:
    return f"{IMAGE_BASE_URL}/{BACKDROP_SIZE}{backdrop_path}"


def still_url(still_path: str) -> str:
    return f"{IMAGE_BASE_URL}/{STILL_SIZE}{still_path}"


class TmdbClient:
    def __init__(self, api_key: str, timeout_s: float = 10.0, session: Optional[object] = None) -> None:
        if not api_key:
            raise ValueError("TMDB API key is required")
        self._api_key = api_key
        # curl_cffi's Session.get() takes one timeout value, not requests'
        # (connect, read) tuple, so it's stored separately per backend.
        self._timeout_s = timeout_s
        self._timeout = (5.0, timeout_s)
        # Only rebuild the session internally on retry (see _get) when we
        # created it ourselves — a caller-injected session (tests use a
        # mock to count/simulate retries) must never get silently swapped
        # out from under it.
        self._owns_session = session is None
        self._session = session or build_retrying_session()

    @property
    def session(self):
        """Exposes the retry-configured session so callers downloading
        TMDB images (enricher.py) can share the same retry/backoff
        behavior instead of building a second, unconfigured session."""
        return self._session

    # -- search -----------------------------------------------------------

    def search_movie(self, query: str, year: Optional[int] = None) -> list[TmdbCandidate]:
        params = {"query": query}
        if year is not None:
            params["year"] = year
        data = self._get("/search/movie", params)
        return [
            TmdbCandidate(
                tmdb_id=r["id"],
                title=r.get("title") or r.get("original_title") or "",
                year=_parse_year(r.get("release_date")),
                popularity=float(r.get("popularity") or 0.0),
            )
            for r in data.get("results", [])
        ]

    def search_tv(self, query: str, year: Optional[int] = None) -> list[TmdbCandidate]:
        params = {"query": query}
        if year is not None:
            params["first_air_date_year"] = year
        data = self._get("/search/tv", params)
        return [
            TmdbCandidate(
                tmdb_id=r["id"],
                title=r.get("name") or r.get("original_name") or "",
                year=_parse_year(r.get("first_air_date")),
                popularity=float(r.get("popularity") or 0.0),
            )
            for r in data.get("results", [])
        ]

    # -- details ------------------------------------------------------------

    def movie_details(self, tmdb_id: int) -> TmdbDetails:
        data = self._get(f"/movie/{tmdb_id}", {"append_to_response": "keywords"})
        keywords = [k["name"] for k in data.get("keywords", {}).get("keywords", [])]
        return TmdbDetails(
            tmdb_id=data["id"],
            title=data.get("title") or data.get("original_title") or "",
            synopsis=data.get("overview") or None,
            genres=[g["name"] for g in data.get("genres", [])],
            keywords=keywords,
            rating=data.get("vote_average"),
            runtime=data.get("runtime"),
            original_language=data.get("original_language"),
            poster_path=data.get("poster_path"),
            backdrop_path=data.get("backdrop_path"),
        )

    def tv_details(self, tmdb_id: int) -> TmdbDetails:
        data = self._get(f"/tv/{tmdb_id}", {"append_to_response": "keywords"})
        # TV keywords nest under "results", not "keywords" (TMDB
        # inconsistency between the movie and TV APIs) — normalized here
        # so callers never need to know which endpoint they came from.
        keywords = [k["name"] for k in data.get("keywords", {}).get("results", [])]
        return TmdbDetails(
            tmdb_id=data["id"],
            title=data.get("name") or data.get("original_name") or "",
            synopsis=data.get("overview") or None,
            genres=[g["name"] for g in data.get("genres", [])],
            keywords=keywords,
            rating=data.get("vote_average"),
            runtime=None,  # TV runtime is per-episode-ish and unused by the content model
            original_language=data.get("original_language"),
            poster_path=data.get("poster_path"),
            backdrop_path=data.get("backdrop_path"),
        )

    def tv_episode_details(self, tmdb_id: int, season_number: int, episode_number: int) -> Optional[dict]:
        """Episode-level title/synopsis/still (Section 2 Episode fields).
        Returns None (rather than raising) on a 404 — a season/episode
        that doesn't exist on TMDB (specials, mismatched numbering) just
        means that one episode stays unenriched, not a hard failure."""
        try:
            data = self._get(f"/tv/{tmdb_id}/season/{season_number}/episode/{episode_number}", {})
        except TmdbError as exc:
            logger.info("No TMDB episode data for S%02dE%02d: %s", season_number, episode_number, exc)
            return None
        return {
            "title": data.get("name") or None,
            "synopsis": data.get("overview") or None,
            "still_path": data.get("still_path"),
        }

    # -- transport ------------------------------------------------------------

    def _get(self, path: str, params: dict) -> dict:
        merged = {**params, "api_key": self._api_key}
        # Single retry layer (the session's HTTPAdapter has connect/read
        # retries disabled — see build_retrying_session — specifically to
        # avoid stacking two retry loops on top of each other, which was
        # silently taking several minutes per flaky title with zero
        # visible output). Printed, not just logged, since nothing in
        # this project configures logging output by default.
        last_exc: Optional[requests.RequestException] = None
        for attempt in range(1, 4):
            try:
                if _HAVE_CURL_CFFI and isinstance(self._session, cffi_requests.Session):
                    resp = self._session.get(f"{BASE_URL}{path}", params=merged, timeout=self._timeout_s)
                else:
                    resp = self._session.get(f"{BASE_URL}{path}", params=merged, timeout=self._timeout)
                break
            except Exception as exc:  # noqa: BLE001 — curl_cffi and requests raise different exception
                                        # hierarchies; both mean "this attempt failed," treated identically.
                last_exc = exc
                # A reset connection can leave a half-dead socket sitting
                # in the pool that the *next* request tries to reuse and
                # then hangs on. For requests.Session, close()-then-reuse
                # is fine — it just drops pooled connections and the
                # session keeps working. curl_cffi's Session.close() is
                # terminal: every request after it fails instantly with
                # "Session is closed," which cascaded into every title
                # failing outright the first time this shipped. So when
                # we own the session, replace it outright on any failure
                # instead of trying to close-and-reuse it — works
                # correctly for both backends. A caller-injected session
                # (tests) is left completely alone.
                if self._owns_session:
                    try:
                        self._session.close()
                    except Exception:  # noqa: BLE001 — best-effort cleanup only
                        pass
                    self._session = build_retrying_session()
                if attempt < 3:
                    wait_s = 1.0 * attempt
                    print(
                        f"[tmdb] Request to {path} failed (attempt {attempt}/3): {exc}. "
                        f"Retrying in {wait_s:.0f}s...",
                        file=sys.stderr,
                    )
                    time.sleep(wait_s)
                else:
                    print(f"[tmdb] Request to {path} failed after 3 attempts: {exc}", file=sys.stderr)
        else:
            raise TmdbError(f"TMDB request failed for {path} after 3 attempts: {last_exc}") from last_exc

        if resp.status_code == 404:
            raise TmdbError(f"TMDB 404 for {path}")
        if not resp.ok:
            raise TmdbError(f"TMDB {resp.status_code} for {path}: {resp.text[:200]}")
        try:
            return resp.json()
        except ValueError as exc:
            raise TmdbError(f"TMDB returned non-JSON for {path}: {exc}") from exc


def build_retrying_session():
    """Prefers curl_cffi (real libcurl, can impersonate a genuine Chrome
    TLS fingerprint) over plain `requests` whenever it's installed —
    Cloudflare-backed APIs like TMDB can fingerprint the TLS handshake
    itself and silently reset connections from Python's OpenSSL-based
    `requests`/urllib3 stack while letting `curl.exe`/browser traffic to
    the exact same host straight through. No header, retry, or timeout
    change can work around that, since the block happens during the TLS
    handshake, before any HTTP header is ever sent.

    Falls back to plain `requests` (with a browser User-Agent and 429/5xx
    retries — helps with ordinary rate limiting, just not TLS
    fingerprinting) when curl_cffi isn't installed, e.g. on a machine
    where TMDB isn't fingerprinting at all and plain requests works fine
    as-is."""
    if _HAVE_CURL_CFFI:
        session = cffi_requests.Session(impersonate="chrome124")
        session.headers.update({"Accept": "application/json"})
        return session

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
            # No keep-alive: a fresh TCP+TLS handshake per request costs a
            # little latency but completely avoids ever handing a request
            # to a connection the server (or something in between) has
            # already half-killed — which is what was causing the
            # un-interruptible hangs.
            "Connection": "close",
        }
    )
    retry = Retry(
        total=2,
        connect=0,  # connection errors handled by TmdbClient._get's own retry, not here
        read=0,     # same
        status=2,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _parse_year(date_str: Optional[str]) -> Optional[int]:
    if not date_str or len(date_str) < 4:
        return None
    try:
        return int(date_str[:4])
    except ValueError:
        return None