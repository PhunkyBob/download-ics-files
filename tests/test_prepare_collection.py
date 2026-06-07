"""Tests de prepare_collection — préparation unifiée de la collecte.

Cette fonction enchaîne : (1) la collecte récursive, (2) le filtrage par
date, (3) le recalcul de `total_existing` après filtrage. Une régression
peut fausser la barre de progression totale.
"""

from __future__ import annotations

import os
from urllib.parse import urlencode

import httpx
import pytest
import respx

import download_syndic as ds


def _url() -> str:
    return "https://extranet2.ics.fr/webservice/gedservice/GetEntityContentServlet?id=1&token=T"


def _payload(docs: list[dict], directory: dict | None = None) -> dict:
    return {
        "responseCode": "200",
        "payload": {"sons": [], "docs": docs, "directory": directory or {}},
    }


def _file(guid: str, nom: str = "x.pdf", date: str = "2024-01-15") -> dict:
    return {
        "guid": guid,
        "nom": nom,
        "nomGed": nom,
        "extension": ".pdf",
        "emplacement": f"/{nom}",
        "dateCreated": date,
    }


class TestPrepareCollection:
    """Tests de prepare_collection."""

    async def test_sans_start_date_ne_filtre_pas(self, tmp_path, tmp_env) -> None:
        url = _url()
        base_folder = str(tmp_path / "dl")
        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr") as mock:
                mock.get(url).mock(
                    return_value=httpx.Response(
                        200,
                        json=_payload(docs=[_file("G1", "a.pdf"), _file("G2", "b.pdf")], directory={"nom": "I"}),
                    )
                )
                files, existing, total = await ds.prepare_collection(
                    url, base_folder, None, client, set()
                )
        assert len(files) == 2
        assert total == 0

    async def test_avec_start_date_filtre(self, tmp_path, tmp_env) -> None:
        url = _url()
        base_folder = str(tmp_path / "dl")
        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr") as mock:
                mock.get(url).mock(
                    return_value=httpx.Response(
                        200,
                        json=_payload(
                            docs=[
                                _file("G-OLD", "old.pdf", date="2023-01-01"),
                                _file("G-NEW", "new.pdf", date="2024-12-01"),
                            ],
                            directory={"nom": "I"},
                        ),
                    )
                )
                files, _, total = await ds.prepare_collection(
                    url, base_folder, "2024-06", client, set()
                )
        guids = sorted(f[0]["guid"] for f in files)
        assert guids == ["G-NEW"]
        assert total == 0

    async def test_recalcule_total_existing_apres_filtrage(self, tmp_path, tmp_env) -> None:
        # On a 2 fichiers existants : 1 avant la date, 1 après.
        # Après filtrage, seul le récent reste → total_existing doit être recalculé.
        url = _url()
        base_folder = str(tmp_path / "dl")
        # Le path local inclut le nom du dossier (directory.nom nettoyé).
        existing = {
            os.path.join(base_folder, "I", "old.pdf"),
            os.path.join(base_folder, "I", "new.pdf"),
        }
        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr") as mock:
                mock.get(url).mock(
                    return_value=httpx.Response(
                        200,
                        json=_payload(
                            docs=[
                                _file("G-OLD", "old.pdf", date="2023-01-01"),
                                _file("G-NEW", "new.pdf", date="2024-12-01"),
                            ],
                            directory={"nom": "I"},
                        ),
                    )
                )
                files, _, total = await ds.prepare_collection(
                    url, base_folder, "2024-06", client, existing
                )
        # Seul new.pdf reste après filtrage, et il est dans existing → 1.
        assert total == 1
        assert files[0][0]["guid"] == "G-NEW"

    async def test_retourne_existing_files_set(self, tmp_path, tmp_env) -> None:
        # Le set d'existing est retourné tel quel (référence, pas copie).
        url = _url()
        base_folder = str(tmp_path / "dl")
        sentinel = {"foo", "bar"}
        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr") as mock:
                mock.get(url).mock(
                    return_value=httpx.Response(200, json=_payload(docs=[], directory={"nom": "I"}))
                )
                _, existing, _ = await ds.prepare_collection(
                    url, base_folder, None, client, sentinel
                )
        assert existing is sentinel
