"""Thin, polite, cached client for the public Fantasy Premier League API.

No authentication is required for any endpoint used here. Responses for
*finished* gameweeks never change, so they are cached on disk and never
re-fetched - that keeps a 20-manager league to a handful of requests per run
instead of ~800.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

BASE = "https://fantasy.premierleague.com/api"

# FPL blocks the default python-urllib agent.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"


class FPLError(RuntimeError):
    pass


class FPLClient:
    def __init__(self, cache_dir: Path | None = None, delay: float = 0.25,
                 offline_fixture_dir: str | None = None):
        self.cache_dir = cache_dir or CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.delay = delay
        self.requests_made = 0
        self.cache_hits = 0
        # Used by the offline test harness: read canned responses instead of HTTP.
        self.offline_fixture_dir = offline_fixture_dir or os.environ.get("FPL_OFFLINE_DIR")

    # ---------------------------------------------------------------- fetching

    def _cache_path(self, key: str) -> Path:
        safe = key.replace("/", "_").replace("?", "_").replace("&", "_").replace("=", "-")
        return self.cache_dir / f"{safe}.json"

    def get(self, path: str, cache: bool = False, optional: bool = False) -> Any:
        """GET an API path. `cache=True` means the response is immutable - store
        it forever. `optional=True` means a 404/error returns None instead of
        raising (used for endpoints that only exist later in the season)."""
        # Never touch the on-disk cache in offline/test mode - otherwise mock
        # responses would poison the cache used by real runs.
        if self.offline_fixture_dir:
            return self._offline_get(path, optional)

        cache_file = self._cache_path(path)
        if cache and cache_file.exists():
            self.cache_hits += 1
            try:
                return json.loads(cache_file.read_text())
            except json.JSONDecodeError:
                cache_file.unlink(missing_ok=True)

        data = self._http_get(path, optional)

        if data is not None and cache:
            cache_file.write_text(json.dumps(data, separators=(",", ":")))
        return data

    def _offline_get(self, path: str, optional: bool) -> Any:
        p = Path(self.offline_fixture_dir) / (
            path.strip("/").replace("/", "_").replace("?", "_")
            .replace("&", "_").replace("=", "-") + ".json"
        )
        if not p.exists():
            if optional:
                return None
            raise FPLError(f"offline fixture missing: {p}")
        return json.loads(p.read_text())

    def _http_get(self, path: str, optional: bool) -> Any:
        url = f"{BASE}/{path.lstrip('/')}"
        last_err: Exception | None = None
        for attempt in range(4):
            try:
                req = urllib.request.Request(url, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=30) as resp:
                    self.requests_made += 1
                    time.sleep(self.delay)
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                # 404 on an endpoint that does not exist yet is expected, not a failure.
                if e.code in (404, 403) and optional:
                    return None
                last_err = e
                if e.code in (429, 500, 502, 503, 504):
                    time.sleep(2 ** attempt)
                    continue
                if optional:
                    return None
                raise FPLError(f"HTTP {e.code} for {url}") from e
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                last_err = e
                time.sleep(2 ** attempt)
        if optional:
            return None
        raise FPLError(f"failed after retries: {url} ({last_err})")

    # ------------------------------------------------------------- endpoints

    def bootstrap(self) -> dict:
        return self.get("bootstrap-static/")

    def fixtures(self) -> list:
        return self.get("fixtures/") or []

    def league_standings(self, league_id: int) -> dict:
        """Classic league standings, following pagination."""
        page = 1
        merged: dict | None = None
        while True:
            data = self.get(f"leagues-classic/{league_id}/standings/?page_standings={page}")
            if merged is None:
                merged = data
            else:
                merged["standings"]["results"].extend(data["standings"]["results"])
            if not data.get("standings", {}).get("has_next"):
                break
            page += 1
            if page > 40:
                break
        return merged or {}

    def entry(self, entry_id: int) -> dict:
        return self.get(f"entry/{entry_id}/", optional=True) or {}

    def entry_history(self, entry_id: int) -> dict:
        return self.get(f"entry/{entry_id}/history/", optional=True) or {}

    def entry_picks(self, entry_id: int, event: int, finished: bool) -> dict | None:
        return self.get(f"entry/{entry_id}/event/{event}/picks/",
                        cache=finished, optional=True)

    def live(self, event: int, finished: bool) -> dict | None:
        return self.get(f"event/{event}/live/", cache=finished, optional=True)

    def league_cup(self, league_id: int) -> dict | None:
        """Mini-league cup. FPL has moved this endpoint around between seasons,
        so try the known shapes and return the first that looks like a cup."""
        candidates = [
            f"league/{league_id}/cup/?page_new_entries=1&page_standings=1",
            f"league/{league_id}/cup/",
            f"leagues-classic/{league_id}/cup/",
        ]
        for path in candidates:
            data = self.get(path, optional=True)
            if isinstance(data, dict) and ("matches" in data or "cup_league" in data
                                           or "status" in data):
                data["_endpoint"] = path
                return data
        return None

    def entry_cup(self, entry_id: int) -> dict | None:
        """Per-manager cup matches - the reliable fallback for building a bracket."""
        return self.get(f"entry/{entry_id}/cup/", optional=True)
