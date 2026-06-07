"""Tests de download_files_from_list — orchestration du téléchargement parallèle.

Cette fonction : (1) filtre les fichiers déjà existants via _is_new, (2) crée
les barres tqdm globales et par-thread, (3) lance les téléchargements en
parallèle (sémaphore), (4) met à jour les compteurs. Une régression peut
provoquer des téléchargements en double ou des barres cassées.
"""

from __future__ import annotations

import httpx
import pytest
import respx

import download_syndic as ds


DOWNLOAD_URL = "https://extranet2.ics.fr/webservice/gedservice/getFileByFTPServlet"


def _file_pair(guid: str, nom: str = "x.pdf", is_new: bool = True, folder: str = "dest") -> tuple[dict, str]:
    info = {
        "guid": guid,
        "nom": nom,
        "nomGed": nom,
        "extension": ".pdf",
        "emplacement": f"/path/{nom}",
        "_clean_name": nom,
        "_is_new": is_new,
    }
    return info, folder


class TestDownloadFilesFromList:
    """Tests de download_files_from_list."""

    async def test_filtre_les_fichiers_existants(self, tmp_path, tmp_env) -> None:
        ds.enable_thread_bars = False
        ds.max_concurrent_downloads = 2

        existing_folder = str(tmp_path / "dest")
        new_folder = str(tmp_path / "new")
        ds.downloaded_files.clear()

        files = [
            _file_pair("G-EXISTING", is_new=False, folder=existing_folder),
            _file_pair("G-NEW", is_new=True, folder=new_folder),
        ]

        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr", assert_all_called=False) as mock:
                route = mock.get(DOWNLOAD_URL).mock(
                    return_value=httpx.Response(200, content=b"OK")
                )
                await ds.download_files_from_list(
                    files, client, existing_files=set(), token="T", total_existing=1
                )
        # Seul le fichier nouveau est passé au téléchargement.
        assert route.call_count == 1
        assert "G-NEW" in ds.downloaded_files
        assert "G-EXISTING" not in ds.downloaded_files

    async def test_aucun_telechargement_si_tous_existing(self, tmp_path, tmp_env) -> None:
        ds.enable_thread_bars = False
        ds.max_concurrent_downloads = 2
        files = [_file_pair("G1", is_new=False), _file_pair("G2", is_new=False)]

        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr", assert_all_called=False) as mock:
                route = mock.get(DOWNLOAD_URL).mock(
                    return_value=httpx.Response(200, content=b"OK")
                )
                await ds.download_files_from_list(
                    files, client, existing_files=set(), token="T", total_existing=2
                )
        assert route.call_count == 0

    async def test_concurrence_max_respectee(self, tmp_path, tmp_env) -> None:
        # On lance 5 téléchargements avec max_concurrent_downloads=2.
        # On s'assure qu'au plus 2 tournent en parallèle.
        import asyncio

        ds.enable_thread_bars = False
        ds.max_concurrent_downloads = 2

        in_flight = 0
        max_in_flight = 0
        lock = asyncio.Lock()

        async def delayed_response(request: httpx.Request) -> httpx.Response:
            nonlocal in_flight, max_in_flight
            async with lock:
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(0.05)
            async with lock:
                in_flight -= 1
            return httpx.Response(200, content=b"OK")

        folder = str(tmp_path / "dest")
        files = [_file_pair(f"G{i}", folder=folder) for i in range(5)]

        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr", assert_all_called=False) as mock:
                mock.get(DOWNLOAD_URL).mock(side_effect=delayed_response)
                await ds.download_files_from_list(
                    files, client, existing_files=set(), token="T", total_existing=0
                )
        assert max_in_flight <= 2
        assert ds.downloaded_files == {f"G{i}" for i in range(5)}

    async def test_thread_counter_incremente(self, tmp_path, tmp_env) -> None:
        ds.enable_thread_bars = False
        ds.max_concurrent_downloads = 3
        ds.thread_counter = 0
        folder = str(tmp_path / "dest")
        files = [_file_pair(f"G{i}", folder=folder) for i in range(3)]

        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr", assert_all_called=False) as mock:
                mock.get(DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=b"OK"))
                await ds.download_files_from_list(
                    files, client, existing_files=set(), token="T", total_existing=0
                )
        assert ds.thread_counter == 3

    async def test_reinitialise_les_globales(self, tmp_path, tmp_env) -> None:
        # Avant l'appel, on pollue les globales ; après, elles doivent être
        # dans un état "neutre" (counter=N fichiers, downloaded_files=set de la session).
        ds.enable_thread_bars = False
        ds.max_concurrent_downloads = 2
        ds.downloaded_files.add("POLLUTING")
        ds.thread_counter = 99
        ds.thread_progress_bars = {99: "fake"}  # type: ignore[assignment]

        folder = str(tmp_path / "dest")
        files = [_file_pair("G1", folder=folder)]

        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr") as mock:
                mock.get(DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=b"OK"))
                await ds.download_files_from_list(
                    files, client, existing_files=set(), token="T", total_existing=0
                )
        # downloaded_files a été clear() puis rempli avec le GUID courant.
        assert ds.downloaded_files == {"G1"}
        # La barre de progression globale a été fermée (réassignée à None).
        assert ds.progress_bar_total is None
        # Les barres de thread sont nettoyées.
        assert ds.thread_progress_bars == {}
        # thread_counter a été reset à 0 puis incrémenté pour chaque fichier : 1.
        assert ds.thread_counter == 1

    async def test_echec_de_telechargement_compte_rien(self, tmp_path, tmp_env) -> None:
        # Un téléchargement qui échoue (HTTP 500) ne doit pas faire passer
        # le fichier dans downloaded_files (sinon les retries ultérieurs
        # court-circuiteraient).
        ds.enable_thread_bars = False
        ds.max_concurrent_downloads = 1
        folder = str(tmp_path / "dest")
        files = [_file_pair("FAIL", folder=folder)]

        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr") as mock:
                mock.get(DOWNLOAD_URL).mock(return_value=httpx.Response(500))
                await ds.download_files_from_list(
                    files, client, existing_files=set(), token="T", total_existing=0
                )
        assert ds.downloaded_files == set()

    async def test_enable_thread_bars_false_pas_de_barres(self, tmp_path, tmp_env) -> None:
        ds.enable_thread_bars = False
        ds.max_concurrent_downloads = 1
        folder = str(tmp_path / "dest")
        files = [_file_pair("G1", folder=folder)]

        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr") as mock:
                mock.get(DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=b"OK"))
                await ds.download_files_from_list(
                    files, client, existing_files=set(), token="T", total_existing=0
                )
        # Aucune barre de thread n'est créée.
        assert ds.thread_progress_bars == {}

    async def test_max_concurrent_par_defaut(self) -> None:
        # Le code déclare max_concurrent_downloads = 10 — on le verrouille.
        assert ds.max_concurrent_downloads == 10
