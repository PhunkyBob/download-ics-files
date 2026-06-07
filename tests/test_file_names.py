"""Tests des utilitaires de nommage de fichiers.

`clean_file_name` normalise un nom (espaces doublés, caractères spéciaux) et
`construct_file_name` ajoute le nomGed entre parenthèses si différent du nom
principal. Ces deux fonctions sont sur le chemin critique de chaque sauvegarde,
donc bien testées.
"""

from __future__ import annotations

import pytest

import download_syndic as ds


class TestCleanFileName:
    """Tests de clean_file_name."""

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("simple.pdf", "simple.pdf"),
            ("  espaces  multiples  .pdf  ", "espaces multiples .pdf"),
            ("élève.pdf", "_l_ve.pdf"),  # caractères non-ASCII → underscore
            ("a/b/c.pdf", "a_b_c.pdf"),  # slash remplacé
            ("file|name?.pdf", "file_name_.pdf"),
            ("a:b*c.pdf", "a_b_c.pdf"),
            ("..secret..pdf", "..secret..pdf"),  # les points sont conservés
        ],
    )
    def test_normalisation(self, raw: str, expected: str) -> None:
        assert ds.clean_file_name(raw) == expected

    def test_espaces_multiples_sont_repliques_en_un_seul(self) -> None:
        assert ds.clean_file_name("a     b") == "a b"

    def test_underscore_double_et_tiret_acceptes(self) -> None:
        # Le tiret et l'underscore sont dans la whitelist et ne sont pas modifiés.
        assert ds.clean_file_name("foo-bar_baz.pdf") == "foo-bar_baz.pdf"

    def test_strip_des_espaces_et_points_en_bord(self) -> None:
        assert ds.clean_file_name("   foo.pdf   ") == "foo.pdf"


class TestConstructFileName:
    """Tests de construct_file_name — composition finale du nom de fichier."""

    def test_nom_simple_sans_nomGed(self) -> None:
        info = {"nom": "facture.pdf", "extension": ".pdf", "nomGed": "facture.pdf"}
        assert ds.construct_file_name(info) == "facture.pdf"

    def test_ajoute_extension_manquante(self) -> None:
        info = {"nom": "facture", "extension": ".pdf", "nomGed": "facture"}
        assert ds.construct_file_name(info) == "facture.pdf"

    def test_insere_nomGed_entre_parentheses_si_different(self) -> None:
        info = {
            "nom": "Doc principal.pdf",
            "extension": ".pdf",
            "nomGed": "Doc original.pdf",
        }
        # L'extension est retirée du nom principal ; le nomGed est inséré tel
        # quel (avec son extension), entre parenthèses.
        assert ds.construct_file_name(info) == "Doc principal (Doc original.pdf).pdf"

    def test_nomGed_egal_au_nom_pas_de_suffixe(self) -> None:
        # Si nomGed == nom, on ne duplique pas l'information.
        info = {"nom": "x.pdf", "extension": ".pdf", "nomGed": "x.pdf"}
        assert ds.construct_file_name(info) == "x.pdf"

    def test_nomGed_vide_pas_de_suffixe(self) -> None:
        info = {"nom": "x.pdf", "extension": ".pdf", "nomGed": ""}
        assert ds.construct_file_name(info) == "x.pdf"

    def test_nom_defaut_si_manquant(self) -> None:
        info = {"extension": ".pdf"}  # pas de "nom"
        assert ds.construct_file_name(info) == "unknown.pdf"

    def test_extension_vide(self) -> None:
        info = {"nom": "datafile", "extension": "", "nomGed": ""}
        assert ds.construct_file_name(info) == "datafile"

    def test_caracteres_speciaux_sont_assainis(self) -> None:
        # Les caractères non whitelistés sont remplacés par _ avant insertion.
        info = {
            "nom": "Doc?principal.pdf",
            "extension": ".pdf",
            "nomGed": "Doc/original.pdf",
        }
        result = ds.construct_file_name(info)
        # Les deux noms sont nettoyés ; le slash du nomGed devient underscore.
        assert "Doc_principal" in result
        assert "Doc_original" in result
        assert result.endswith(".pdf")
