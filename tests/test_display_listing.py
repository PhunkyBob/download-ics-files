"""Tests de display_listing — affichage du listing (mode à blanc).

Cette fonction ne fait QUE print ; on capture stdout pour vérifier que
les compteurs (nouveaux vs existants) sont corrects. C'est ce que voit
l'utilisateur en mode à blanc — d'où l'importance de la cohérence.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

import download_syndic as ds


@pytest.fixture
def base_folder(tmp_path) -> Iterator[str]:
    folder = str(tmp_path / "dest")
    os.makedirs(folder, exist_ok=True)
    yield folder


def _file(guid: str, nom: str = "x.pdf", is_new: bool = True) -> dict:
    return {
        "guid": guid,
        "nom": nom,
        "nomGed": nom,
        "extension": ".pdf",
        "emplacement": f"/{nom}",
        "_clean_name": nom,
        "_is_new": is_new,
    }


class TestDisplayListing:
    """Tests de display_listing."""

    def test_aucun_fichier(self, base_folder: str, capsys) -> None:
        ds.display_listing([], set(), base_folder)
        captured = capsys.readouterr()
        assert "0 fichiers" in captured.out

    def test_compte_new_et_existing(self, base_folder: str, capsys) -> None:
        files = [
            (_file("G1", "a.pdf", is_new=True), base_folder),
            (_file("G2", "b.pdf", is_new=False), base_folder),
            (_file("G3", "c.pdf", is_new=True), base_folder),
        ]
        ds.display_listing(files, set(), base_folder)
        captured = capsys.readouterr()
        # 3 fichiers, 2 nouveaux, 1 existant.
        assert "3 fichiers" in captured.out
        assert "2 nouveaux" in captured.out
        assert "1 déjà présents" in captured.out

    def test_groupe_par_dossier(self, base_folder: str, capsys) -> None:
        sub = os.path.join(base_folder, "Sub")
        files = [
            (_file("G1", "a.pdf"), base_folder),
            (_file("G2", "b.pdf"), sub),
            (_file("G3", "c.pdf"), sub),
        ]
        ds.display_listing(files, set(), base_folder)
        captured = capsys.readouterr()
        # On doit voir les deux dossiers listés.
        assert "Racine" in captured.out or "." in captured.out
        assert "Sub" in captured.out

    def test_affiche_statut_nouveau(self, base_folder: str, capsys) -> None:
        files = [(_file("G1", "a.pdf", is_new=True), base_folder)]
        ds.display_listing(files, set(), base_folder)
        captured = capsys.readouterr()
        assert "🆕" in captured.out or "nouveau" in captured.out

    def test_affiche_statut_existant(self, base_folder: str, capsys) -> None:
        files = [(_file("G1", "a.pdf", is_new=False), base_folder)]
        ds.display_listing(files, set(), base_folder)
        captured = capsys.readouterr()
        assert "✅" in captured.out or "présent" in captured.out

    def test_limite_affichage_a_20_fichiers(self, base_folder: str, capsys) -> None:
        files = [(_file(f"G{i}", f"f{i}.pdf"), base_folder) for i in range(25)]
        ds.display_listing(files, set(), base_folder)
        captured = capsys.readouterr()
        # Au-delà de 20, on affiche "... et N autres".
        assert "et 5 autres" in captured.out

    def test_chemin_relatif_correct(self, base_folder: str, capsys) -> None:
        # Le sous-dossier doit être affiché en chemin relatif.
        sub = os.path.join(base_folder, "Immeuble B", "Archives")
        files = [(_file("G1", "a.pdf"), sub)]
        ds.display_listing(files, set(), base_folder)
        captured = capsys.readouterr()
        # Le path relatif doit apparaître (pas le chemin absolu complet).
        assert "Immeuble B" in captured.out

    def test_utilise_clean_name_si_present(self, base_folder: str, capsys) -> None:
        # Si _clean_name est défini, on l'utilise (cas venant de la collecte).
        info = _file("G1", "raw_name.pdf")
        info["_clean_name"] = "nom_propre.pdf"
        files = [(info, base_folder)]
        ds.display_listing(files, set(), base_folder)
        captured = capsys.readouterr()
        # On doit voir le nom nettoyé, pas le raw.
        assert "nom_propre.pdf" in captured.out
        assert "raw_name.pdf" not in captured.out

    def test_resume_global(self, base_folder: str, capsys) -> None:
        files = [
            (_file("G1", "a.pdf", is_new=True), base_folder),
            (_file("G2", "b.pdf", is_new=False), base_folder),
        ]
        ds.display_listing(files, set(), base_folder)
        captured = capsys.readouterr()
        # Le résumé final doit donner les totaux.
        assert "Résumé" in captured.out
        assert "2 fichiers en ligne" in captured.out
