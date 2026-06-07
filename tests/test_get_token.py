"""Tests de get_token — récupération du token via le webservice idservice.

Le webservice idservice inverse login/pwd par rapport au side-menu-left :
- side-menu-left : login="ics06@ics.fr" pwd="ics"
- idservice      : login=ics mdp=ics06@ics.fr

Cette inversion est cruciale ; une régression casserait silencieusement
l'authentification sans lever d'erreur visible.
"""

from __future__ import annotations

import httpx
import pytest
import respx

import download_syndic as ds


PROPERTY = {
    "cle": "ABC12345",
    "ics_login": "ics06@ics.fr",  # côté side-menu-left
    "ics_pwd": "ics",  # côté side-menu-left
    "cabinet": "CAB1",
    "imme": "1234",
    "copro": "5678",
}


class TestGetToken:
    """Tests de get_token."""

    async def test_succes_token_dans_cle_token(self) -> None:
        async with httpx.AsyncClient() as client:
            with respx.mock(assert_all_called=False) as mock:
                mock.get(__import__("re").compile(r"idservice.*")).mock(
                    return_value=httpx.Response(200, json={"success": True, "token": "TOK123"})
                )
                token = await ds.get_token(client, PROPERTY)
        assert token == "TOK123"

    async def test_inversion_login_pwd(self) -> None:
        # La requête doit utiliser ics_pwd comme login et ics_login comme mdp.
        async with httpx.AsyncClient() as client:
            with respx.mock(assert_all_called=False) as mock:
                route = mock.get(__import__("re").compile(r"idservice.*")).mock(
                    return_value=httpx.Response(200, json={"success": True, "token": "X"})
                )
                await ds.get_token(client, PROPERTY)
        # Le request.url est un URLSearchParams-encoded string.
        request_url = str(route.calls.last.request.url)
        assert "login=ics" in request_url  # ics_pwd du side-menu
        assert "mdp=ics06%40ics.fr" in request_url or "mdp=ics06@ics.fr" in request_url

    async def test_token_dans_cle_result(self) -> None:
        async with httpx.AsyncClient() as client:
            with respx.mock(assert_all_called=False) as mock:
                mock.get(__import__("re").compile(r"idservice.*")).mock(
                    return_value=httpx.Response(200, json={"success": True, "result": "RES-TOK"})
                )
                token = await ds.get_token(client, PROPERTY)
        assert token == "RES-TOK"

    async def test_token_dans_cle_cle(self) -> None:
        async with httpx.AsyncClient() as client:
            with respx.mock(assert_all_called=False) as mock:
                mock.get(__import__("re").compile(r"idservice.*")).mock(
                    return_value=httpx.Response(200, json={"success": True, "cle": "CLE-TOK"})
                )
                token = await ds.get_token(client, PROPERTY)
        assert token == "CLE-TOK"

    async def test_token_dans_cle_id(self) -> None:
        async with httpx.AsyncClient() as client:
            with respx.mock(assert_all_called=False) as mock:
                mock.get(__import__("re").compile(r"idservice.*")).mock(
                    return_value=httpx.Response(200, json={"success": True, "id": "ID-TOK"})
                )
                token = await ds.get_token(client, PROPERTY)
        assert token == "ID-TOK"

    async def test_http_non_200(self) -> None:
        async with httpx.AsyncClient() as client:
            with respx.mock(assert_all_called=False) as mock:
                mock.get(__import__("re").compile(r"idservice.*")).mock(
                    return_value=httpx.Response(500)
                )
                token = await ds.get_token(client, PROPERTY)
        assert token is None

    async def test_success_false(self) -> None:
        async with httpx.AsyncClient() as client:
            with respx.mock(assert_all_called=False) as mock:
                mock.get(__import__("re").compile(r"idservice.*")).mock(
                    return_value=httpx.Response(200, json={"success": False, "erreur": "nope"})
                )
                token = await ds.get_token(client, PROPERTY)
        assert token is None

    async def test_reponse_sans_token(self) -> None:
        async with httpx.AsyncClient() as client:
            with respx.mock(assert_all_called=False) as mock:
                mock.get(__import__("re").compile(r"idservice.*")).mock(
                    return_value=httpx.Response(200, json={"success": True})
                )
                token = await ds.get_token(client, PROPERTY)
        assert token is None

    async def test_cle_portefeuille_et_operation_dans_l_url(self) -> None:
        async with httpx.AsyncClient() as client:
            with respx.mock(assert_all_called=False) as mock:
                route = mock.get(__import__("re").compile(r"idservice.*")).mock(
                    return_value=httpx.Response(200, json={"success": True, "token": "X"})
                )
                await ds.get_token(client, PROPERTY)
        url = str(route.calls.last.request.url)
        assert "clePortefeuille=ABC12345" in url
        assert "nomProduit=Ged" in url
        assert "operation=get" in url
        assert "retour=json" in url
