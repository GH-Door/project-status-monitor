"""Run pytest with real httpx transports blocked; fake API transports still work.

Usage from the repository root: uv run python tests/run_offline.py tests -q
"""

import sys

import httpx
import pytest


class NoRemoteCalls:
    @pytest.fixture(autouse=True)
    def block_external_http(self, monkeypatch):
        def deny(*args, **kwargs):
            raise AssertionError("Real HTTP calls are disabled for this regression run")

        async def deny_async(*args, **kwargs):
            raise AssertionError("Real HTTP calls are disabled for this regression run")

        monkeypatch.setattr(httpx.HTTPTransport, "handle_request", deny)
        monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", deny_async)


if __name__ == "__main__":
    raise SystemExit(pytest.main(sys.argv[1:] or ["tests"], plugins=[NoRemoteCalls()]))
