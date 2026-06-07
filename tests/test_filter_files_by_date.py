"""Tests de filter_files_by_date — filtre temporel appliqué après collecte.

Le filtrage par date est l'un des points d'entrée utilisateur. Une régression
ici pourrait soit faire télécharger des archives antérieures à la date
souhaitée, soit au contraire exclure des fichiers récents.
"""

from __future__ import annotations

import pytest

import download_syndic as ds


def _file(date: str | None) -> dict:
    return {"dateCreated": date, "nom": f"file_{date}.pdf", "guid": date or "?"}


class TestFilterFilesByDate:
    """Tests de filter_files_by_date."""

    def test_garde_les_fichiers_apres_la_date(self) -> None:
        files = [(_file("2024-03-15"), "/a"), (_file("2024-12-01"), "/a")]
        out = ds.filter_files_by_date(files, "2024-06")
        assert len(out) == 1
        assert out[0][0]["dateCreated"] == "2024-12-01"

    def test_inclut_les_fichiers_a_la_frontiere(self) -> None:
        # Comparaison lexicographique : "2024-06-01" >= "2024-06-01" → inclus.
        files = [(_file("2024-06-01"), "/a")]
        out = ds.filter_files_by_date(files, "2024-06")
        assert len(out) == 1

    def test_exclut_les_fichiers_avant_la_date(self) -> None:
        files = [(_file("2023-12-31"), "/a"), (_file("2024-01-01"), "/a")]
        out = ds.filter_files_by_date(files, "2024-12")
        # 2024-12 → start_date_normalized = 2024-12-01 : les deux dates sont < 2024-12-01.
        assert len(out) == 0

    def test_conserve_les_fichiers_sans_date(self) -> None:
        # Pas de dateCreated → on garde le fichier (mieux vaut télécharger en trop).
        # L'autre fichier a une date postérieure au filtre.
        files = [(_file(None), "/a"), (_file("2024-12-15"), "/a")]
        out = ds.filter_files_by_date(files, "2024-06")
        assert len(out) == 2

    def test_liste_vide(self) -> None:
        assert ds.filter_files_by_date([], "2024-01") == []

    def test_preserve_le_folder_path(self) -> None:
        # Le filtre ne doit pas perdre l'information du dossier destination.
        files = [(_file("2024-12-01"), "/some/folder")]
        out = ds.filter_files_by_date(files, "2024-01")
        assert out[0][1] == "/some/folder"

    @pytest.mark.parametrize(
        "start_date, date_created, kept",
        [
            ("2024-01", "2023-12-31", False),
            ("2024-01", "2024-01-01", True),
            ("2024-01", "2024-12-31", True),
            ("2024-06", "2024-05-31", False),
            ("2024-06", "2024-06-30", True),
            ("2099-12", "2024-01-01", False),
        ],
    )
    def test_comparaisons_lexicographiques(self, start_date: str, date_created: str, kept: bool) -> None:
        files = [(_file(date_created), "/a")]
        out = ds.filter_files_by_date(files, start_date)
        assert (len(out) == 1) is kept
