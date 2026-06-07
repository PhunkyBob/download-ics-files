"""Tests de get_folder_content — récupération paginée du contenu d'un dossier.

Cette fonction parse la réponse JSON de l'API, distingue dossiers/fichiers,
extrait les infos pertinentes, et gère la pagination tant que l'API renvoie
le nombre de résultats attendu. Une régression silencieuse (mauvaise clé
extraite, pagination infinie, dédup cassée) pourrait passer inaperçue.
"""

from __future__ import annotations

from urllib.parse import urlencode

import httpx
import pytest
import respx

import download_syndic as ds


def _folder_url(folder_id: int = 10, result_number: int = 10) -> str:
    params = {
        "cabinet": "false",
        "id": str(folder_id),
        "page": "1",
        "resultNumber": str(result_number),
        "token": "TOK",
    }
    return f"https://extranet2.ics.fr/webservice/gedservice/SearchArborescenceContentServlet?{urlencode(params)}"


def _ok_payload(
    sons: list[dict] | None = None,
    docs: list[dict] | None = None,
    directory: dict | None = None,
) -> dict:
    return {
        "responseCode": "200",
        "payload": {
            "sons": sons or [],
            "docs": docs or [],
            "directory": directory or {},
        },
    }


class TestGetFolderContent:
    """Tests de get_folder_content."""

    async def test_dossier_vide(self) -> None:
        url = _folder_url()
        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr") as mock:
                mock.get(url).mock(return_value=httpx.Response(200, json=_ok_payload()))
                content = await ds.get_folder_content(url, client)
        assert content["folders"] == []
        assert content["files"] == []
        assert content["directory_info"] == {}

    async def test_extrait_dossiers_et_fichiers(self) -> None:
        url = _folder_url()
        sons = [
            {
                "type": "DOSSIER",
                "idArbo": 100,
                "nom": "Sous-dossier 1",
                "nomGed": "sg1",
                "chemin": "/A/Sous1",
                "cheminComplet": "/complet/Sous1",
                "documentsCount": 5,
                "foldersCount": 0,
                "droits": "Conseil syndical",
            },
            # Élément non-DOSSIER : doit être ignoré.
            {"type": "FICHIER", "nom": "should_be_ignored"},
        ]
        docs = [
            {
                "guid": "G1",
                "nom": "facture.pdf",
                "nomGed": "facture_ged",
                "dateUpload": "2024-01-15",
                "extension": ".pdf",
                "size": 100,
                "emplacement": "/A/facture.pdf",
                "arborescence": "A",
                "droits": "Conseil syndical",
                "dateCreated": "2024-01-10",
                "source": "GED",
            }
        ]
        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr") as mock:
                mock.get(url).mock(return_value=httpx.Response(200, json=_ok_payload(sons=sons, docs=docs)))
                content = await ds.get_folder_content(url, client)
        assert len(content["folders"]) == 1
        assert content["folders"][0]["id"] == 100
        assert content["folders"][0]["nom"] == "Sous-dossier 1"
        assert len(content["files"]) == 1
        assert content["files"][0]["guid"] == "G1"

    async def test_status_non_200_retourne_dossier_vide(self) -> None:
        url = _folder_url()
        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr") as mock:
                mock.get(url).mock(return_value=httpx.Response(500, json={"error": "server"}))
                content = await ds.get_folder_content(url, client)
        assert content["folders"] == []
        assert content["files"] == []

    async def test_responseCode_non_200_retourne_dossier_vide(self, capsys) -> None:
        url = _folder_url()
        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr") as mock:
                mock.get(url).mock(
                    return_value=httpx.Response(200, json={"responseCode": "500", "msg": "nope"})
                )
                content = await ds.get_folder_content(url, client)
        assert content == ds._empty_folder_content()
        captured = capsys.readouterr()
        # On a loggé l'erreur (responseCode + URL + payload complet).
        assert "responseCode=" in captured.out
        assert "URL:" in captured.out

    async def test_json_invalide_retourne_dossier_vide(self) -> None:
        url = _folder_url()
        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr") as mock:
                mock.get(url).mock(return_value=httpx.Response(200, text="<html>oops</html>"))
                content = await ds.get_folder_content(url, client)
        assert content == ds._empty_folder_content()

    async def test_pagination_sur_page_suivante(self) -> None:
        # resultNumber=2, on a 2 docs sur la page 1 et 1 doc sur la page 2.
        # → on s'arrête quand la 2e page renvoie moins de resultNumber.
        url_p1 = _folder_url(result_number=2)
        url_p2 = _folder_url(result_number=2)

        def _docs(prefix: str, n: int) -> list[dict]:
            return [
                {
                    "guid": f"{prefix}{i}",
                    "nom": f"file_{prefix}{i}.pdf",
                    "extension": ".pdf",
                    "emplacement": f"/{prefix}{i}",
                }
                for i in range(n)
            ]

        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr", assert_all_called=False) as mock:
                # Le mock respx matche l'URL complète, mais ici la 2e page a un
                # ?page=2 donc on construit l'URL complète.
                from urllib.parse import urlparse, parse_qs, urlencode
                p = urlparse(url_p1)
                qs = parse_qs(p.query)
                qs["page"] = ["2"]
                url_p2 = f"{p.scheme}://{p.netloc}{p.path}?{urlencode(qs, doseq=True)}"

                mock.get(url_p1).mock(
                    return_value=httpx.Response(200, json=_ok_payload(docs=_docs("p1", 2)))
                )
                mock.get(url_p2).mock(
                    return_value=httpx.Response(200, json=_ok_payload(docs=_docs("p2", 1)))
                )
                content = await ds.get_folder_content(url_p1, client)
        # 2 + 1 = 3 fichiers collectés sur 2 pages.
        assert len(content["files"]) == 3
        guids = [f["guid"] for f in content["files"]]
        assert guids == ["p10", "p11", "p20"]

    async def test_pagination_deduplique_les_guids(self) -> None:
        # Si l'API renvoie les mêmes GUIDs sur 2 pages consécutives, on s'arrête.
        url_p1 = _folder_url(result_number=2)
        from urllib.parse import urlparse, parse_qs, urlencode
        p = urlparse(url_p1)
        qs = parse_qs(p.query)
        qs["page"] = ["2"]
        url_p2 = f"{p.scheme}://{p.netloc}{p.path}?{urlencode(qs, doseq=True)}"

        same_docs = [
            {"guid": "G1", "nom": "a.pdf", "extension": ".pdf", "emplacement": "/1"},
            {"guid": "G2", "nom": "b.pdf", "extension": ".pdf", "emplacement": "/2"},
        ]

        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr", assert_all_called=False) as mock:
                mock.get(url_p1).mock(
                    return_value=httpx.Response(200, json=_ok_payload(docs=same_docs))
                )
                mock.get(url_p2).mock(
                    return_value=httpx.Response(200, json=_ok_payload(docs=same_docs))
                )
                content = await ds.get_folder_content(url_p1, client)
        # On a détecté les doublons et on s'est arrêté à la page 1.
        assert len(content["files"]) == 2

    async def test_pagination_arrete_si_page_renvoie_erreur(self) -> None:
        url_p1 = _folder_url(result_number=2)
        from urllib.parse import urlparse, parse_qs, urlencode
        p = urlparse(url_p1)
        qs = parse_qs(p.query)
        qs["page"] = ["2"]
        url_p2 = f"{p.scheme}://{p.netloc}{p.path}?{urlencode(qs, doseq=True)}"

        page1_docs = [
            {"guid": "G1", "nom": "a.pdf", "extension": ".pdf", "emplacement": "/1"},
            {"guid": "G2", "nom": "b.pdf", "extension": ".pdf", "emplacement": "/2"},
        ]

        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr", assert_all_called=False) as mock:
                mock.get(url_p1).mock(
                    return_value=httpx.Response(200, json=_ok_payload(docs=page1_docs))
                )
                mock.get(url_p2).mock(
                    return_value=httpx.Response(200, json={"responseCode": "500", "msg": "fail"})
                )
                content = await ds.get_folder_content(url_p1, client)
        # On a récupéré la page 1, puis on a arrêté sur l'erreur de la page 2.
        assert len(content["files"]) == 2

    async def test_pagination_arrete_si_http_non_200(self) -> None:
        url_p1 = _folder_url(result_number=2)
        from urllib.parse import urlparse, parse_qs, urlencode
        p = urlparse(url_p1)
        qs = parse_qs(p.query)
        qs["page"] = ["2"]
        url_p2 = f"{p.scheme}://{p.netloc}{p.path}?{urlencode(qs, doseq=True)}"

        page1_docs = [
            {"guid": "G1", "nom": "a.pdf", "extension": ".pdf", "emplacement": "/1"},
            {"guid": "G2", "nom": "b.pdf", "extension": ".pdf", "emplacement": "/2"},
        ]

        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr", assert_all_called=False) as mock:
                mock.get(url_p1).mock(
                    return_value=httpx.Response(200, json=_ok_payload(docs=page1_docs))
                )
                mock.get(url_p2).mock(return_value=httpx.Response(503, text="unavailable"))
                content = await ds.get_folder_content(url_p1, client)
        assert len(content["files"]) == 2

    async def test_directory_info_est_extrait(self) -> None:
        url = _folder_url()
        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr") as mock:
                mock.get(url).mock(
                    return_value=httpx.Response(
                        200,
                        json=_ok_payload(directory={"nom": "MonDossier", "cheminComplet": "/A/B"}),
                    )
                )
                content = await ds.get_folder_content(url, client)
        assert content["directory_info"] == {"nom": "MonDossier", "cheminComplet": "/A/B"}

    async def test_docs_sans_guid_ne_cassent_pas_la_pagination(self) -> None:
        # Les docs sans guid sont ajoutés mais ne participent pas à la dédup.
        url_p1 = _folder_url(result_number=2)
        from urllib.parse import urlparse, parse_qs, urlencode
        p = urlparse(url_p1)
        qs = parse_qs(p.query)
        qs["page"] = ["2"]
        url_p2 = f"{p.scheme}://{p.netloc}{p.path}?{urlencode(qs, doseq=True)}"

        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr", assert_all_called=False) as mock:
                mock.get(url_p1).mock(
                    return_value=httpx.Response(
                        200,
                        json=_ok_payload(docs=[
                            {"guid": "G1", "nom": "a", "extension": ".pdf", "emplacement": "/1"},
                            {"guid": "G2", "nom": "b", "extension": ".pdf", "emplacement": "/2"},
                        ]),
                    )
                )
                # Page 2 : 1 seul doc (pas un doublon).
                mock.get(url_p2).mock(
                    return_value=httpx.Response(
                        200,
                        json=_ok_payload(docs=[
                            {"guid": "G3", "nom": "c", "extension": ".pdf", "emplacement": "/3"},
                        ]),
                    )
                )
                content = await ds.get_folder_content(url_p1, client)
        assert len(content["files"]) == 3
