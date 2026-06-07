"""Tests de get_property_details — extraction des attributs du side-menu-left.

Le parsing utilise une regex sur la balise <side-menu-left> (les custom
elements ne sont pas parsés correctement par BS4). On vérifie :
- retour None si la page n'est pas 200,
- retour None si la balise est absente,
- retour None si la clé `cle` est absente,
- extraction correcte de tous les attributs.
"""

from __future__ import annotations

import httpx
import pytest
import respx

import download_syndic as ds


PROPERTY_URL = "https://extranet2.ics.fr/V5/documents-syndic-1234-5678.html"


def _side_menu_attrs(**overrides: str) -> str:
    base = {
        "cle": "ABC12345",
        "login": "ics06@ics.fr",
        "pwd": "ics",
        "cabinet": "CAB1",
        "imme": "1234",
        "copro": "5678",
    }
    base.update(overrides)
    attrs = " ".join(f'{k}="{v}"' for k, v in base.items())
    return f"<side-menu-left {attrs}></side-menu-left>"


class TestGetPropertyDetails:
    """Tests de get_property_details."""

    async def test_extraction_complete(self) -> None:
        html = f"<html><body>{_side_menu_attrs()}</body></html>"
        async with httpx.AsyncClient() as client:
            with respx.mock(assert_all_called=False) as mock:
                mock.get(PROPERTY_URL).mock(return_value=httpx.Response(200, text=html))
                result = await ds.get_property_details(client, PROPERTY_URL)
        assert result is not None
        assert result["cle"] == "ABC12345"
        assert result["ics_login"] == "ics06@ics.fr"
        assert result["ics_pwd"] == "ics"
        assert result["cabinet"] == "CAB1"
        assert result["imme"] == "1234"
        assert result["copro"] == "5678"

    async def test_status_non_200(self) -> None:
        async with httpx.AsyncClient() as client:
            with respx.mock(assert_all_called=False) as mock:
                mock.get(PROPERTY_URL).mock(return_value=httpx.Response(500))
                result = await ds.get_property_details(client, PROPERTY_URL)
        assert result is None

    async def test_balise_side_menu_absente(self) -> None:
        html = "<html><body>Aucun side-menu-left ici</body></html>"
        async with httpx.AsyncClient() as client:
            with respx.mock(assert_all_called=False) as mock:
                mock.get(PROPERTY_URL).mock(return_value=httpx.Response(200, text=html))
                result = await ds.get_property_details(client, PROPERTY_URL)
        assert result is None

    async def test_cle_absente(self) -> None:
        attrs = " ".join(f'{k}="{v}"' for k, v in {
            "login": "x", "pwd": "y", "cabinet": "z", "imme": "1", "copro": "2",
        }.items())
        html = f"<html><body><side-menu-left {attrs}></side-menu-left></body></html>"
        async with httpx.AsyncClient() as client:
            with respx.mock(assert_all_called=False) as mock:
                mock.get(PROPERTY_URL).mock(return_value=httpx.Response(200, text=html))
                result = await ds.get_property_details(client, PROPERTY_URL)
        assert result is None

    async def test_attributs_avec_tirets(self) -> None:
        # Les noms d'attributs HTML peuvent contenir des tirets (data-*, etc.).
        html = (
            '<html><body>'
            '<side-menu-left cle="ABC" data-foo="bar" imme="1">'
            '</side-menu-left></body></html>'
        )
        async with httpx.AsyncClient() as client:
            with respx.mock(assert_all_called=False) as mock:
                mock.get(PROPERTY_URL).mock(return_value=httpx.Response(200, text=html))
                result = await ds.get_property_details(client, PROPERTY_URL)
        assert result is not None
        assert result["cle"] == "ABC"
        assert result["imme"] == "1"

    async def test_suivi_des_redirections(self) -> None:
        # Le client reçoit l'URL avec follow_redirects=True — on vérifie juste
        # que l'appel HTTP est bien fait (peu importe le statut final).
        html = f"<html><body>{_side_menu_attrs()}</body></html>"
        async with httpx.AsyncClient() as client:
            with respx.mock(assert_all_called=False) as mock:
                route = mock.get(PROPERTY_URL).mock(
                    return_value=httpx.Response(200, text=html)
                )
                result = await ds.get_property_details(client, PROPERTY_URL)
        assert result is not None
        assert route.called
