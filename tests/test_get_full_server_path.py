"""Tests de get_full_server_path — extraction du chemin serveur local.

Cette fonction reconstruit un chemin local à partir des métadonnées du dossier
retournées par l'API. Elle applique le strip du préfixe ICS standard et
nettoie chaque composant. Une régression ici casserait la création des
sous-dossiers de téléchargement.
"""

from __future__ import annotations

import os

import pytest

import download_syndic as ds


class TestGetFullServerPath:
    """Tests de get_full_server_path."""

    def test_chemin_complet_sans_prefixe(self) -> None:
        info = {"cheminComplet": "/Immeuble A/Sous-dossier"}
        # Les composants sont nettoyés par clean_file_name.
        result = ds.get_full_server_path(info)
        assert result == os.path.join("Immeuble A", "Sous-dossier")

    def test_strip_du_prefixe_ics_standard(self) -> None:
        info = {"cheminComplet": "/u/clients/clesev/ges_oullins_ged/Immeuble X/Sous"}
        result = ds.get_full_server_path(info)
        assert "clesev" not in result
        assert "ges_oullins_ged" not in result
        assert result == os.path.join("Immeuble X", "Sous")

    def test_utilise_chemin_si_chemin_complet_absent(self) -> None:
        info = {"chemin": "/Immeuble B/Sous-dossier"}
        result = ds.get_full_server_path(info)
        assert result == os.path.join("Immeuble B", "Sous-dossier")

    def test_chemin_complet_prioritaire_sur_chemin(self) -> None:
        # Si les deux sont présents, on utilise cheminComplet.
        info = {
            "cheminComplet": "/complet",
            "chemin": "/simple",
        }
        result = ds.get_full_server_path(info)
        assert result == "complet"

    def test_chemin_vide(self) -> None:
        assert ds.get_full_server_path({}) == ""
        assert ds.get_full_server_path({"chemin": "", "cheminComplet": ""}) == ""

    def test_caracteres_speciaux_dans_composants(self) -> None:
        # Les composants sales sont nettoyés via clean_file_name.
        info = {"cheminComplet": "/Immeuble?/Dossier*"}
        result = ds.get_full_server_path(info)
        # Pas de ? ni de * dans le résultat.
        assert "?" not in result
        assert "*" not in result

    def test_separateur_os_applique(self) -> None:
        # On doit utiliser os.sep pour la portabilité (ici \ sous Windows).
        info = {"cheminComplet": "/A/B/C"}
        result = ds.get_full_server_path(info)
        # Le chemin doit utiliser os.sep entre les composants.
        assert result == os.path.join("A", "B", "C")

    @pytest.mark.parametrize(
        "raw, expected_parts",
        [
            ("/A/B", ["A", "B"]),
            ("/A/B/C", ["A", "B", "C"]),
            ("A/B", ["A", "B"]),  # pas de slash initial → pas de partie vide
        ],
    )
    def test_gestion_du_slash_initial(self, raw: str, expected_parts: list[str]) -> None:
        info = {"cheminComplet": raw}
        result = ds.get_full_server_path(info)
        for part in expected_parts:
            assert part in result
