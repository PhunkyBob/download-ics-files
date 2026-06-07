"""Tests d'intégration pour main() — orchestration de bout en bout.

`main()` combine : auth → sélection propriétés → date → action → scan →
collecte par propriété → listing OU téléchargement. On mocke toutes les
entrées (auth, prompts, HTTP) pour vérifier le câblage sans dépendre du
portail réel.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import pytest
import respx

import download_syndic as ds


# ---------- Helpers ----------


def _docs_html(*buildings: tuple[str, str, str, str]) -> str:
    parts = ["<html><body>"]
    for name, imme, copro, doc_type in buildings:
        parts.append(
            f'<div class="row">'
            f'<p class="main-text">{name}</p>'
            f'<a href="documents-syndic-{imme}-{copro}.html">{doc_type}</a>'
            f"</div>"
        )
    parts.append("</body></html>")
    return "".join(parts)


def _side_menu_attrs(cle: str = "ABC", imme: str = "1", copro: str = "1") -> str:
    attrs = " ".join(
        f'{k}="{v}"' for k, v in {
            "cle": cle, "login": "ics06@ics.fr", "pwd": "ics",
            "cabinet": "C", "imme": imme, "copro": copro,
        }.items()
    )
    return f"<side-menu-left {attrs}></side-menu-left>"


def _entity_payload(docs: list[dict]) -> dict:
    return {
        "responseCode": "200",
        "payload": {"sons": [], "docs": docs, "directory": {"nom": "Immeuble"}},
    }


def _file(guid: str, nom: str = "x.pdf", date: str = "2024-01-15") -> dict:
    return {
        "guid": guid,
        "nom": nom,
        "nomGed": nom,
        "extension": ".pdf",
        "emplacement": f"/path/{nom}",
        "dateCreated": date,
    }


def _setup_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOGIN_URL", "https://www.test-syndic.fr/login")
    monkeypatch.setenv("LOGIN", "user@test.fr")
    monkeypatch.setenv("PASSWORD", "secret")
    monkeypatch.setenv("DOWNLOAD_FOLDER", "DL_TMP")


def _stub_full_flow(
    monkeypatch: pytest.MonkeyPatch,
    docs_html: str,
    entity_docs: list[dict],
    action_value: str = "1",  # 1=liste, 2=télécharger
    confirm_value: bool = True,
) -> dict:
    """Stub tous les points d'entrée de main() et retourne un état de capture."""

    state: dict[str, Any] = {}

    # Authenticate
    import respx as _respx

    _respx.routes.clear()  # type: ignore[attr-defined]
    _ = _respx.mock(assert_all_called=False)
    state["respx_mock"] = _

    _respx.routes.clear()  # type: ignore[attr-defined]

    return state


# ---------- Tests ----------


