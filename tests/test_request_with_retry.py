"""Tests de request_with_retry — backoff exponentiel sur timeouts.

Cette fonction est cruciale sous Linux où les timeouts sont plus fréquents.
Elle doit : (1) retourner la réponse au premier essai réussi, (2) réessayer
avec un délai croissant sur timeout, (3) lever la dernière exception si tous
les essais échouent.
"""

from __future__ import annotations

import asyncio
import httpx
import pytest
import respx

import download_syndic as ds


URL = "https://example.test/api"


@pytest.fixture
def low_retry() -> None:
    # Réduit le nombre de retries à 2 et le timeout pour ne pas attendre.
    # Note : les timeouts sont appliqués par httpx, on contrôle le nombre de
    # tentatives via HTTP_MAX_RETRIES.
    ds.HTTP_MAX_RETRIES = 2


@pytest.fixture
def fast_sleep(monkeypatch: pytest.MonkeyPatch):
    """Neutralise asyncio.sleep pour ne pas attendre pendant les retries."""
    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return sleeps


class TestRequestWithRetry:
    """Tests de request_with_retry."""

    async def test_succes_du_premier_coup(self, low_retry: None) -> None:
        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://example.test") as mock:
                mock.get("/api").mock(return_value=httpx.Response(200, json={"ok": True}))

                resp = await ds.request_with_retry(client, "GET", URL)
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    async def test_retry_apres_un_timeout_puis_succes(self, fast_sleep: list[float]) -> None:
        ds.HTTP_MAX_RETRIES = 3

        class CountingTransport(httpx.AsyncBaseTransport):
            def __init__(self) -> None:
                self.calls = 0

            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                self.calls += 1
                if self.calls == 1:
                    raise httpx.ReadTimeout("simulated")
                return httpx.Response(200, json={"recovered": True})

        transport = CountingTransport()
        async with httpx.AsyncClient(transport=transport) as client:
            resp = await ds.request_with_retry(client, "GET", URL)
        assert resp.status_code == 200
        assert resp.json() == {"recovered": True}
        assert transport.calls == 2
        # Le backoff est 2^(attempt+1) — premier retry attend 2s.
        assert fast_sleep == [2]

    async def test_leve_apres_echec_de_toutes_les_tentatives(self, fast_sleep: list[float]) -> None:
        ds.HTTP_MAX_RETRIES = 3

        class AlwaysTimeoutTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                raise httpx.ReadTimeout("always fails")

        async with httpx.AsyncClient(transport=AlwaysTimeoutTransport()) as client:
            with pytest.raises(httpx.ReadTimeout):
                await ds.request_with_retry(client, "GET", URL)
        # 3 tentatives → 2 sleeps entre elles (backoff 2s puis 4s).
        assert fast_sleep == [2, 4]

    async def test_passe_les_kwargs_a_httpx(self) -> None:
        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://example.test") as mock:
                route = mock.post("/api").mock(return_value=httpx.Response(201, json={"created": True}))

                resp = await ds.request_with_retry(
                    client, "POST", URL, json={"foo": "bar"}, headers={"X-Test": "1"}
                )
        assert resp.status_code == 201
        assert route.called
        assert route.calls.last.request.method == "POST"

    async def test_retry_sur_connect_timeout(self, fast_sleep: list[float]) -> None:
        ds.HTTP_MAX_RETRIES = 2

        class ConnectFailTransport(httpx.AsyncBaseTransport):
            def __init__(self) -> None:
                self.calls = 0

            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                self.calls += 1
                if self.calls < 2:
                    raise httpx.ConnectTimeout("connect")
                return httpx.Response(200)

        transport = ConnectFailTransport()
        async with httpx.AsyncClient(transport=transport) as client:
            resp = await ds.request_with_retry(client, "GET", URL)
        assert resp.status_code == 200
        assert transport.calls == 2

    async def test_label_est_utilise_dans_les_logs(self, fast_sleep: list[float], capsys) -> None:
        ds.HTTP_MAX_RETRIES = 2

        class AlwaysFailTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                raise httpx.ReadTimeout("x")

        async with httpx.AsyncClient(transport=AlwaysFailTransport()) as client:
            with pytest.raises(httpx.ReadTimeout):
                await ds.request_with_retry(client, "GET", URL, label="Mon-label")
        captured = capsys.readouterr()
        assert "Mon-label" in captured.out
