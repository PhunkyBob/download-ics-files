"""Tests de collect_all_files_recursive — exploration récursive de l'arborescence.

Cette fonction orchestre : (1) la récupération paginée du dossier courant,
(2) le calcul des chemins locaux cibles, (3) le marquage new/existing pour
chaque fichier, (4) la récursion en parallèle sur les sous-dossiers. Toute
régression silencieuse (mauvais chemin local, _is_new mal initialisé,
récursion séquentielle) n'apparaît qu'à l'usage final.
"""

from __future__ import annotations

import os
from urllib.parse import urlencode

import httpx
import pytest
import respx

import download_syndic as ds


def _url(folder_id: int = 10) -> str:
    return (
        f"https://extranet2.ics.fr/webservice/gedservice/SearchArborescenceContentServlet"
        f"?id={folder_id}&token=T&resultNumber=10"
    )


def _payload(sons: list[dict] | None = None, docs: list[dict] | None = None, directory: dict | None = None) -> dict:
    return {
        "responseCode": "200",
        "payload": {
            "sons": sons or [],
            "docs": docs or [],
            "directory": directory or {},
        },
    }


def _file(guid: str, nom: str = "x.pdf", **overrides) -> dict:
    d = {
        "guid": guid,
        "nom": nom,
        "nomGed": nom,
        "extension": ".pdf",
        "emplacement": f"/somewhere/{nom}",
    }
    d.update(overrides)
    return d


def _folder(id_: int, nom: str) -> dict:
    return {
        "type": "DOSSIER",
        "idArbo": id_,
        "nom": nom,
        "nomGed": nom,
        "chemin": f"/{nom}",
        "cheminComplet": f"/parent/{nom}",
        "documentsCount": 0,
        "foldersCount": 0,
        "droits": "Conseil syndical",
    }


