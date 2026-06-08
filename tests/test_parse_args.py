"""Tests de parse_args — parser argparse des arguments CLI.

Couvre :
- les valeurs par défaut (mode interactif, sans flag) ;
- --download-all activé/désactivé ;
- --since valide / invalide (mauvais format → SystemExit) ;
- la cohérence des noms (long flags uniquement, doubles tirets).
"""

from __future__ import annotations

import pytest

import download_syndic as ds


class TestParseArgs:
    """Tests du parser argparse."""

    def test_sans_args_defauts_interactifs(self) -> None:
        """Sans argument : mode interactif (download_all=False) et date 2000-01."""
        args = ds.parse_args([])
        assert args.download_all is False
        assert args.since == "2000-01"

    def test_flag_download_all_active(self) -> None:
        args = ds.parse_args(["--download-all"])
        assert args.download_all is True
        assert args.since == "2000-01"  # défaut préservé

    def test_since_personnalise(self) -> None:
        args = ds.parse_args(["--since", "2024-06"])
        assert args.download_all is False
        assert args.since == "2024-06"

    def test_download_all_et_since_combines(self) -> None:
        """Les deux flags sont indépendants et combinables."""
        args = ds.parse_args(["--download-all", "--since", "2023-01"])
        assert args.download_all is True
        assert args.since == "2023-01"

    def test_since_invalide_leve_systemexit(self) -> None:
        """Un format invalide (pas YYYY-MM) doit faire échouer argparse → SystemExit(2)."""
        with pytest.raises(SystemExit) as exc_info:
            ds.parse_args(["--since", "2024/01"])
        assert exc_info.value.code == 2

    def test_since_annee_seule_invalide(self) -> None:
        with pytest.raises(SystemExit):
            ds.parse_args(["--since", "2024"])

    def test_since_vide_invalide(self) -> None:
        with pytest.raises(SystemExit):
            ds.parse_args(["--since", ""])

    def test_flag_inconnu_rejete(self) -> None:
        """Les flags non déclarés sont rejetés (typo protection)."""
        with pytest.raises(SystemExit):
            ds.parse_args(["--download"])  # manque -all

    def test_aide_disponible(self, capsys) -> None:
        """--help affiche l'usage (et exit 0) — vérifie juste qu'on n'a pas d'erreur cachée."""
        with pytest.raises(SystemExit) as exc_info:
            ds.parse_args(["--help"])
        assert exc_info.value.code == 0
        # L'usage doit mentionner les deux flags documentés.
        captured = capsys.readouterr()
        assert "--download-all" in captured.out
        assert "--since" in captured.out
