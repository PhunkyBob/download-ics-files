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
        # Les cookies .env sont aussi des méthodes d'auth valides, donc on
        # les supprime pour vraiment tester le cas "aucune méthode".
        monkeypatch.delenv("PHPSESSID", raising=False)
        monkeypatch.delenv("CABINET_GROUPE", raising=False)
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


# ---------- Tests du mode cookie (PHPSESSID / CABINET_GROUPE) ----------


class TestAuthenticateWithCookies:
    """Tests du fallback par injection de cookies (PHPSESSID + CABINET_GROUPE).

    Couvre la règle de priorité `CLI cookies > .env login > .env cookies`,
    le merge CLI/env, et la détection de session invalide (redirection 302
    vers la page de login).
    """

    def _set_full_env(
        self, monkeypatch: pytest.MonkeyPatch,
        *,
        login_url: str = LOGIN_URL,
        login: str = "user@test.fr",
        password: str = "secret",
        phpsessid: str = "",
        cabinet_groupe: str = "",
    ) -> None:
        """Helper : pose l'ensemble des vars d'auth dans env (champs vides = absents)."""
        monkeypatch.setenv("LOGIN_URL", login_url)
        monkeypatch.setenv("LOGIN", login)
        monkeypatch.setenv("PASSWORD", password)
        if phpsessid:
            monkeypatch.setenv("PHPSESSID", phpsessid)
        else:
            monkeypatch.delenv("PHPSESSID", raising=False)
        if cabinet_groupe:
            monkeypatch.setenv("CABINET_GROUPE", cabinet_groupe)
        else:
            monkeypatch.delenv("CABINET_GROUPE", raising=False)

    # --- 1. CLI cookies complets → succès ---

    async def test_cli_cookies_complets_succes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Aucun cookie dans env (sinon ça serait en doublon mais on veut tester
        # le cas "uniquement CLI").
        monkeypatch.delenv("PHPSESSID", raising=False)
        monkeypatch.delenv("CABINET_GROUPE", raising=False)
        properties_html = _docs_html(("Immeuble A", "1234", "5678", "VOS DOCUMENTS"))

        import respx as _respx
        with _respx.mock(assert_all_called=False) as mock:
            # documents.html doit être appelé ; ni login ni redirect attendus.
            mock.get(DOCS_URL).mock(return_value=httpx.Response(200, text=properties_html))
            result = await ds.authenticate(
                cli_phpsessid="phpsess_cli",
                cli_cabinet_groupe="cab_cli",
            )

        assert result is not None
        assert result["phpsessid"] == "phpsess_cli"
        assert result["cabinet_groupe"] == "cab_cli"
        assert len(result["properties"]) == 1
        assert result["properties"][0]["imme"] == "1234"
        await result["client"].aclose()

    # --- 2. CLI cookies + merge avec .env ---

    async def test_cli_cookies_avec_merge_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # CABINET_GROUPE vient du .env, PHPSESSID vient du CLI → on merge.
        self._set_full_env(
            monkeypatch, cabinet_groupe="cab_from_env",
        )
        properties_html = _docs_html(("Immeuble A", "1234", "5678", "VOS DOCUMENTS"))

        import respx as _respx
        with _respx.mock(assert_all_called=False) as mock:
            mock.get(DOCS_URL).mock(return_value=httpx.Response(200, text=properties_html))
            result = await ds.authenticate(
                cli_phpsessid="phpsess_from_cli",
                # cli_cabinet_groupe=None → merge depuis .env
            )

        assert result is not None
        assert result["phpsessid"] == "phpsess_from_cli"
        assert result["cabinet_groupe"] == "cab_from_env"
        await result["client"].aclose()

    # --- 3. Cookies via .env (sans CLI) → succès ---

    async def test_env_cookies_succes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Pas de LOGIN_URL → on saute direct à l'étape 3 (cookies .env).
        monkeypatch.delenv("LOGIN_URL", raising=False)
        monkeypatch.delenv("LOGIN", raising=False)
        monkeypatch.delenv("PASSWORD", raising=False)
        monkeypatch.setenv("PHPSESSID", "phpsess_env")
        monkeypatch.setenv("CABINET_GROUPE", "cab_env")
        properties_html = _docs_html(("Immeuble A", "9999", "0000", "VOS DOCUMENTS"))

        import respx as _respx
        with _respx.mock(assert_all_called=False) as mock:
            mock.get(DOCS_URL).mock(return_value=httpx.Response(200, text=properties_html))
            result = await ds.authenticate()

        assert result is not None
        assert result["phpsessid"] == "phpsess_env"
        assert result["cabinet_groupe"] == "cab_env"
        assert result["properties"][0]["imme"] == "9999"
        await result["client"].aclose()

    # --- 4. Priorité : login .env > cookies .env (sans CLI) ---

    async def test_login_priorite_sur_env_cookies(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Tout est dans env : login et cookies. Sans flag CLI → login gagne.
        self._set_full_env(
            monkeypatch,
            phpsessid="phpsess_env",
            cabinet_groupe="cab_env",
        )
        properties_html = _docs_html(("Immeuble A", "1", "2", "VOS DOCUMENTS"))

        import respx as _respx
        with _respx.mock(assert_all_called=False) as mock:
            # Le flux login doit être emprunté : GET login + POST login_externe
            # + GET initialisation + GET documents.
            mock.get(LOGIN_URL).mock(return_value=httpx.Response(200, text=_login_form_html()))
            mock.post(FORM_ACTION).mock(
                return_value=httpx.Response(
                    302,
                    headers={
                        "location": REDIRECT_URL,
                        "set-cookie": "PHPSESSID=phpsess_from_login; path=/",
                    },
                )
            )
            mock.get(REDIRECT_URL).mock(
                return_value=httpx.Response(
                    200, headers={"set-cookie": "CABINET_GROUPE=cab_from_login; path=/"}
                )
            )
            mock.get(DOCS_URL).mock(return_value=httpx.Response(200, text=properties_html))
            result = await ds.authenticate()

        assert result is not None
        # Le PHPSESSID vient du flux login, PAS de l'env.
        assert result["phpsessid"] == "phpsess_from_login"
        assert result["cabinet_groupe"] == "cab_from_login"
        await result["client"].aclose()

    # --- 5. CLI cookies > login .env ---

    async def test_cli_cookies_precedent_sur_login(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Login complet dans env, MAIS --phpsessid passé → on saute le login.
        self._set_full_env(monkeypatch)
        properties_html = _docs_html(("Immeuble A", "1", "2", "VOS DOCUMENTS"))

        import respx as _respx
        with _respx.mock(assert_all_called=False) as mock:
            # Seuls documents.html doit être mocké. Si le login est tenté,
            # mock.get(LOGIN_URL) lèverait une erreur respx.
            mock.get(DOCS_URL).mock(return_value=httpx.Response(200, text=properties_html))
            result = await ds.authenticate(
                cli_phpsessid="phpsess_cli",
                cli_cabinet_groupe="cab_cli",
            )

        assert result is not None
        assert result["phpsessid"] == "phpsess_cli"
        assert result["cabinet_groupe"] == "cab_cli"
        await result["client"].aclose()

    # --- 6. LOGIN_URL absent → on tombe à l'étape 3 (cookies .env) ---

    async def test_login_url_manquant_passe_aux_cookies(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # LOGIN et PASSWORD sont là, mais LOGIN_URL manque → étape 2 skippée.
        monkeypatch.delenv("LOGIN_URL", raising=False)
        monkeypatch.setenv("LOGIN", "user@test.fr")
        monkeypatch.setenv("PASSWORD", "secret")
        monkeypatch.setenv("PHPSESSID", "phpsess_env")
        monkeypatch.setenv("CABINET_GROUPE", "cab_env")
        properties_html = _docs_html(("Immeuble A", "1", "2", "VOS DOCUMENTS"))

        import respx as _respx
        with _respx.mock(assert_all_called=False) as mock:
            # Si le login était tenté, mock.get(LOGIN_URL) lèverait.
            mock.get(DOCS_URL).mock(return_value=httpx.Response(200, text=properties_html))
            result = await ds.authenticate()

        assert result is not None
        assert result["phpsessid"] == "phpsess_env"
        await result["client"].aclose()

    # --- 7. CLI partiel sans fallback env → erreur ---

    async def test_cli_partiel_sans_env_refuse(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # --phpsessid seul, rien dans env → erreur "partial_cli_cookies".
        monkeypatch.delenv("PHPSESSID", raising=False)
        monkeypatch.delenv("CABINET_GROUPE", raising=False)

        result = await ds.authenticate(
            cli_phpsessid="phpsess_solo",
            cli_cabinet_groupe=None,
        )
        assert result is None
        captured = capsys.readouterr()
        assert "partial" in captured.out.lower() or "incomplet" in captured.out.lower()

    # --- 8. Env cookies partiel → erreur ---

    async def test_env_cookies_partiel_refuse(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # PHPSESSID seul dans env, pas de CABINET_GROUPE → erreur.
        monkeypatch.delenv("LOGIN_URL", raising=False)
        monkeypatch.delenv("LOGIN", raising=False)
        monkeypatch.delenv("PASSWORD", raising=False)
        monkeypatch.setenv("PHPSESSID", "phpsess_env")
        monkeypatch.delenv("CABINET_GROUPE", raising=False)

        result = await ds.authenticate()
        assert result is None
        captured = capsys.readouterr()
        assert "PHPSESSID" in captured.out and "CABINET_GROUPE" in captured.out

    # --- 9. Session invalide (redirect 302 vers la page de login) ---

    async def test_session_invalide_302_vers_login(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.delenv("PHPSESSID", raising=False)
        monkeypatch.delenv("CABINET_GROUPE", raising=False)
        login_url = "https://extranet2.ics.fr/V5/connexion-cdg.html"

        import respx as _respx
        with _respx.mock(assert_all_called=False) as mock:
            # Le serveur redirige vers la page de login car la session est morte.
            # On mocke aussi la cible du redirect : follow_redirects=True dans
            # le code de prod fait que httpx suit la 302 — il faut donc que
            # respx puisse répondre à l'URL cible.
            mock.get(DOCS_URL).mock(
                return_value=httpx.Response(
                    302,
                    headers={"location": login_url},
                )
            )
            mock.get(login_url).mock(return_value=httpx.Response(200, text="<html>login</html>"))
            result = await ds.authenticate(
                cli_phpsessid="phpsess_dead",
                cli_cabinet_groupe="cab_dead",
            )

        assert result is None
        captured = capsys.readouterr()
        assert "invalide" in captured.out.lower() or "expir" in captured.out.lower()

    # --- 10. documents.html 500 ---

    async def test_documents_html_500(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PHPSESSID", raising=False)
        monkeypatch.delenv("CABINET_GROUPE", raising=False)

        import respx as _respx
        with _respx.mock(assert_all_called=False) as mock:
            mock.get(DOCS_URL).mock(return_value=httpx.Response(500))
            result = await ds.authenticate(
                cli_phpsessid="phpsess_500",
                cli_cabinet_groupe="cab_500",
            )

        assert result is None

    # --- 11. Aucune méthode disponible ---

    async def test_aucune_methode_disponible(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.delenv("LOGIN_URL", raising=False)
        monkeypatch.delenv("LOGIN", raising=False)
        monkeypatch.delenv("PASSWORD", raising=False)
        monkeypatch.delenv("PHPSESSID", raising=False)
        monkeypatch.delenv("CABINET_GROUPE", raising=False)

        result = await ds.authenticate()
        assert result is None
        captured = capsys.readouterr()
        # Le message doit aider l'utilisateur en listant les options.
        out = captured.out.lower()
        assert "login" in out
        assert "phpsessid" in out
        assert "cabinet_groupe" in out