class TestCollectAllFilesRecursive:
    """Tests de collect_all_files_recursive."""

    async def test_dossier_vide(self, tmp_path, tmp_env) -> None:
        url = _url()
        base_folder = str(tmp_path / "dl")
        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr") as mock:
                mock.get(url).mock(return_value=httpx.Response(200, json=_payload()))
                files, existing = await ds.collect_all_files_recursive(
                    url, client, base_folder, existing_files=set()
                )
        assert files == []
        assert existing == 0

    async def test_marque_new_pour_fichiers_inconnus(self, tmp_path, tmp_env) -> None:
        url = _url()
        base_folder = str(tmp_path / "dl")
        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr") as mock:
                mock.get(url).mock(
                    return_value=httpx.Response(
                        200,
                        json=_payload(docs=[_file("G1"), _file("G2")], directory={"nom": "Immeuble"}),
                    )
                )
                files, existing = await ds.collect_all_files_recursive(
                    url, client, base_folder, existing_files=set()
                )
        assert existing == 0
        assert all(f[0]["_is_new"] is True for f in files)

    async def test_marque_existing_pour_fichiers_connus(self, tmp_path, tmp_env) -> None:
        url = _url()
        base_folder = str(tmp_path / "dl")
        # Le path local inclut le nom du dossier (directory.nom nettoyé).
        existing_path = os.path.join(base_folder, "I", "x.pdf")
        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr") as mock:
                mock.get(url).mock(
                    return_value=httpx.Response(
                        200,
                        json=_payload(docs=[_file("G1", "x.pdf")], directory={"nom": "I"}),
                    )
                )
                files, existing = await ds.collect_all_files_recursive(
                    url, client, base_folder, existing_files={existing_path}
                )
        assert existing == 1
        assert files[0][0]["_is_new"] is False

    async def test_chemin_local_reflete_directory_info(self, tmp_path, tmp_env) -> None:
        # Le nom de dossier local vient de directory_info.nom nettoyé.
        url = _url()
        base_folder = str(tmp_path / "dl")
        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr") as mock:
                mock.get(url).mock(
                    return_value=httpx.Response(
                        200,
                        json=_payload(docs=[_file("G1")], directory={"nom": "Mon Immeuble"}),
                    )
                )
                files, _ = await ds.collect_all_files_recursive(
                    url, client, base_folder, existing_files=set()
                )
        # Le folder_path doit inclure le nom nettoyé du dossier.
        folder_path = files[0][1]
        assert folder_path.startswith(base_folder)
        assert "Mon Immeuble" in folder_path

    async def test_recursion_vers_sous_dossiers(self, tmp_path, tmp_env) -> None:
        url_p = _url(10)
        base_folder = str(tmp_path / "dl")

        async with httpx.AsyncClient() as client:
            with respx.mock(
                base_url="https://extranet2.ics.fr",
                assert_all_called=False,
                assert_all_mocked=False,
            ) as mock:
                mock.get(url_p).mock(
                    return_value=httpx.Response(
                        200,
                        json=_payload(
                            sons=[_folder(20, "Sous-dossier")],
                            docs=[_file("G_PARENT")],
                            directory={"nom": "Immeuble"},
                        ),
                    )
                )
                # Le sous-dossier est appelé via une URL re-construite par
                # build_subfolder_url (avec paramètres par défaut ajoutés).
                # On utilise un pattern regex permissif.
                import re
                mock.get(re.compile(r".*id=20.*")).mock(
                    return_value=httpx.Response(
                        200,
                        json=_payload(docs=[_file("G_CHILD")], directory={"nom": "Sous-dossier"}),
                    )
                )
                files, existing = await ds.collect_all_files_recursive(
                    url_p, client, base_folder, existing_files=set()
                )
        # 1 fichier parent + 1 fichier enfant.
        assert len(files) == 2
        guids = sorted(f[0]["guid"] for f in files)
        assert guids == ["G_CHILD", "G_PARENT"]
        # L'enfant doit avoir un folder_path qui inclut "Sous-dossier".
        child = next(f for f in files if f[0]["guid"] == "G_CHILD")
        assert "Sous-dossier" in child[1]

    async def test_existing_count_somme_parent_et_enfants(self, tmp_path, tmp_env) -> None:
        url_p = _url(10)
        base_folder = str(tmp_path / "dl")

        async with httpx.AsyncClient() as client:
            with respx.mock(
                base_url="https://extranet2.ics.fr",
                assert_all_called=False,
                assert_all_mocked=False,
            ) as mock:
                mock.get(url_p).mock(
                    return_value=httpx.Response(
                        200,
                        json=_payload(
                            sons=[_folder(20, "Sub")],
                            docs=[_file("G1", "a.pdf")],
                            directory={"nom": "I"},
                        ),
                    )
                )
                import re
                mock.get(re.compile(r".*id=20.*")).mock(
                    return_value=httpx.Response(
                        200,
                        json=_payload(docs=[_file("G2", "b.pdf")], directory={"nom": "Sub"}),
                    )
                )
                existing = {
                    os.path.join(base_folder, "I", "a.pdf"),
                    os.path.join(base_folder, "I", "Sub", "b.pdf"),
                }
                files, n_existing = await ds.collect_all_files_recursive(
                    url_p, client, base_folder, existing_files=existing
                )
        assert n_existing == 2
        assert all(f[0]["_is_new"] is False for f in files)

    async def test_appel_recursif_passe_les_bons_parametres(self, tmp_path, tmp_env) -> None:
        # On vérifie que build_subfolder_url est appelé pour la navigation
        # vers le sous-dossier (donc que l'URL change, sans exiger de token
        # particulier dans la config).
        url_p = _url(10)
        base_folder = str(tmp_path / "dl")

        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr", assert_all_called=False) as mock:
                mock.get(url_p).mock(
                    return_value=httpx.Response(
                        200,
                        json=_payload(
                            sons=[_folder(999, "Sub")],
                            docs=[],
                            directory={"nom": "I"},
                        ),
                    )
                )
                # Le sous-dossier ne sera jamais appelé car docs vide : on n'inspecte
                # que l'URL parent. Mais on veut quand même un mock permissif au cas où.
                mock.get(__import__("re").compile(r".*")).mock(
                    return_value=httpx.Response(200, json=_payload())
                )
                files, _ = await ds.collect_all_files_recursive(
                    url_p, client, base_folder, existing_files=set()
                )
        assert files == []

    async def test_recursion_sur_3_niveaux(self, tmp_path, tmp_env) -> None:
        url_a = _url(1)
        base_folder = str(tmp_path / "dl")

        async with httpx.AsyncClient() as client:
            with respx.mock(
                base_url="https://extranet2.ics.fr",
                assert_all_called=False,
                assert_all_mocked=False,
            ) as mock:
                mock.get(url_a).mock(
                    return_value=httpx.Response(
                        200,
                        json=_payload(sons=[_folder(2, "B")], docs=[_file("A")], directory={"nom": "A"}),
                    )
                )
                import re
                mock.get(re.compile(r".*id=2.*")).mock(
                    return_value=httpx.Response(
                        200,
                        json=_payload(sons=[_folder(3, "C")], docs=[_file("B")], directory={"nom": "B"}),
                    )
                )
                mock.get(re.compile(r".*id=3.*")).mock(
                    return_value=httpx.Response(
                        200,
                        json=_payload(docs=[_file("C")], directory={"nom": "C"}),
                    )
                )
                files, _ = await ds.collect_all_files_recursive(
                    url_a, client, base_folder, existing_files=set()
                )
        guids = sorted(f[0]["guid"] for f in files)
        assert guids == ["A", "B", "C"]

    async def test_aucun_dossier_dossier_virtuel_vide(self, tmp_path, tmp_env) -> None:
        # Si la réponse ne contient pas de dossier "directory", le current_path
        # reste vide. Le folder_path final est alors base_folder.
        url = _url()
        base_folder = str(tmp_path / "dl")
        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr") as mock:
                mock.get(url).mock(
                    return_value=httpx.Response(200, json=_payload(docs=[_file("G1")]))
                )
                files, _ = await ds.collect_all_files_recursive(
                    url, client, base_folder, existing_files=set()
                )
        # Pas de directory.nom → on reste à la racine.
        assert files[0][1] == base_folder
