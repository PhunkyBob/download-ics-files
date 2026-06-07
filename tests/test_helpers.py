"""Tests des helpers internes (fonctions préfixées `_` et utilitaires divers).

Ces fonctions sont petites mais leur comportement conditionne l'affichage et
le bon fonctionnement de l'orchestration. Toute régression silencieuse ici
peut masquer un bug plus loin dans le pipeline.
"""

from __future__ import annotations

from typing import Any

import pytest

import download_syndic as ds


class TestTruncate:
    """Tests de _truncate."""

    def test_chaine_courte_retournee_telle_quelle(self) -> None:
        assert ds._truncate("abc", 10) == "abc"

    def test_chaine_egale_a_la_limite(self) -> None:
        assert ds._truncate("abcdefghij", 10) == "abcdefghij"

    def test_chaine_longue_est_tronquee_avec_ellipsis(self) -> None:
        result = ds._truncate("abcdefghijklmnop", 10)
        # On garde 7 caractères + "..." = 10 au total.
        assert result == "abcdefg..."
        assert len(result) == 10

    def test_limite_trois_caracteres(self) -> None:
        # Comportement limite : on garde 0 char + "...".
        assert ds._truncate("abcdef", 3) == "..."

    def test_chaine_vide(self) -> None:
        assert ds._truncate("", 5) == ""


class TestEmptyFolderContent:
    """Tests de _empty_folder_content."""

    def test_retourne_dictionnaire_avec_cles_attendues(self) -> None:
        content = ds._empty_folder_content()
        assert "folders" in content
        assert "files" in content
        assert "directory_info" in content
        assert content["folders"] == []
        assert content["files"] == []
        assert content["directory_info"] == {}

    def test_retourne_une_nouvelle_instance_a_chaque_appel(self) -> None:
        # Important : on mute ce dictionnaire côté appelant, on ne veut pas
        # partager une référence mutable entre appels.
        a = ds._empty_folder_content()
        b = ds._empty_folder_content()
        a["files"].append("x")
        assert b["files"] == []


class TestExtractFileInfo:
    """Tests de _extract_file_info — projection d'un doc API vers FileInfo."""

    def test_extrait_tous_les_champs_attendus(self) -> None:
        doc: dict[str, Any] = {
            "guid": "ABC-123",
            "nom": "facture.pdf",
            "nomGed": "facture_ged",
            "dateUpload": "2024-01-15",
            "extension": ".pdf",
            "size": 12345,
            "emplacement": "/some/path",
            "arborescence": "root/sub",
            "droits": "Conseil syndical",
            "dateCreated": "2024-01-10",
            "source": "GED",
            # Champ non listé ci-dessous : doit être ignoré.
            "champ_inconnu": "valeur",
        }
        info = ds._extract_file_info(doc)
        assert info["guid"] == "ABC-123"
        assert info["nom"] == "facture.pdf"
        assert info["nomGed"] == "facture_ged"
        assert info["dateUpload"] == "2024-01-15"
        assert info["extension"] == ".pdf"
        assert info["size"] == 12345
        assert info["emplacement"] == "/some/path"
        assert info["arborescence"] == "root/sub"
        assert info["droits"] == "Conseil syndical"
        assert info["dateCreated"] == "2024-01-10"
        assert info["source"] == "GED"
        assert "champ_inconnu" not in info

    def test_champs_manquants_deviennent_none(self) -> None:
        info = ds._extract_file_info({})
        for value in info.values():
            assert value is None


class TestHasLoginExterne:
    """Tests de _has_login_externe — utilisée pour identifier le bon formulaire."""

    @pytest.mark.parametrize(
        "action, expected",
        [
            ("https://site/login_externe.php", True),
            ("/path/to/login_externe_check", True),
            ("login_externe", True),
            ("https://site/other_action", False),
            ("", False),
            (None, False),
        ],
    )
    def test_detection(self, action: str | None, expected: bool) -> None:
        assert ds._has_login_externe(action) is expected


class TestUpdateThreadBar:
    """Tests de _update_thread_bar — robuste face à l'absence de barres actives."""

    def test_no_op_si_enable_thread_bars_false(self) -> None:
        # C'est un no-op complet, on vérifie juste qu'il ne lève pas.
        ds.enable_thread_bars = False
        ds.thread_progress_bars = {0: "fake-bar"}  # type: ignore[assignment]
        ds._update_thread_bar(0, "nouveau message")  # ne doit pas lever

    def test_no_op_si_thread_id_none(self) -> None:
        ds.enable_thread_bars = True
        ds.thread_progress_bars = {0: "fake-bar"}  # type: ignore[assignment]
        ds._update_thread_bar(None, "msg")  # ne doit pas lever

    def test_no_op_si_thread_id_inconnu(self) -> None:
        ds.enable_thread_bars = True
        ds.thread_progress_bars = {}  # aucune barre pour 0
        ds._update_thread_bar(0, "msg")  # ne doit pas lever

    def test_met_a_jour_si_barre_presente(self) -> None:
        # Mock simple d'un tqdm : on enregistre juste les accès à `bar_format`.
        class FakeBar:
            bar_format: str = ""
            refresh_called: bool = False

            def refresh(self) -> None:
                self.refresh_called = True

        bar = FakeBar()
        ds.enable_thread_bars = True
        ds.thread_progress_bars = {2: bar}  # type: ignore[assignment]
        ds._update_thread_bar(2, "nouveau format")
        assert bar.bar_format == "nouveau format"
        assert bar.refresh_called is True

    def test_avale_les_exceptions_sur_une_barre_defectueuse(self) -> None:
        # Une barre qui plante au refresh ne doit pas faire crasher le downloader.
        class BadBar:
            bar_format: str = ""
            def refresh(self) -> None:
                raise RuntimeError("boom")

        ds.enable_thread_bars = True
        ds.thread_progress_bars = {1: BadBar()}  # type: ignore[assignment]
        ds._update_thread_bar(1, "msg")  # ne doit pas lever (try/except interne)
