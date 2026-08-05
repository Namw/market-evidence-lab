from __future__ import annotations

import time

import httpx
from django.conf import settings

from apps.collection.source_network import source_proxy_url


RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class ResilientHttpClient:
    def __init__(self, *, source_key="market_funds", transport=None, sleep=time.sleep):
        timeout = httpx.Timeout(
            settings.MARKET_FUNDS_READ_TIMEOUT_SECONDS,
            connect=settings.MARKET_FUNDS_CONNECT_TIMEOUT_SECONDS,
        )
        self._client = httpx.Client(
            headers={"User-Agent": settings.MARKET_FUNDS_USER_AGENT},
            timeout=timeout,
            follow_redirects=True,
            proxy=source_proxy_url(source_key) or None,
            trust_env=False,
            transport=transport,
        )
        self._sleep = sleep
        self.request_count = 0
        self.received_count = 0
        self.skipped_count = 0

    def get(self, url: str) -> httpx.Response:
        max_retries = max(0, min(settings.MARKET_FUNDS_MAX_RETRIES, 5))
        for attempt in range(max_retries + 1):
            self.request_count += 1
            try:
                response = self._client.get(url)
            except (httpx.ConnectError, httpx.ReadTimeout):
                if attempt >= max_retries:
                    raise
                self._sleep(min(0.2 * (2**attempt), 1.0))
                continue
            if response.status_code not in RETRYABLE_STATUS_CODES:
                response.raise_for_status()
                self.received_count += 1
                return response
            if attempt >= max_retries:
                response.raise_for_status()
            self._sleep(min(0.2 * (2**attempt), 1.0))
        raise RuntimeError("bounded retry loop exhausted")

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
