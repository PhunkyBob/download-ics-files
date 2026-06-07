"""Tests de run_download — confirmation utilisateur avant téléchargement.

C'est le point de contrôle humain final : si la confirmation est refusée,
aucun fichier n'est téléchargé. Une régression ici pourrait soit
télécharger sans demander, soit bloquer indéfiniment.
"""

from __future__ import annotations

import httpx
import pytest
import respx

import download_syndic as ds


DOWNLOAD_URL = "https://extranet2.ics.fr/webservice/gedservice/getFileByFTPServlet"


def _file_pair(guid: str, nom: str = "x.pdf", folder: str = "dest") -> tuple[dict, str]:
    return (
        {
            "guid": guid,
            "nom": nom,
            "nomGed": nom,
            "extension": ".pdf",
            "emplacement": f"/{nom}",
            "_clean_name": nom,
            "_is_new": True,
        },
        folder,
    )


class TestRunDownload:
    """Tests de run_download."""

    async def test_confirmation_refusee_pas_de_telechargement(
        self, tmp_path, tmp_env, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        async def fake_confirm(message, default=False):
            return False  # l'utilisateur refuse

        monkeypatch.setattr(ds, "_confirm", fake_confirm)
        ds.downloaded_files.clear()

        folder = str(tmp_path / "dest")
        files = [_file_pair("G1", folder=folder)]

        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr", assert_all_called=False) as mock:
                route = mock.get(DOWNLOAD_URL).mock(
                    return_value=httpx.Response(200, content=b"X")
                )
                await ds.run_download(
                    files, set(), "T", folder, client, total_existing=0
                )
        # Aucun appel HTTP, aucun fichier marqué downloaded.
        assert not route.called
        assert ds.downloaded_files == set()
        assert "annulé" in capsys.readouterr().out.lower()

    async def test_confirmation_acceptee_lance_le_telechargement(
        self, tmp_path, tmp_env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_confirm(message, default=False):
            return True

        monkeypatch.setattr(ds, "_confirm", fake_confirm)
        ds.enable_thread_bars = False
        ds.downloaded_files.clear()

        folder = str(tmp_path / "dest")
        files = [_file_pair("G1", folder=folder)]

        async with httpx.AsyncClient() as client:
            with respx.mock(base_url="https://extranet2.ics.fr") as mock:
                mock.get(DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=b"OK"))
                await ds.run_download(
                    files, set(), "T", folder, client, total_existing=0
                )
        # Le fichier a bien été téléchargé.
        assert "G1" in ds.downloaded_files
