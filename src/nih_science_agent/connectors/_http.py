"""Shared HTTP plumbing for connectors: a small cached GET/POST client.

Connectors (PubMed, iCite, …) subclass :class:`CachedClient` to get a managed
``httpx.Client`` plus deterministic on-disk response caching keyed by the full
request. RePORTER predates this and keeps its own copy; new connectors share
this base so caching/lifecycle behave identically everywhere.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0
USER_AGENT = "nih-science-agent/0.1 (+https://github.com/nih-science-agent)"
_RETRY_STATUS = {429, 500, 502, 503, 504}


def _with_retry(call: Callable[[], httpx.Response], attempts: int = 3) -> httpx.Response:
    """Call ``call`` with retries on transient errors (5xx/429, timeouts).

    Public APIs (notably NCBI E-utilities) return intermittent 500s; a couple of
    short retries makes batch pipelines reliable without masking real failures.
    """
    last: Exception | None = None
    for i in range(attempts):
        try:
            resp = call()
            if resp.status_code in _RETRY_STATUS and i < attempts - 1:
                time.sleep(0.5 * (i + 1))
                continue
            return resp
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            last = exc
            if i < attempts - 1:
                time.sleep(0.5 * (i + 1))
    if last:
        raise last
    return call()


class CachedClient:
    """Base class providing a cached ``httpx.Client`` keyed by request shape."""

    def __init__(
        self,
        cache_subdir: str,
        client: httpx.Client | None = None,
        cache_dir: Path | None = None,
        use_cache: bool = True,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        from nih_science_agent.config import get_settings

        settings = get_settings()
        self.cache_dir = cache_dir or (settings.cache_dir / cache_subdir)
        self.use_cache = use_cache
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None
        if use_cache:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    # -- lifecycle -------------------------------------------------------- #

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout, headers={"User-Agent": USER_AGENT})
        return self._client

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    def __enter__(self):  # noqa: ANN204 - returns self subtype
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- caching ---------------------------------------------------------- #

    def _cache_path(self, key_parts: dict[str, Any]) -> Path:
        key = hashlib.sha256(
            json.dumps(key_parts, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:24]
        return self.cache_dir / f"{key}.json"

    def _get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        params = params or {}
        cache_key = {"method": "GET", "url": url, "params": params}
        cache_path = self._cache_path(cache_key)
        if self.use_cache and cache_path.exists():
            logger.debug("cache hit: %s", cache_path.name)
            return json.loads(cache_path.read_text())

        logger.info("GET %s", url)
        resp = _with_retry(lambda: self._http().get(url, params=params))
        resp.raise_for_status()
        data = resp.json()
        if self.use_cache:
            cache_path.write_text(json.dumps(data))
        return data

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET a JSON object endpoint."""
        return self._get(url, params)

    def get_json_list(self, url: str, params: dict[str, Any] | None = None) -> list[Any]:
        """GET a JSON array endpoint (e.g. Socrata), returning a list."""
        data = self._get(url, params)
        return data if isinstance(data, list) else []

    def post_json(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST a JSON body and return the JSON response (cached by url+body)."""
        cache_key = {"method": "POST", "url": url, "body": body}
        cache_path = self._cache_path(cache_key)
        if self.use_cache and cache_path.exists():
            logger.debug("cache hit: %s", cache_path.name)
            return json.loads(cache_path.read_text())

        logger.info("POST %s", url)
        resp = _with_retry(lambda: self._http().post(url, json=body))
        resp.raise_for_status()
        data = resp.json()
        if self.use_cache:
            cache_path.write_text(json.dumps(data))
        return data
