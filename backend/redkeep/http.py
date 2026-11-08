"""A small HTTP layer shared by the data clients.

* Retries connection errors, 429 and 5xx with exponential backoff, honouring
  ``Retry-After``.
* Spaces out requests per host, to stay inside public rate limits.
* Caches responses on disk, keyed by method, URL, parameters and body, with a
  fetch timestamp. The cache doubles as the point-in-time record behind every
  snapshot. Auth headers are never part of the key or the stored file.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "TheRedKeep/0.1 (research prototype; read-only)"
RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class HttpError(RuntimeError):
    def __init__(self, url: str, status: int, body: str):
        super().__init__(f"{status} from {url}: {body[:300]}")
        self.url, self.status, self.body = url, status, body


class Http:
    def __init__(
        self,
        *,
        cache_dir: Path | None = None,
        min_interval: Mapping[str, float] | None = None,
        timeout: float = 30.0,
        max_retries: int = 4,
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
    ):
        self.cache_dir = cache_dir
        self.min_interval = dict(min_interval or {})
        self.max_retries = max_retries
        self._last: dict[str, float] = {}
        self._sleep = sleep
        self.client = httpx.Client(
            timeout=timeout, headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            transport=transport, follow_redirects=True,
        )

    # -- public -------------------------------------------------------------

    def get_json(self, url: str, params: Mapping[str, Any] | None = None, *, headers=None, ttl: float | None = 0) -> Any:
        return json.loads(self._request("GET", url, params=params, headers=headers, ttl=ttl))

    def get_text(self, url: str, params: Mapping[str, Any] | None = None, *, headers=None, ttl: float | None = 0) -> str:
        return self._request("GET", url, params=params, headers=headers, ttl=ttl)

    def post_json(self, url: str, body: Any, *, headers=None, ttl: float | None = 0) -> Any:
        return json.loads(self._request("POST", url, json_body=body, headers=headers, ttl=ttl))

    def close(self) -> None:
        self.client.close()

    # -- internals ----------------------------------------------------------

    def _key(self, method: str, url: str, params, json_body) -> str:
        raw = json.dumps([method, url, sorted((params or {}).items()), json_body], sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def _cached(self, key: str, ttl: float | None) -> str | None:
        """ttl=0: never read the cache; ttl=None: any age is fine."""
        if not self.cache_dir or ttl == 0:
            return None
        path = self.cache_dir / f"{key}.json"
        if not path.exists():
            return None
        rec = json.loads(path.read_text())
        if ttl is not None and time.time() - rec["fetched_at"] > ttl:
            return None
        return rec["body"]

    def _store(self, key: str, method: str, url: str, params, body: str) -> None:
        if not self.cache_dir:
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        rec = {"fetched_at": time.time(), "method": method, "url": url, "params": params, "body": body}
        (self.cache_dir / f"{key}.json").write_text(json.dumps(rec, default=str))

    def _pace(self, url: str) -> None:
        host = urlsplit(url).hostname or ""
        gap = self.min_interval.get(host, 0.0)
        if gap:
            wait = self._last.get(host, 0.0) + gap - time.monotonic()
            if wait > 0:
                self._sleep(wait)
            self._last[host] = time.monotonic()

    def _request(self, method, url, *, params=None, json_body=None, headers=None, ttl: float | None = 0) -> str:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        key = self._key(method, url, params, json_body)
        hit = self._cached(key, ttl)
        if hit is not None:
            return hit
        delay = 1.0
        for attempt in range(self.max_retries + 1):
            self._pace(url)
            try:
                r = self.client.request(method, url, params=params, json=json_body, headers=headers)
            except httpx.TransportError as e:
                if attempt == self.max_retries:
                    raise
                log.warning("%s %s failed (%s); retrying in %.0fs", method, url, e, delay)
                self._sleep(delay)
                delay *= 2
                continue
            if r.status_code in RETRY_STATUS and attempt < self.max_retries:
                wait = float(r.headers.get("retry-after") or delay)
                log.warning("%s %s returned %s; retrying in %.0fs", method, url, r.status_code, wait)
                self._sleep(min(wait, 60.0))
                delay *= 2
                continue
            if r.status_code >= 400:
                raise HttpError(str(r.url), r.status_code, r.text)
            self._store(key, method, url, params, r.text)
            return r.text
        raise AssertionError("unreachable")
