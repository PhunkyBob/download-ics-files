"""Fixtures partagés pour la suite de tests de download_syndic.

Le module testé utilise plusieurs globales au niveau du module (downloaded_files,
progress_bar_total, thread_progress_bars, etc.) qu'il faut isoler entre tests pour
éviter les fuites d'état. Les fixtures `module_globals` et `tmp_env` répondent à
ce besoin.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest


# Liste des noms de globales à réinitialiser entre tests qui touchent au module.
# Toute nouvelle globale d'état ajoutée à download_syndic.py doit être listée ici.
_MODULE_GLOBALS = (
    "downloaded_files",
    "progress_bar_total",
    "thread_progress_bars",
    "thread_counter",
    "max_concurrent_downloads",
    "enable_thread_bars",
    "HTTP_TIMEOUT",
    "HTTP_MAX_RETRIES",
    "download_folder",
    "base_url",
)


@pytest.fixture(autouse=True)
def module_globals() -> Iterator[None]:
    """Sauvegarde puis restaure toutes les globales d'état du module testé.

    Le code de production mute `downloaded_files`, `thread_progress_bars`, etc. en
    cours d'exécution ; sans cette fixture, un test qui télécharge un fichier
    laisse son GUID dans le set et pollue les tests suivants.

    On capture un snapshot en début de test, puis on **nettoie aussi** l'état
    mutable au début — sinon un test qui ajoute un GUID à `downloaded_files`
    fait court-circuiter le test suivant qui partage le même guid par défaut.
    """
    import download_syndic as ds

    snapshot: dict[str, object] = {name: getattr(ds, name) for name in _MODULE_GLOBALS}

    # Reset explicite de l'état mutable. On préserve le type (set/dict) d'origine
    # pour ne pas casser les annotations de type et les usages en aval.
    ds.downloaded_files.clear()
    ds.thread_progress_bars.clear()
    ds.progress_bar_total = None
    ds.thread_counter = 0

    try:
        yield
    finally:
        for name, value in snapshot.items():
            setattr(ds, name, value)


@pytest.fixture(autouse=True)
def reset_respx() -> Iterator[None]:
    """Réinitialise le routeur global de respx entre tests.

    respx 0.23 utilise un routeur global qui accumule les routes de tous les
    tests, ce qui fait planter les tests suivants avec
    "AllMockedAssertionError: some routes were not called" si les URLs
    mockées d'un test précédent ne sont jamais revisitées.
    """
    try:
        import respx as _respx

        _respx.routes.clear()  # type: ignore[attr-defined]
    except (ImportError, AttributeError):
        # respx non installé ou API changée : on n'insiste pas, les tests
        # qui en ont besoin fourniront leur propre protection
        # (assert_all_called=False).
        pass
    yield
    try:
        import respx as _respx

        _respx.routes.clear()  # type: ignore[attr-defined]
    except (ImportError, AttributeError):
        pass


@pytest.fixture
def tmp_env(tmp_path: pytest.TempPathFactory) -> Iterator[None]:
    """Redirige DOWNLOAD_FOLDER vers un dossier temporaire pour chaque test.

    Plusieurs fonctions du module lisent `download_folder` au moment de
    l'appel (et non à l'import), il suffit donc de le réassigner dans
    `download_syndic`. On restaure l'original ensuite via la fixture
    `module_globals` qui capture la valeur d'import.
    """
    import download_syndic as ds

    target = tmp_path / "downloads"
    target.mkdir()
    ds.download_folder = str(target)
    yield