class TestMain:
    """Tests de main()."""

    async def test_authentification_echouee_sort_sans_erreur(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _setup_env(monkeypatch)
        # On mocke la page de login pour qu'elle renvoie 503 → authenticate()
        # retourne None proprement et main() sort sans erreur.
        with respx.mock(assert_all_called=False) as mock:
            mock.get("https://www.test-syndic.fr/login").mock(
                return_value=httpx.Response(503)
            )
            result = await ds.main()
        # Pas d'exception, pas de retour significatif (return implicite).
        assert result is None

    async def test_chemin_avec_liste(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path, tmp_env, capsys
    ) -> None:
        """Chemin nominal : auth OK → 1 propriété → date par défaut → action 1 (lister)."""
        _setup_env(monkeypatch)
        ds.download_folder = str(tmp_path / "dl")
        os.makedirs(ds.download_folder, exist_ok=True)

        # Mock authenticate → on retourne un faux AuthSession.
        fake_session = {
            "phpsessid": "abc",
            "cabinet_groupe": "GRP",
            "client": httpx.AsyncClient(),
            "properties": [
                {
                    "url": "https://extranet2.ics.fr/V5/documents-syndic-1-1.html",
                    "imme": "1",
                    "copro": "1",
                    "building_name": "Immeuble A",
                    "doc_type": "VOS",
                    "label": "Immeuble A — VOS",
                }
            ],
        }
        monkeypatch.setattr(ds, "authenticate", lambda: _async_return(fake_session))

        # select_properties → la seule propriété est retournée directement.
        # (déjà géré en interne : len(properties) == 1).

        # prompt_start_date → "2024-01".
        async def fake_start_date() -> str:
            return "2024-01"

        monkeypatch.setattr(ds, "prompt_start_date", fake_start_date)

        # prompt_action → "1" (lister).
        async def fake_action() -> str:
            return "1"

        monkeypatch.setattr(ds, "prompt_action", fake_action)

        # get_property_details
        async def fake_property_details(client, url):
            return {
                "cle": "CLE", "ics_login": "x", "ics_pwd": "y",
                "cabinet": "C", "imme": "1", "copro": "1",
            }

        monkeypatch.setattr(ds, "get_property_details", fake_property_details)

        # get_token
        async def fake_token(client, details):
            return "TOK"

        monkeypatch.setattr(ds, "get_token", fake_token)

        # get_entity_url_with_fallback → on retourne l'URL VOS directement.
        async def fake_entity(client, vos, imm, label=""):
            return vos, "VOS"

        monkeypatch.setattr(ds, "get_entity_url_with_fallback", fake_entity)

        # HTTP mocké pour la collecte
        entity_url_pattern = __import__("re").compile(r".*extranet2.ics.fr.*")
        with respx.mock(base_url="https://extranet2.ics.fr", assert_all_called=False) as mock:
            mock.get(entity_url_pattern).mock(
                return_value=httpx.Response(
                    200,
                    json=_entity_payload([
                        _file("G1", "a.pdf", "2024-12-01"),
                        _file("G2", "b.pdf", "2024-12-02"),
                    ]),
                )
            )
            await ds.main()

        captured = capsys.readouterr()
        # Le mode liste affiche un résumé.
        assert "Résumé" in captured.out or "nouveau" in captured.out

        # Ferme le faux client pour ne pas leak.
        await fake_session["client"].aclose()

    async def test_action_telecharger_avec_confirmation(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path, tmp_env
    ) -> None:
        """Chemin téléchargement : action 2 → confirmation oui → fichiers sauvés."""
        _setup_env(monkeypatch)
        ds.download_folder = str(tmp_path / "dl")
        os.makedirs(ds.download_folder, exist_ok=True)
        ds.enable_thread_bars = False
        ds.max_concurrent_downloads = 1

        fake_session = {
            "phpsessid": "abc",
            "cabinet_groupe": "GRP",
            "client": httpx.AsyncClient(),
            "properties": [
                {
                    "url": "https://extranet2.ics.fr/V5/documents-syndic-1-1.html",
                    "imme": "1",
                    "copro": "1",
                    "building_name": "Immeuble A",
                    "doc_type": "VOS",
                    "label": "Immeuble A — VOS",
                }
            ],
        }
        monkeypatch.setattr(ds, "authenticate", lambda: _async_return(fake_session))

        async def fake_start_date() -> str:
            return "2024-01"

        monkeypatch.setattr(ds, "prompt_start_date", fake_start_date)

        async def fake_action() -> str:
            return "2"

        monkeypatch.setattr(ds, "prompt_action", fake_action)

        async def fake_confirm(message, default=False):
            return True  # confirmation oui

        monkeypatch.setattr(ds, "_confirm", fake_confirm)

        async def fake_property_details(client, url):
            return {
                "cle": "CLE", "ics_login": "x", "ics_pwd": "y",
                "cabinet": "C", "imme": "1", "copro": "1",
            }

        monkeypatch.setattr(ds, "get_property_details", fake_property_details)

        async def fake_token(client, details):
            return "TOK"

        monkeypatch.setattr(ds, "get_token", fake_token)

        async def fake_entity(client, vos, imm, label=""):
            return vos, "VOS"

        monkeypatch.setattr(ds, "get_entity_url_with_fallback", fake_entity)

        # 1 fichier à télécharger.
        with respx.mock(base_url="https://extranet2.ics.fr", assert_all_called=False) as mock:
            mock.get(__import__("re").compile(r"GetEntityContent")).mock(
                return_value=httpx.Response(
                    200,
                    json=_entity_payload([_file("G1", "a.pdf", "2024-12-01")]),
                )
            )
            mock.get(__import__("re").compile(r"getFileByFTPServlet")).mock(
                return_value=httpx.Response(200, content=b"PDF")
            )
            await ds.main()

        # Le fichier a bien été téléchargé.
        assert "G1" in ds.downloaded_files
        await fake_session["client"].aclose()

    async def test_propriete_ignoree_si_details_manquants(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path, tmp_env, capsys
    ) -> None:
        """Si get_property_details retourne None, on saute la propriété."""
        _setup_env(monkeypatch)
        ds.download_folder = str(tmp_path / "dl")
        os.makedirs(ds.download_folder, exist_ok=True)

        fake_session = {
            "phpsessid": "abc",
            "cabinet_groupe": "GRP",
            "client": httpx.AsyncClient(),
            "properties": [
                {
                    "url": "https://extranet2.ics.fr/V5/documents-syndic-1-1.html",
                    "imme": "1",
                    "copro": "1",
                    "building_name": "A",
                    "doc_type": "VOS",
                    "label": "A",
                }
            ],
        }
        monkeypatch.setattr(ds, "authenticate", lambda: _async_return(fake_session))

        async def fake_start_date() -> str:
            return "2024-01"

        monkeypatch.setattr(ds, "prompt_start_date", fake_start_date)

        async def fake_action() -> str:
            return "1"

        monkeypatch.setattr(ds, "prompt_action", fake_action)

        # get_property_details → None
        async def fake_details(client, url):
            return None

        monkeypatch.setattr(ds, "get_property_details", fake_details)

        await ds.main()

        captured = capsys.readouterr()
        assert "ignorée" in captured.out
        await fake_session["client"].aclose()

    async def test_propriete_ignoree_si_token_manquant(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path, tmp_env, capsys
    ) -> None:
        """Si get_token retourne None, on saute la propriété."""
        _setup_env(monkeypatch)
        ds.download_folder = str(tmp_path / "dl")
        os.makedirs(ds.download_folder, exist_ok=True)

        fake_session = {
            "phpsessid": "abc",
            "cabinet_groupe": "GRP",
            "client": httpx.AsyncClient(),
            "properties": [
                {
                    "url": "https://extranet2.ics.fr/V5/documents-syndic-1-1.html",
                    "imme": "1",
                    "copro": "1",
                    "building_name": "A",
                    "doc_type": "VOS",
                    "label": "A",
                }
            ],
        }
        monkeypatch.setattr(ds, "authenticate", lambda: _async_return(fake_session))

        async def fake_start_date() -> str:
            return "2024-01"

        monkeypatch.setattr(ds, "prompt_start_date", fake_start_date)

        async def fake_action() -> str:
            return "1"

        monkeypatch.setattr(ds, "prompt_action", fake_action)

        async def fake_details(client, url):
            return {
                "cle": "C", "ics_login": "x", "ics_pwd": "y",
                "cabinet": "C", "imme": "1", "copro": "1",
            }

        monkeypatch.setattr(ds, "get_property_details", fake_details)

        async def fake_token(client, details):
            return None  # token KO

        monkeypatch.setattr(ds, "get_token", fake_token)

        await ds.main()

        captured = capsys.readouterr()
        assert "token" in captured.out.lower()
        await fake_session["client"].aclose()

    async def test_pas_de_proprietes_selectionnees_sort(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path, tmp_env
    ) -> None:
        # Cas pathologique : on interrompt select_properties → liste vide.
        _setup_env(monkeypatch)
        ds.download_folder = str(tmp_path / "dl")
        os.makedirs(ds.download_folder, exist_ok=True)

        fake_session = {
            "phpsessid": "abc",
            "cabinet_groupe": "GRP",
            "client": httpx.AsyncClient(),
            "properties": [_prop := {
                "url": "x", "imme": "1", "copro": "1",
                "building_name": "A", "doc_type": "V", "label": "A",
            }, {
                "url": "y", "imme": "2", "copro": "2",
                "building_name": "B", "doc_type": "V", "label": "B",
            }],
        }
        monkeypatch.setattr(ds, "authenticate", lambda: _async_return(fake_session))

        async def fake_select_props(properties):
            return []  # aucune sélection

        monkeypatch.setattr(ds, "select_properties", fake_select_props)

        await ds.main()  # ne doit pas crasher
        await fake_session["client"].aclose()


# ---------- Petit utilitaire async ----------


async def _async_return(value: Any) -> Any:
    """Helper pour monkeypatcher des fonctions qui retournent une valeur fixe."""
    return value
