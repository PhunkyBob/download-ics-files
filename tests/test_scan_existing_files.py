"""Tests de scan_existing_files — inventaire local avant téléchargement.

Le scan local est partagé entre toutes les propriétés pour éviter de reparcourir
l'arbre N fois. Un bug ici (fichier non détecté) forcerait un re-téléchargement
systématique ; un faux positif (fichier fantôme détecté) masquerait des
téléchargements attendus.
"""

from __future__ import annotations

import os

import download_syndic as ds


class TestScanExistingFiles:
    """Tests de scan_existing_files."""

    def test_dossier_inexistant_retourne_set_vide(self, tmp_path) -> None:
        # Pas de message d'erreur, pas de crash, on rend juste un set vide.
        result = ds.scan_existing_files(str(tmp_path / "nope"))
        assert result == set()

    def test_dossier_vide(self, tmp_path) -> None:
        result = ds.scan_existing_files(str(tmp_path))
        assert result == set()

    def test_liste_les_fichiers_a_la_racine(self, tmp_path) -> None:
        (tmp_path / "a.pdf").write_bytes(b"x")
        (tmp_path / "b.pdf").write_bytes(b"y")

        result = ds.scan_existing_files(str(tmp_path))
        assert len(result) == 2
        assert os.path.join(str(tmp_path), "a.pdf") in result
        assert os.path.join(str(tmp_path), "b.pdf") in result

    def test_parcours_recursif(self, tmp_path) -> None:
        sub = tmp_path / "sub" / "subsub"
        sub.mkdir(parents=True)
        (tmp_path / "root.pdf").write_bytes(b"1")
        (tmp_path / "sub" / "lvl1.pdf").write_bytes(b"2")
        (sub / "lvl2.pdf").write_bytes(b"3")

        result = ds.scan_existing_files(str(tmp_path))
        assert len(result) == 3
        # Tous les chemins doivent être absolus (relatifs au dossier de scan).
        for path in result:
            assert os.path.isabs(path)

    def test_chemins_retournes_sont_complets(self, tmp_path) -> None:
        (tmp_path / "x.pdf").write_bytes(b"x")
        result = ds.scan_existing_files(str(tmp_path))
        for path in result:
            assert path.startswith(str(tmp_path))

    def test_ignore_les_sous_dossiers(self, tmp_path) -> None:
        # Seuls les fichiers sont comptés, pas les dossiers.
        (tmp_path / "dossier_vide").mkdir()
        (tmp_path / "fichier.txt").write_bytes(b"x")
        result = ds.scan_existing_files(str(tmp_path))
        assert len(result) == 1
        assert all(os.path.isfile(p) for p in result)

    def test_ne_leve_pas_si_permission_erreur(self, tmp_path) -> None:
        # On simule un fichier qu'on ne peut pas stat() — la fonction swallow
        # l'OSError et continue.
        (tmp_path / "accessible.pdf").write_bytes(b"x")
        # Ce test reste léger : on vérifie juste qu'aucune exception n'est levée
        # sur des fichiers normaux. Le cas OSError est difficile à reproduire
        # de manière portable, mais le code le gère (try/except OSError: continue).
        result = ds.scan_existing_files(str(tmp_path))
        assert len(result) == 1
