"""Tests des constructeurs d'URL du module download_syndic.

Ces fonctions sont pures (aucun effet de bord, aucun appel réseau) et méritent
une couverture exhaustive : leur sortie détermine si les requêtes HTTP
atteignent le bon endpoint avec les bons paramètres.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

import download_syndic as ds


class TestBuildSearchUrl:
    """Tests de build_search_url."""

    def test_construit_url_avec_parametres_par_defaut(self) -> None:
        url = ds.build_search_url("abc123", 42)
        parsed = urlparse(url)
        params = parse_qs(parsed.query)

        assert parsed.scheme == "https"
        assert parsed.netloc == "extranet2.ics.fr"
        assert parsed.path == "/webservice/gedservice/SearchArborescenceContentServlet"

        assert params["token"] == ["abc123"]
        assert params["id"] == ["42"]
        assert params["page"] == ["1"]
        assert params["resultNumber"] == ["10"]
        assert params["cabinet"] == ["false"]
        assert params["toJson"] == ["true"]
        assert params["sortName"] == ["DESCENDING_DATE"]
        assert params["droits"] == ["Conseil syndical"]

    def test_accepte_numero_de_page_personnalise(self) -> None:
        url = ds.build_search_url("tok", 7, page=3)
        assert parse_qs(urlparse(url).query)["page"] == ["3"]

    def test_accepte_folder_id_string(self) -> None:
        url = ds.build_search_url("tok", "abc-def")
        assert parse_qs(urlparse(url).query)["id"] == ["abc-def"]

    def test_token_est_force_en_string(self) -> None:
        # Si un entier est passé par erreur, il doit quand même être sérialisé.
        url = ds.build_search_url(123456, 1)  # type: ignore[arg-type]
        assert "token=123456" in url


class TestBuildGedUrl:
    """Tests de build_ged_url."""

    def test_passe_les_parametres_de_base(self) -> None:
        url = ds.build_ged_url("FooServlet", "tok", id="9", type="Immeuble")
        params = parse_qs(urlparse(url).query)

        assert urlparse(url).path == "/webservice/gedservice/FooServlet"
        assert params["token"] == ["tok"]
        assert params["id"] == ["9"]
        assert params["type"] == ["Immeuble"]
        # Paramètres par défaut toujours présents :
        assert params["page"] == ["1"]
        assert params["toJson"] == ["true"]

    def test_ignore_les_parametres_a_valeur_none(self) -> None:
        url = ds.build_ged_url("S", "tok", id="1", extra=None, other="x")
        params = parse_qs(urlparse(url).query)

        assert "extra" not in params
        assert params["other"] == ["x"]

    def test_force_string_pour_chaque_valeur(self) -> None:
        url = ds.build_ged_url("S", "tok", page=2, resultNumber=50)
        params = parse_qs(urlparse(url).query)
        assert params["page"] == ["2"]
        assert params["resultNumber"] == ["50"]


class TestBuildEntityUrl:
    """Tests de build_entity_url — couvre les deux variantes de vue API."""

    def test_vue_vos_documents_ajoute_subtype(self) -> None:
        url = ds.build_entity_url("tok", "imme1", "copro1", use_copro_filter=True)
        params = parse_qs(urlparse(url).query)

        assert params["subType"] == ["COPROPRIETAIRE"]
        assert params["subTypeId"] == ["copro1"]
        assert params["type"] == ["Immeuble"]
        assert params["id"] == ["imme1"]
        assert params["isPermissionFilterEnabled"] == ["true"]

    def test_vue_immeuble_omet_subtype(self) -> None:
        url = ds.build_entity_url("tok", "imme1", "copro1", use_copro_filter=False)
        params = parse_qs(urlparse(url).query)

        # Sans filtre copro, l'API accepte la requête pour la vue IMMEUBLE.
        assert "subType" not in params
        assert "subTypeId" not in params
        assert params["type"] == ["Immeuble"]

    def test_page_par_defaut_est_1(self) -> None:
        url = ds.build_entity_url("tok", "i", "c")
        assert parse_qs(urlparse(url).query)["page"] == ["1"]

    def test_page_personnalisee(self) -> None:
        url = ds.build_entity_url("tok", "i", "c", page=5)
        assert parse_qs(urlparse(url).query)["page"] == ["5"]


class TestBuildSubfolderUrl:
    """Tests de build_subfolder_url — logique de pagination vs navigation."""

    URL_BASE = (
        "https://extranet2.ics.fr/webservice/gedservice/GetEntityContentServlet"
        "?cabinet=false&droits=Conseil+syndical&id=10&page=1"
        "&resultNumber=10&sortName=DESCENDING_DATE&toJson=true"
        "&token=ABC&type=Immeuble"
    )
    URL_VOS = (
        "https://extranet2.ics.fr/webservice/gedservice/GetEntityContentServlet"
        "?subType=COPROPRIETAIRE&subTypeId=99&id=10&page=1&token=ABC"
    )

    def test_avec_token_et_sans_preserve_path_bascule_sur_search_arborescence(self) -> None:
        # Navigation vers un sous-dossier → on utilise SearchArborescenceContentServlet.
        url = ds.build_subfolder_url(self.URL_BASE, "555", preserve_path=False)
        assert "SearchArborescenceContentServlet" in url
        assert parse_qs(urlparse(url).query)["id"] == ["555"]

    def test_reporte_subtype_et_subtypeid_du_parent(self) -> None:
        url = ds.build_subfolder_url(self.URL_VOS, "555", preserve_path=False)
        params = parse_qs(urlparse(url).query)
        # VOS → on doit conserver subType/subTypeId pour rester dans le bon contexte copro.
        assert params.get("subType") == ["COPROPRIETAIRE"]
        assert params.get("subTypeId") == ["99"]
        assert "SearchArborescenceContentServlet" in url

    def test_preserve_path_ne_change_que_id_et_page(self) -> None:
        url = ds.build_subfolder_url(self.URL_BASE, "555", page=2, preserve_path=True)
        params = parse_qs(urlparse(url).query)
        # On garde le path du parent (GetEntityContentServlet) pour paginer.
        assert "GetEntityContentServlet" in url
        assert params["id"] == ["555"]
        assert params["page"] == ["2"]
        # Les autres paramètres du parent sont préservés.
        assert params["token"] == ["ABC"]
        assert params["type"] == ["Immeuble"]

    def test_token_manquant_bascule_sur_preserve_path(self) -> None:
        # Si on n'arrive pas à extraire le token, on garde le path du parent (best effort).
        url_no_token = "https://extranet2.ics.fr/webservice/gedservice/Foo?id=1"
        url = ds.build_subfolder_url(url_no_token, "9", preserve_path=False)
        assert parse_qs(urlparse(url).query)["id"] == ["9"]


@pytest.mark.parametrize(
    "use_filter, expected_keys",
    [
        (True, {"subType", "subTypeId"}),
        (False, set()),
    ],
)
def test_build_entity_url_presence_subtype(use_filter: bool, expected_keys: set[str]) -> None:
    url = ds.build_entity_url("t", "i", "c", use_copro_filter=use_filter)
    params_keys = set(parse_qs(urlparse(url).query).keys())
    assert expected_keys.issubset(params_keys) if expected_keys else True
