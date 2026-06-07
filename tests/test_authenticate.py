"""Tests de authenticate — flux de connexion au portail syndic.

Couvre : (1) échec si les variables d'env sont absentes, (2) échec si la
page de login ne renvoie pas 200, (3) échec si le formulaire login_externe
est introuvable, (4) échec sur identifiants incorrects, (5) échec si la
réponse n'est pas une redirection, (6) échec si PHPSESSID est absent,
(7) échec si pas de redirection après login, (8) échec si documents.html
n'est pas accessible, (9) parsing correct de la liste de propriétés.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import pytest
import respx

import download_syndic as ds


LOGIN_URL = "https://www.mon-agence-syndic.fr/mon-compte/coproprietaire"
FORM_ACTION = "https://www.mon-agence-syndic.fr/login_externe_check.php"
REDIRECT_URL = "https://extranet2.ics.fr/V5/initialisation.html"
DOCS_URL = "https://extranet2.ics.fr/V5/documents.html"


def _login_form_html(groupe_value: str = "copro") -> str:
    return f"""
    <html><body>
      <form action="{FORM_ACTION}" method="post">
        <input name="groupe" value="{groupe_value}" />
      </form>
    </body></html>
    """


def _docs_html(*buildings: tuple[str, str, str]) -> str:
    """Construit le HTML de documents.html avec N propriétés.

    Chaque tuple est (building_name, imme, copro, doc_type).
    """
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


def _set_env(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    monkeypatch.setenv("LOGIN_URL", LOGIN_URL)
    monkeypatch.setenv("LOGIN", "user@test.fr")
    monkeypatch.setenv("PASSWORD", "secret")
    for key, val in values.items():
        monkeypatch.setenv(key, val)


class TestAuthenticate:
    """Tests de authenticate."""

    async def test_variables_env_manquantes_retourne_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LOGIN_URL", raising=False)
        monkeypatch.delenv("LOGIN", raising=False)
        monkeypatch.delenv("PASSWORD", raising=False)
        result = await ds.authenticate()
        assert result is None

    async def test_login_page_non_200(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_env(monkeypatch)
        async with httpx.AsyncClient() as client:
            with respx.mock(assert_all_called=False) as mock:
                mock.get(LOGIN_URL).mock(return_value=httpx.Response(503))
                # authenticate crée son propre client — on ne peut pas le
                # mocker via le paramètre. On vérifie juste le code retour.
                # Note : on triche en patchant httpx.AsyncClient pour utiliser respx.
                import respx as _respx
                with _respx.mock(assert_all_called=False) as global_mock:
                    global_mock.get(LOGIN_URL).mock(return_value=httpx.Response(503))
                    result = await ds.authenticate()
        assert result is None

    async def test_formulaire_login_externe_introuvable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_env(monkeypatch)
        html_sans_formulaire = "<html><body>Aucun formulaire ici</body></html>"
        import respx as _respx
        with _respx.mock(assert_all_called=False) as mock:
            mock.get(LOGIN_URL).mock(return_value=httpx.Response(200, text=html_sans_formulaire))
            result = await ds.authenticate()
        assert result is None

    async def test_identifiants_incorrects(self, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
        _set_env(monkeypatch)
        import respx as _respx
        with _respx.mock(assert_all_called=False) as mock:
            mock.get(LOGIN_URL).mock(return_value=httpx.Response(200, text=_login_form_html()))
            mock.post(FORM_ACTION).mock(
                return_value=httpx.Response(200, text="<html>Identification incorrecte</html>")
            )
            result = await ds.authenticate()
        assert result is None
        assert "incorrect" in capsys.readouterr().out.lower()

    async def test_reponse_non_redirection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_env(monkeypatch)
        import respx as _respx
        with _respx.mock(assert_all_called=False) as mock:
            mock.get(LOGIN_URL).mock(return_value=httpx.Response(200, text=_login_form_html()))
            mock.post(FORM_ACTION).mock(return_value=httpx.Response(404))
            result = await ds.authenticate()
        assert result is None

    async def test_phpsessid_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_env(monkeypatch)
        # La réponse de redirection est 302 mais sans cookie PHPSESSID.
        import respx as _respx
        with _respx.mock(assert_all_called=False) as mock:
            mock.get(LOGIN_URL).mock(return_value=httpx.Response(200, text=_login_form_html()))
            mock.post(FORM_ACTION).mock(
                return_value=httpx.Response(302, headers={"location": REDIRECT_URL})
            )
            result = await ds.authenticate()
        assert result is None

    async def test_pas_de_redirection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_env(monkeypatch)
        import respx as _respx
        with _respx.mock(assert_all_called=False) as mock:
            mock.get(LOGIN_URL).mock(return_value=httpx.Response(200, text=_login_form_html()))
            mock.post(FORM_ACTION).mock(
                return_value=httpx.Response(
                    302,
                    headers={
                        "location": "",
                        "set-cookie": "PHPSESSID=abc123; path=/",
                    },
                )
            )
            result = await ds.authenticate()
        assert result is None

    async def test_succes_avec_proprietes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_env(monkeypatch)
        properties_html = _docs_html(
            ("Immeuble A", "1234", "5678", "VOS DOCUMENTS"),
            ("Immeuble B", "9999", "0000", "DOCUMENTS DE L'IMMEUBLE"),
        )

        import respx as _respx
        with _respx.mock(assert_all_called=False) as mock:
            mock.get(LOGIN_URL).mock(return_value=httpx.Response(200, text=_login_form_html()))
            mock.post(FORM_ACTION).mock(
                return_value=httpx.Response(
                    302,
                    headers={
                        "location": REDIRECT_URL,
                        "set-cookie": "PHPSESSID=abc123def456; path=/",
                    },
                )
            )
            mock.get(REDIRECT_URL).mock(
                return_value=httpx.Response(
                    200,
                    headers={"set-cookie": "CABINET_GROUPE=GRP1; path=/"},
                )
            )
            mock.get(DOCS_URL).mock(return_value=httpx.Response(200, text=properties_html))
            result = await ds.authenticate()

        assert result is not None
        assert result["phpsessid"] == "abc123def456"
        assert result["cabinet_groupe"] == "GRP1"
        assert len(result["properties"]) == 2
        # Le client doit être ouvert (l'appelant doit le fermer).
        assert isinstance(result["client"], httpx.AsyncClient)
        await result["client"].aclose()

    async def test_cabinet_groupe_absent_ne_leve_pas(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_env(monkeypatch)
        properties_html = _docs_html(("Immeuble A", "1234", "5678", "VOS DOCUMENTS"))

        import respx as _respx
        with _respx.mock(assert_all_called=False) as mock:
            mock.get(LOGIN_URL).mock(return_value=httpx.Response(200, text=_login_form_html()))
            mock.post(FORM_ACTION).mock(
                return_value=httpx.Response(
                    302,
                    headers={
                        "location": REDIRECT_URL,
                        "set-cookie": "PHPSESSID=abc123; path=/",
                    },
                )
            )
            mock.get(REDIRECT_URL).mock(return_value=httpx.Response(200))  # pas de cookie
            mock.get(DOCS_URL).mock(return_value=httpx.Response(200, text=properties_html))
            result = await ds.authenticate()
        assert result is not None
        assert result["cabinet_groupe"] == ""
        await result["client"].aclose()

    async def test_documents_html_non_200(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_env(monkeypatch)
        import respx as _respx
        with _respx.mock(assert_all_called=False) as mock:
            mock.get(LOGIN_URL).mock(return_value=httpx.Response(200, text=_login_form_html()))
            mock.post(FORM_ACTION).mock(
                return_value=httpx.Response(
                    302,
                    headers={
                        "location": REDIRECT_URL,
                        "set-cookie": "PHPSESSID=abc; path=/",
                    },
                )
            )
            mock.get(REDIRECT_URL).mock(
                return_value=httpx.Response(200, headers={"set-cookie": "CABINET_GROUPE=G; path=/"})
            )
            mock.get(DOCS_URL).mock(return_value=httpx.Response(500))
            result = await ds.authenticate()
        assert result is None

    async def test_parsing_propriete_avec_champs_manquants(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Les liens sans format documents-syndic-{imme}-{copro}.html sont ignorés.
        _set_env(monkeypatch)
        html_invalide = """
        <html><body>
          <div class="row">
            <p class="main-text">Immeuble A</p>
            <a href="documents-syndic-1234-5678.html">VOS DOCUMENTS</a>
            <a href="autre-lien.html">À ignorer</a>
          </div>
        </body></html>
        """
        import respx as _respx
        with _respx.mock(assert_all_called=False) as mock:
            mock.get(LOGIN_URL).mock(return_value=httpx.Response(200, text=_login_form_html()))
            mock.post(FORM_ACTION).mock(
                return_value=httpx.Response(
                    302,
                    headers={
                        "location": REDIRECT_URL,
                        "set-cookie": "PHPSESSID=abc; path=/",
                    },
                )
            )
            mock.get(REDIRECT_URL).mock(
                return_value=httpx.Response(200, headers={"set-cookie": "CABINET_GROUPE=G; path=/"})
            )
            mock.get(DOCS_URL).mock(return_value=httpx.Response(200, text=html_invalide))
            result = await ds.authenticate()
        assert result is not None
        # Seul le lien valide est gardé.
        assert len(result["properties"]) == 1
        assert result["properties"][0]["imme"] == "1234"
        assert result["properties"][0]["copro"] == "5678"
        await result["client"].aclose()

    async def test_dedup_des_proprietes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Deux liens pour la même paire imme/copro → on n'en garde qu'un.
        _set_env(monkeypatch)
        html = """
        <html><body>
          <div class="row">
            <p class="main-text">Immeuble A</p>
            <a href="documents-syndic-1-2.html">VOS DOCUMENTS</a>
            <a href="documents-syndic-1-2.html">DOCUMENTS DE L'IMMEUBLE</a>
          </div>
        </body></html>
        """
        import respx as _respx
        with _respx.mock(assert_all_called=False) as mock:
            mock.get(LOGIN_URL).mock(return_value=httpx.Response(200, text=_login_form_html()))
            mock.post(FORM_ACTION).mock(
                return_value=httpx.Response(
                    302,
                    headers={
                        "location": REDIRECT_URL,
                        "set-cookie": "PHPSESSID=abc; path=/",
                    },
                )
            )
            mock.get(REDIRECT_URL).mock(
                return_value=httpx.Response(200, headers={"set-cookie": "CABINET_GROUPE=G; path=/"})
            )
            mock.get(DOCS_URL).mock(return_value=httpx.Response(200, text=html))
            result = await ds.authenticate()
        assert result is not None
        assert len(result["properties"]) == 1
        await result["client"].aclose()
