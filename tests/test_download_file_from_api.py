"""Tests de download_file_from_api — téléchargement d'un fichier individuel.

Couvre : (1) succès HTTP, (2) échec HTTP, (3) dédup via downloaded_files,
(4) dédup via filesystem (suffixe _N), (5) création du dossier destination,
(6) paramètres d'appel au webservice ged, (7) champs manquants.
"""

from __future__ import annotations

import httpx
import pytest
import respx

import download_syndic as ds


DOWNLOAD_URL = "https://extranet2.ics.fr/webservice/gedservice/getFileByFTPServlet"


def _file_info(**overrides) -> dict:
    base = {
        "guid": "GUID-1",
        "nom": "facture",
        "nomGed": "facture",
        "extension": ".pdf",
        "emplacement": "/path/to/facture.pdf",
    }
    base.update(overrides)
    return base


class TestDownloadFileFromApi:
    """Tests de download_file_from_api."""

    async def test_telechargement_reussi(self, tmp_path, tmp_env) -> None:
        folder = str(tmp_path / "dest")
        ds.enable_thread_bars = False  # évite la création de barres tqdm

        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr") as mock:
                mock.get(DOWNLOAD_URL).mock(
                    return_value=httpx.Response(
                        200,
                        content=b"PDF-CONTENT",
                        headers={"content-type": "application/pdf"},
                    )
                )
                ok = await ds.download_file_from_api(
                    _file_info(), folder, client, thread_id=None, token="TOK"
                )
        assert ok is True
        with open(f"{folder}\\facture.pdf", "rb") as f:
            assert f.read() == b"PDF-CONTENT"

    async def test_cree_le_dossier_destination_si_absent(self, tmp_path, tmp_env) -> None:
        folder = str(tmp_path / "nouveau" / "sous" / "dossier")
        ds.enable_thread_bars = False

        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr") as mock:
                mock.get(DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=b"X"))
                ok = await ds.download_file_from_api(
                    _file_info(), folder, client, thread_id=None, token="TOK"
                )
        assert ok is True
        import os
        assert os.path.isdir(folder)
        assert os.path.isfile(f"{folder}\\facture.pdf")

    async def test_dedup_via_downloaded_files_set(self, tmp_path, tmp_env) -> None:
        # Le 1er appel télécharge, le 2e (même GUID) retourne True sans appeler le réseau.
        folder = str(tmp_path / "dest")
        ds.enable_thread_bars = False
        ds.downloaded_files.add("GUID-1")  # déjà "téléchargé"

        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr", assert_all_called=False) as mock:
                route = mock.get(DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=b"X"))
                ok = await ds.download_file_from_api(
                    _file_info(), folder, client, thread_id=None, token="TOK"
                )
        assert ok is True
        assert not route.called  # aucun appel HTTP n'a été fait

    async def test_suffixe_numerique_si_fichier_existe(self, tmp_path, tmp_env) -> None:
        # Le fichier destination existe déjà → suffixe _1.
        import os
        folder = str(tmp_path / "dest")
        os.makedirs(folder, exist_ok=True)
        with open(f"{folder}\\facture.pdf", "wb") as f:
            f.write(b"OLD")
        ds.enable_thread_bars = False

        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr") as mock:
                mock.get(DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=b"NEW"))
                ok = await ds.download_file_from_api(
                    _file_info(), folder, client, thread_id=None, token="TOK"
                )
        assert ok is True
        with open(f"{folder}\\facture.pdf", "rb") as f:
            assert f.read() == b"OLD"  # l'original n'est pas écrasé
        with open(f"{folder}\\facture_1.pdf", "rb") as f:
            assert f.read() == b"NEW"

    async def test_incremente_le_suffixe_jusqu_a_trouver_un_nom_libre(self, tmp_path, tmp_env) -> None:
        import os
        folder = str(tmp_path / "dest")
        os.makedirs(folder, exist_ok=True)
        # On crée facture.pdf + facture_1.pdf + facture_2.pdf : la boucle
        # doit passer à facture_3.pdf (le 1er nom libre).
        for n in (None, 1, 2):
            name = "facture.pdf" if n is None else f"facture_{n}.pdf"
            with open(f"{folder}\\{name}", "wb") as f:
                f.write(b"OLD")
        ds.enable_thread_bars = False

        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr") as mock:
                mock.get(DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=b"NEW"))
                ok = await ds.download_file_from_api(
                    _file_info(), folder, client, thread_id=None, token="TOK"
                )
        assert ok is True
        with open(f"{folder}\\facture_3.pdf", "rb") as f:
            assert f.read() == b"NEW"

    async def test_http_non_200_ne_telecharge_pas(self, tmp_path, tmp_env, capsys) -> None:
        folder = str(tmp_path / "dest")
        ds.enable_thread_bars = False

        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr") as mock:
                mock.get(DOWNLOAD_URL).mock(return_value=httpx.Response(500, text="err"))
                ok = await ds.download_file_from_api(
                    _file_info(), folder, client, thread_id=None, token="TOK"
                )
        assert ok is False
        captured = capsys.readouterr()
        assert "500" in captured.out

    async def test_guid_manquant_echec(self, tmp_path, tmp_env, capsys) -> None:
        folder = str(tmp_path / "dest")
        ds.enable_thread_bars = False
        info = _file_info(guid=None)

        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr", assert_all_called=False) as mock:
                route = mock.get(DOWNLOAD_URL).mock(return_value=httpx.Response(200))
                ok = await ds.download_file_from_api(
                    info, folder, client, thread_id=None, token="TOK"
                )
        assert ok is False
        assert not route.called
        assert "Informations manquantes" in capsys.readouterr().out

    async def test_emplacement_manquant_echec(self, tmp_path, tmp_env, capsys) -> None:
        folder = str(tmp_path / "dest")
        ds.enable_thread_bars = False
        info = _file_info(emplacement=None)

        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr", assert_all_called=False) as mock:
                route = mock.get(DOWNLOAD_URL).mock(return_value=httpx.Response(200))
                ok = await ds.download_file_from_api(
                    info, folder, client, thread_id=None, token="TOK"
                )
        assert ok is False
        assert not route.called

    async def test_token_manquant_echec(self, tmp_path, tmp_env, capsys) -> None:
        folder = str(tmp_path / "dest")
        ds.enable_thread_bars = False

        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr", assert_all_called=False) as mock:
                route = mock.get(DOWNLOAD_URL).mock(return_value=httpx.Response(200))
                ok = await ds.download_file_from_api(
                    _file_info(), folder, client, thread_id=None, token=None
                )
        assert ok is False
        assert not route.called
        assert "token" in capsys.readouterr().out.lower()

    async def test_parametres_de_requete(self, tmp_path, tmp_env) -> None:
        folder = str(tmp_path / "dest")
        ds.enable_thread_bars = False

        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr") as mock:
                route = mock.get(DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=b"X"))
                await ds.download_file_from_api(
                    _file_info(nom="monfichier", extension=".pdf"),
                    folder,
                    client,
                    thread_id=None,
                    token="MY-TOKEN",
                )
        request = route.calls.last.request
        assert "token=MY-TOKEN" in str(request.url)
        assert "emplacement=" in str(request.url)
        assert "nomFile=monfichier" in str(request.url)
        assert "extension=.pdf" in str(request.url)

    async def test_utilise_clean_name_si_defini_sur_file_info(self, tmp_path, tmp_env) -> None:
        # _clean_name est mis en cache par collect_all_files_recursive pour
        # éviter de le recalculer. On vérifie qu'il est utilisé tel quel.
        folder = str(tmp_path / "dest")
        ds.enable_thread_bars = False

        info = _file_info()
        info["_clean_name"] = "nom_pre_calcule.pdf"

        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr") as mock:
                mock.get(DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=b"X"))
                await ds.download_file_from_api(
                    info, folder, client, thread_id=None, token="TOK"
                )
        import os
        assert os.path.isfile(f"{folder}\\nom_pre_calcule.pdf")

    async def test_exception_reseau_ne_fait_pas_crasher(self, tmp_path, tmp_env, capsys) -> None:
        folder = str(tmp_path / "dest")
        ds.enable_thread_bars = False

        class BoomTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                raise httpx.ConnectError("boom")

        async with httpx.AsyncClient(transport=BoomTransport()) as client:
            ok = await ds.download_file_from_api(
                _file_info(), folder, client, thread_id=None, token="TOK"
            )
        assert ok is False
        assert "boom" in capsys.readouterr().out

    async def test_ajoute_le_guid_a_downloaded_files(self, tmp_path, tmp_env) -> None:
        folder = str(tmp_path / "dest")
        ds.enable_thread_bars = False
        ds.downloaded_files.clear()

        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr") as mock:
                mock.get(DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=b"X"))
                await ds.download_file_from_api(
                    _file_info(guid="NEW-GUID"), folder, client, thread_id=None, token="TOK"
                )
        assert "NEW-GUID" in ds.downloaded_files
