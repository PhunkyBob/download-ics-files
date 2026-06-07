"""Tests de get_entity_url_with_fallback — discrimination de la vue API.

L'API renvoie responseCode="500" quand subType=COPROPRIETAIRE est utilisé
pour la vue "DOCUMENTS DE L'IMMEUBLE". Cette fonction :
- essaie l'URL VOS (avec subType) en premier ;
- si 500, bascule sur l'URL IMMEUBLE (sans subType).
"""

from __future__ import annotations

import httpx
import pytest
import respx

import download_syndic as ds


VOS_URL = "https://extranet2.ics.fr/vos?id=1&subType=COPROPRIETAIRE"
IMM_URL = "https://extranet2.ics.fr/imm?id=1"


class TestGetEntityUrlWithFallback:
    """Tests de get_entity_url_with_fallback."""

    async def test_vos_url_marche_retourne_vue_vos(self) -> None:
        async with httpx.AsyncClient() as client:
            with respx.mock(assert_all_called=False) as mock:
                mock.get(VOS_URL).mock(
                    return_value=httpx.Response(200, json={"responseCode": "200", "payload": {}})
                )
                url, vue = await ds.get_entity_url_with_fallback(client, VOS_URL, IMM_URL, label="prop")
        assert url == VOS_URL
        assert vue == "VOS"

    async def test_vos_url_renvoie_500_bascule_sur_imm_url(self) -> None:
        async with httpx.AsyncClient() as client:
            with respx.mock(assert_all_called=False) as mock:
                mock.get(VOS_URL).mock(
                    return_value=httpx.Response(200, json={"responseCode": "500", "msg": "oops"})
                )
                mock.get(IMM_URL).mock(
                    return_value=httpx.Response(200, json={"responseCode": "200", "payload": {}})
                )
                url, vue = await ds.get_entity_url_with_fallback(client, VOS_URL, IMM_URL, label="prop")
        assert url == IMM_URL
        assert vue == "IMMEUBLE"

    async def test_json_invalide_retourne_vos_sans_lever(self) -> None:
        # Une réponse non-JSON doit être tolérée (équivalent à responseCode != "500").
        async with httpx.AsyncClient() as client:
            with respx.mock(assert_all_called=False) as mock:
                mock.get(VOS_URL).mock(return_value=httpx.Response(200, text="not json"))
                url, vue = await ds.get_entity_url_with_fallback(client, VOS_URL, IMM_URL)
        assert url == VOS_URL
        assert vue == "VOS"

    async def test_label_passe_dans_les_logs(self, capsys) -> None:
        async with httpx.AsyncClient() as client:
            with respx.mock(assert_all_called=False) as mock:
                mock.get(VOS_URL).mock(
                    return_value=httpx.Response(200, json={"responseCode": "500"})
                )
                mock.get(IMM_URL).mock(
                    return_value=httpx.Response(200, json={"responseCode": "200"})
                )
                await ds.get_entity_url_with_fallback(client, VOS_URL, IMM_URL, label="MonImmeuble")
        captured = capsys.readouterr()
        assert "MonImmeuble" in captured.out

    async def test_sans_label_pas_de_log(self, capsys) -> None:
        async with httpx.AsyncClient() as client:
            with respx.mock(assert_all_called=False) as mock:
                mock.get(VOS_URL).mock(
                    return_value=httpx.Response(200, json={"responseCode": "500"})
                )
                mock.get(IMM_URL).mock(
                    return_value=httpx.Response(200, json={"responseCode": "200"})
                )
                await ds.get_entity_url_with_fallback(client, VOS_URL, IMM_URL)
        # Sans label, _log est un no-op.
        captured = capsys.readouterr()
        assert "🔁" not in captured.out
