import argparse
import asyncio
import contextlib
import json
import os
import re
import sys
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any, TypeAlias, TypedDict, cast
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import bs4
import httpx
import questionary
from dotenv import load_dotenv
from questionary import Choice
from tqdm import tqdm

load_dotenv()

FileInfo: TypeAlias = dict[str, Any]
Folder: TypeAlias = dict[str, Any]
DirectoryInfo: TypeAlias = dict[str, Any]
Property: TypeAlias = dict[str, str]
PropertyDetails: TypeAlias = dict[str, str]
FilePair: TypeAlias = tuple[FileInfo, str]

base_url: str = "https://extranet2.ics.fr/V5/"
download_folder: str = os.getenv("DOWNLOAD_FOLDER", "DOWNLOADS")

# Timeout configurable via env (défaut 60s, plus généreux que 30s pour Linux)
HTTP_TIMEOUT: float = float(os.getenv("HTTP_TIMEOUT", "60"))
HTTP_MAX_RETRIES: int = int(os.getenv("HTTP_MAX_RETRIES", "3"))

DOWNLOAD_URL: str = "https://extranet2.ics.fr/webservice/gedservice/getFileByFTPServlet"

downloaded_files: set[str] = set()
progress_bar_total: tqdm | None = None
thread_progress_bars: dict[int, tqdm] = {}
max_concurrent_downloads: int = 10
thread_counter: int = 0
enable_thread_bars: bool = True

# Deux variantes : appels API JSON et téléchargement de fichiers
API_HEADERS: dict[str, str] = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:98.0) Gecko/20100101 Firefox/98.0",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://extranet2.ics.fr/V5/",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

DOWNLOAD_HEADERS: dict[str, str] = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:98.0) Gecko/20100101 Firefox/98.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://extranet2.ics.fr/V5/",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Pragma": "no-cache",
    "Cache-Control": "no-cache",
}


def build_search_url(token: str, folder_id: int | str, page: int = 1) -> str:
    """
    Construit une URL SearchArborescenceContentServlet avec des paramètres individuels.

    Args:
        token: Token d'authentification
        folder_id: ID du dossier (idArbo)
        page: Numéro de page
    """
    params: dict[str, str] = {
        "cabinet": "false",
        "droits": "Conseil syndical",
        "id": str(folder_id),
        "page": str(page),
        "resultNumber": "10",
        "sortName": "DESCENDING_DATE",
        "toJson": "true",
        "token": token,
    }
    query = urlencode(params)
    return f"https://extranet2.ics.fr/webservice/gedservice/SearchArborescenceContentServlet?{query}"


def build_ged_url(servlet: str, token: str, **params: Any) -> str:
    """
    Construit une URL vers un webservice ged/ics avec les paramètres standardisés.

    Args:
        servlet: Nom du servlet (ex: "SearchArborescenceContentServlet")
        token: Token d'authentification
        **params: Paramètres additionnels (id, page, type, etc.)
    """
    base: dict[str, str] = {
        "cabinet": "false",
        "droits": "Conseil syndical",
        "page": "1",
        "resultNumber": "10",
        "sortName": "DESCENDING_DATE",
        "toJson": "true",
        "token": token,
    } | {k: str(v) for k, v in params.items() if v is not None}
    return f"https://extranet2.ics.fr/webservice/gedservice/{servlet}?{urlencode(base)}"


def build_entity_url(
    token: str, imme: str, copro: str, page: int = 1, use_copro_filter: bool = False
) -> str:
    """
    Construit une URL GetEntityContentServlet pour l'entité Immeuble.

    Le filtre subType=COPROPRIETAIRE/subTypeId=<copro> n'est nécessaire QUE
    pour la vue "VOS DOCUMENTS" (cf. les 2 curls fonctionnels fournis par
    l'utilisateur). Pour la vue "DOCUMENTS DE L'IMMEUBLE", ajouter ce filtre
    provoque un HTTP 500 — l'API ne sait pas gérer cette combinaison pour
    cette vue-là.

    Args:
        token: Token d'authentification
        imme: ID de l'immeuble
        copro: ID de la copropriétaire
        page: Numéro de page
        use_copro_filter: True pour ajouter le filtre subType/subTypeId
            (vue "VOS DOCUMENTS"), False pour l'omettre (vue "DOCUMENTS DE L'IMMEUBLE")
    """
    extra: dict[str, str] = (
        {"subType": "COPROPRIETAIRE", "subTypeId": copro}
        if use_copro_filter
        else {}
    )
    return build_ged_url(
        "GetEntityContentServlet",
        token,
        id=imme,
        isPermissionFilterEnabled="true",
        type="Immeuble",
        page=str(page),
        **extra
    )


def clean_file_name(file_name: str) -> str:
    file_name = re.sub(" +", " ", file_name)
    file_name = re.sub(r"[^A-Za-z0-9 _.-]", "_", file_name)
    return file_name.strip()


def construct_file_name(file_info: FileInfo) -> str:
    """
    Construit le nom de fichier tel qu'il sera sauvegardé, en utilisant la même logique
    que dans download_file_from_api.

    Args:
        file_info: Informations du fichier depuis l'API
    """
    file_name = file_info.get("nom", "unknown")
    extension = file_info.get("extension", "")
    nom_ged = file_info.get("nomGed", "")

    clean_name = clean_file_name(file_name)

    # Ajoute le nomGed entre parenthèses s'il est différent du nom principal
    if nom_ged and nom_ged != file_name:
        clean_nom_ged = clean_file_name(nom_ged)
        # Retire l'extension du nom principal pour insérer le nomGed
        name_without_ext = clean_name
        if extension and clean_name.endswith(extension):
            name_without_ext = clean_name[: -len(extension)]
        clean_name = f"{name_without_ext} ({clean_nom_ged}){extension}"
    elif not clean_name.endswith(extension) and extension:
        clean_name = clean_name + extension

    return clean_name.strip()


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else f"{s[:n - 3]}..."


def _empty_folder_content() -> dict[str, Any]:
    return {"folders": [], "files": [], "directory_info": {}}


async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    label: str | None = None,
    **kwargs: Any,
) -> httpx.Response:
    """
    GET/POST avec retry exponentiel sur timeouts (surtout utile sous Linux).
    Lève la dernière exception si toutes les tentatives échouent.
    """
    last_exception: Exception | None = None
    for attempt in range(HTTP_MAX_RETRIES):
        try:
            return await client.request(method, url, **kwargs)
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.PoolTimeout) as e:
            last_exception = e
            tag = label or url[:80]
            if attempt < HTTP_MAX_RETRIES - 1:
                wait = 2 ** (attempt + 1)
                print(f"⏳ Timeout sur {tag}... (tentative {attempt+1}/{HTTP_MAX_RETRIES}), retry dans {wait}s")
                await asyncio.sleep(wait)
            else:
                print(f"❌ Timeout définitif après {HTTP_MAX_RETRIES} tentatives pour {tag}...")
                raise last_exception from last_exception
    raise RuntimeError("unreachable")  # pragma: no cover


def scan_existing_files(base_folder: str) -> set[str]:
    """
    Scanne récursivement le dossier de téléchargement pour créer un mapping
    des fichiers existants par nom et taille.

    Args:
        base_folder: Chemin du dossier racine à scanner
    """
    existing_files: set[str] = set()

    if not os.path.isdir(base_folder):
        # Repr() expose les espaces / caractères invisibles qui font souvent
        # dériver le chemin (cf. .env avec un trailing space). On ne peut pas
        # distinguer ici "dossier inexistant" de "le chemin pointe vers un
        # fichier", mais les deux cas produisent le même symptôme (scan vide
        # → tout marqué nouveau), donc un seul avertissement suffit.
        print(
            f"⚠️  Dossier de téléchargement introuvable : {base_folder!r}\n"
            f"   Tous les fichiers distants seront marqués comme nouveaux.\n"
            f"   Vérifiez DOWNLOAD_FOLDER dans .env (espaces, casse, fautes de frappe)."
        )
        return existing_files

    print(f"🔍 Scan des fichiers existants dans {base_folder}...")

    total_files = 0
    for root, _dirs, files in os.walk(base_folder):
        for file in files:
            total_files += 1
            file_path = os.path.join(root, file)
            try:
                existing_files.add(file_path)
            except OSError:
                continue

    print(f"✅ Scan terminé: {total_files} fichiers existants trouvés")
    return existing_files


def _extract_file_info(doc: dict[str, Any]) -> FileInfo:
    return {
        "guid": doc.get("guid"),
        "nom": doc.get("nom"),
        "nomGed": doc.get("nomGed"),
        "dateUpload": doc.get("dateUpload"),
        "extension": doc.get("extension"),
        "size": doc.get("size"),
        "emplacement": doc.get("emplacement"),
        "arborescence": doc.get("arborescence"),
        "droits": doc.get("droits"),
        "dateCreated": doc.get("dateCreated"),
        "source": doc.get("source"),
    }


def _update_thread_bar(thread_id: int | None, message: str) -> None:
    if not enable_thread_bars or thread_id is None or thread_id not in thread_progress_bars:
        return
    with contextlib.suppress(Exception):
        thread_progress_bars[thread_id].bar_format = message
        thread_progress_bars[thread_id].refresh()


def build_subfolder_url(parent_url: str, folder_id: str, page: int = 1, preserve_path: bool = False) -> str:
    """
    Construit l'URL d'un sous-dossier ou d'une page suivante.

    `preserve_path=True` : on conserve le path/query du parent — utilisé pour
    paginer dans le même contexte (VOS ou IMMEUBLE), indispensable pour la
    vue VOS où l'API perd le contexte copro sans subType/subTypeId.

    `preserve_path=False` (défaut) : on bascule sur
    `SearchArborescenceContentServlet` (GetEntityContentServlet ne gère que
    les idArbo d'entité et fait 500 sur les sous-dossiers), en reportant
    éventuellement subType/subTypeId du parent.
    """
    parsed = urlparse(parent_url)
    params = parse_qs(parsed.query)
    token = params.get("token", [None])[0]

    if not token or preserve_path:
        # Pagination (ou fallback token manquant) : on hérite du path/params
        # du parent, on change juste l'id et la page.
        new_params = dict(params)
        new_params["id"] = [folder_id]
        new_params["page"] = [str(page)]
        return urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                urlencode(new_params, doseq=True),
                parsed.fragment,
            )
        )

    # Navigation sous-dossier : on reporte subType/subTypeId du parent si présents.
    extra = {k: params[k][0] for k in ("subType", "subTypeId") if k in params}
    return build_ged_url(
        "SearchArborescenceContentServlet",
        token,
        id=folder_id,
        page=str(page),
        **extra
    )


async def get_entity_url_with_fallback(
    client: httpx.AsyncClient, vos_url: str, imm_url: str, label: str = ""
) -> tuple[str, str]:
    """
    Détermine l'URL d'entité qui marche (VOS ou IMMEUBLE).

    L'API renvoie `responseCode == "500"` quand subType=COPROPRIETAIRE est
    utilisé pour la vue "DOCUMENTS DE L'IMMEUBLE" (combinaison non supportée
    pour cette vue). On exploite cette propriété : essai d'abord avec
    subType, fallback sans subType si 500.

    Returns:
        (url_qui_marche, vue) où vue est "VOS" | "IMMEUBLE".
    """

    async def _get_json(url: str) -> dict[str, Any]:
        r = await request_with_retry(client, "GET", url, headers=API_HEADERS, label=label)
        try:
            return r.json()
        except json.JSONDecodeError:
            return {}

    def _log(msg: str) -> None:
        if label:
            print(f"   🔁 {label}: {msg}")

    data = await _get_json(vos_url)
    if data.get("responseCode") == "500":
        _log("HTTP 500 avec subType → retry sans (vue IMMEUBLE)")
        await _get_json(imm_url)  # déclenche le retry si besoin, résultat ignoré
        return imm_url, "IMMEUBLE"

    return vos_url, "VOS"


async def get_folder_content(folder_url: str, client: httpx.AsyncClient) -> dict[str, Any]:
    """
    Récupère le contenu d'un dossier à partir d'une URL de dossier.
    Supporte les URLs avec paramètres individuels ou avec paramètre 'request' JSON.

    Args:
        folder_url: L'URL du dossier à explorer
        client: Client HTTP asynchrone
    """
    folders: list[Folder] = []
    all_files: list[FileInfo] = []

    parsed_url = urlparse(folder_url)
    params = parse_qs(parsed_url.query)
    result_number = int(params.get("resultNumber", [10])[0])
    folder_id = cast(str, params.get("id", [None])[0])

    response = await request_with_retry(client, "GET", folder_url, headers=API_HEADERS)

    if response.status_code != 200:
        print(f"Erreur lors de la récupération du contenu: {response.status_code}")
        return _empty_folder_content()

    try:
        data = response.json()

        if data.get("responseCode") != "200":
            # Affiche le maximum d'info pour permettre le diagnostic côté appelant :
            # responseCode + msg + URL + payload complet (le msg est souvent vide).
            code = data.get("responseCode")
            msg = data.get("msg")
            print(f"❌ Erreur API (responseCode={code!r}, msg={msg!r})")
            print(f"   URL: {folder_url}")
            print(f"   Réponse complète: {data}")
            return _empty_folder_content()

        payload = data.get("payload", {})

        sons = payload.get("sons", [])
        folders.extend(
            {
                "id": son.get("idArbo"),
                "nom": son.get("nom"),
                "nomGed": son.get("nomGed"),
                "chemin": son.get("chemin"),
                "cheminComplet": son.get("cheminComplet"),
                "documentsCount": son.get("documentsCount", 0),
                "foldersCount": son.get("foldersCount", 0),
                "droits": son.get("droits"),
            }
            for son in sons
            if son.get("type") == "DOSSIER"
        )
        docs = payload.get("docs", [])
        all_files.extend(_extract_file_info(doc) for doc in docs)
        directory_info: DirectoryInfo = payload.get("directory", {})

        # Continue tant que le nombre de documents retournés égale resultNumber
        current_page = 1
        documents_on_current_page = len(docs)

        # Garde une trace des GUIDs déjà vus pour détecter les doublons
        seen_guids: set[str] = {doc.get("guid") for doc in docs if doc.get("guid")}

        while documents_on_current_page == result_number:
            current_page += 1
            # Pagination du dossier courant : on preserve le path du parent
            # (et donc subType/subTypeId) pour rester dans le même contexte copro.
            new_url = build_subfolder_url(folder_url, folder_id, page=current_page, preserve_path=True)

            response = await request_with_retry(client, "GET", new_url, headers=API_HEADERS)

            if response.status_code == 200:
                try:
                    page_data = response.json()
                    if page_data.get("responseCode") == "200":
                        page_docs = page_data.get("payload", {}).get("docs", [])
                        documents_on_current_page = len(page_docs)

                        # Vérifie si on a des doublons (même contenu que les pages précédentes)
                        page_guids = {doc.get("guid") for doc in page_docs if doc.get("guid")}

                        # Si tous les GUIDs de cette page ont déjà été vus, on arrête
                        if page_guids and page_guids.issubset(seen_guids):
                            break

                        seen_guids.update(page_guids)

                        all_files.extend(_extract_file_info(doc) for doc in page_docs)
                    else:
                        print(f"Erreur page {current_page}: {page_data.get('msg', 'Erreur inconnue')}")
                        break
                except json.JSONDecodeError:
                    print(f"\n❌ Erreur de décodage JSON pour la page {current_page}")
                    break
            else:
                print(f"\n❌ Erreur HTTP pour la page {current_page}: {response.status_code}")
                break

        return {"folders": folders, "files": all_files, "directory_info": directory_info}

    except json.JSONDecodeError:
        print("Erreur: Réponse JSON invalide")
        return _empty_folder_content()


async def download_file_from_api(
    file_info: FileInfo,
    folder_path: str,
    client: httpx.AsyncClient,
    thread_id: int | None = None,
    existing_files: set[str] | None = None,
    token: str | None = None,
) -> bool:
    """
    Télécharge un fichier à partir des informations de l'API.

    Args:
        file_info: Informations du fichier depuis l'API
        folder_path: Chemin du dossier de destination
        client: Client HTTP asynchrone
        thread_id: ID du thread pour la barre de progression
        existing_files: Ensemble des fichiers existants pour éviter les téléchargements dupliqués
        token: Token d'authentification extrait de l'URL
    """
    global downloaded_files, progress_bar_total

    if existing_files is None:
        existing_files = set()

    file_name = file_info.get("nom", "unknown")
    extension = file_info.get("extension", "")
    guid = file_info.get("guid")
    emplacement = file_info.get("emplacement")

    if not guid or not emplacement:
        print(f"Informations manquantes pour le fichier {file_name}")
        return False

    if not token:
        print(f"Aucun token fourni pour le téléchargement de {file_name}")
        return False

    if guid in downloaded_files:
        return True

    relative_path = os.path.relpath(folder_path, download_folder) if folder_path != download_folder else "Racine"
    thread_display_id = (thread_id + 1) if thread_id is not None else 0
    short_desc = f"⚡ Thread {thread_display_id}: {relative_path}/{file_name}{extension}"
    _update_thread_bar(thread_id, _truncate(short_desc, 80))

    clean_name = file_info.get("_clean_name") or construct_file_name(file_info)

    # Gère les doublons de noms en ajoutant un suffixe numérique (cas rare)
    full_path = os.path.join(folder_path, clean_name)
    counter = 1
    while os.path.exists(full_path):
        name_part, ext_part = os.path.splitext(clean_name)
        full_path = os.path.join(folder_path, f"{name_part}_{counter}{ext_part}")
        counter += 1

    params: dict[str, str] = {
        "token": token,
        "emplacement": emplacement,
        "cabinet": "false",
        "nomFile": file_name,
        "extension": extension,
    }

    try:
        Path(folder_path).mkdir(parents=True, exist_ok=True)

        # Téléchargement en streaming pour ne pas charger tout le fichier en mémoire
        async with client.stream("GET", DOWNLOAD_URL, headers=DOWNLOAD_HEADERS, params=params) as response:
            if response.status_code != 200:
                print(f"\nErreur lors du téléchargement de {file_name}: {response.status_code}")
                return False

            with open(full_path, "wb") as f:
                async for chunk in response.aiter_bytes(64 * 1024):
                    f.write(chunk)

        downloaded_files.add(guid)

        _update_thread_bar(thread_id, f"Thread {thread_display_id}: ✅ {_truncate(clean_name, 50)}")

        if progress_bar_total and hasattr(progress_bar_total, "update"):
            progress_bar_total.update(1)

        return True

    except Exception as e:
        print(f"\nErreur lors du téléchargement de {file_name}: {e}")
        return False


def get_full_server_path(directory_info: dict[str, Any]) -> str:
    """
    Extrait le chemin complet du serveur depuis les informations du dossier.

    Args:
        directory_info: Informations du dossier depuis l'API
    """
    if not (
        full_path := directory_info.get(
            "cheminComplet", directory_info.get("chemin", "")
        )
    ):
        return ""
    # Retire le préfixe /u/clients/clesev/ges_oullins_ged/ s'il existe
    if full_path.startswith("/u/clients/clesev/ges_oullins_ged/"):
        full_path = full_path[len("/u/clients/clesev/ges_oullins_ged/") :]

    if full_path.startswith("/"):
        full_path = full_path[1:]

    full_path = full_path.replace("/", os.sep)

    # Nettoie chaque partie du chemin en utilisant les noms d'affichage
    path_parts = full_path.split(os.sep)
    cleaned_parts = [clean_file_name(part) for part in path_parts if part]

    return os.path.join(*cleaned_parts) if cleaned_parts else ""


async def collect_all_files_recursive(
    folder_url: str,
    client: httpx.AsyncClient,
    base_folder: str | None = None,
    current_path: str = "",
    depth: int = 0,
    existing_files: set[str] | None = None,
) -> tuple[list[FilePair], int]:
    """
    Collecte récursivement tous les fichiers et leurs informations de téléchargement.

    Args:
        folder_url: URL du dossier à explorer
        client: Client HTTP asynchrone
        base_folder: Dossier racine de téléchargement
        current_path: Chemin relatif actuel
        depth: Profondeur dans l'arborescence pour l'affichage
        existing_files: Ensemble des fichiers existants

    Returns:
        (files_to_download, existing_count) où files_to_download est une
        liste de tuples (file_info, folder_path) et existing_count est le
        nombre de fichiers déjà présents dans ce sous-arbre.
    """
    if base_folder is None:
        base_folder = download_folder

    if existing_files is None:
        existing_files = set()

    all_files_to_download: list[FilePair] = []

    content = await get_folder_content(folder_url, client)

    directory_info = content.get("directory_info", {})
    current_folder_name = directory_info.get("nom", "")

    # Pour le premier appel, détermine le chemin de base avec le nom d'affichage
    if not current_path and current_folder_name:
        current_path = clean_file_name(current_folder_name)

    files_count = len(content["files"])
    folders_count = len(content["folders"])
    full_folder_path = os.path.join(base_folder, current_path) if current_path else base_folder
    new_files_count = 0
    existing_files_count = 0

    for file_info in content["files"]:
        # Cache le nom nettoyé sur file_info pour éviter de le recalculer
        # dans download_file_from_api et display_listing.
        constructed_name = construct_file_name(file_info)
        file_info["_clean_name"] = constructed_name
        file_key = os.path.join(full_folder_path, constructed_name)
        is_new = file_key not in existing_files
        # Marqueur consommé par display_listing (comptage) et
        # download_files_from_list (filtrage avant téléchargement).
        file_info["_is_new"] = is_new

        if is_new:
            new_files_count += 1
        else:
            existing_files_count += 1
        # On inclut TOUS les fichiers dans la liste (avec leur statut _is_new)
        # pour que display_listing puisse afficher le compte exact.
        # download_files_from_list filtrera ensuite sur _is_new=True.
        all_files_to_download.append((file_info, full_folder_path))

    indent = "  " * depth
    folder_display = current_folder_name or "Racine"

    print(
        f"\r{indent}📁 {folder_display} ({files_count} fichiers: {new_files_count} nouveaux, {existing_files_count} existants, {folders_count} dossiers)",
        flush=True,
    )

    # Traite récursivement tous les sous-dossiers en parallèle
    tasks: list[Awaitable[tuple[list[FilePair], int]]] = []
    for folder in content["folders"]:
        folder_id = folder["id"]
        folder_name = folder["nom"]

        subfolder_url = build_subfolder_url(folder_url, folder_id)
        clean_folder_name_val = clean_file_name(folder_name)
        new_path = os.path.join(current_path, clean_folder_name_val) if current_path else clean_folder_name_val

        task = collect_all_files_recursive(
            subfolder_url, client, base_folder, new_path, depth + 1, existing_files
        )
        tasks.append(task)

    sub_existing_total = existing_files_count
    if tasks:
        subfolder_results = await asyncio.gather(*tasks)
        for subfolder_files, sub_existing in subfolder_results:
            all_files_to_download.extend(subfolder_files)
            sub_existing_total += sub_existing

    return all_files_to_download, sub_existing_total


async def download_files_from_list(
    files_list: list[FilePair],
    client: httpx.AsyncClient,
    existing_files: set[str] | None = None,
    token: str | None = None,
    total_existing: int = 0,
) -> None:
    """
    Télécharge tous les fichiers à partir d'une liste pré-construite avec parallélisation
    et barres de progression multiples.

    Args:
        files_list: Liste de tuples (file_info, folder_path) pour chaque fichier à télécharger
        client: Client HTTP authentifié (réutilisé — pas recréé)
        existing_files: Ensemble des fichiers existants (ignoré ici, compté via total_existing)
        token: Token d'authentification
        total_existing: Nombre total de fichiers déjà présents (pour initialiser la barre)
    """
    global downloaded_files, progress_bar_total, thread_progress_bars, thread_counter, enable_thread_bars

    downloaded_files.clear()
    thread_progress_bars.clear()
    thread_counter = 0

    # _is_new a été calculé en phase de collecte par collect_all_files_recursive.
    new_files_list: list[FilePair] = [(fi, fp) for fi, fp in files_list if fi.get("_is_new", True)]
    files_to_download = len(new_files_list)

    print(f"📊 Fichiers déjà présents: {total_existing}")
    print(f"📊 Fichiers à télécharger: {files_to_download}")
    print("🚀 Début du téléchargement")

    total_files = files_to_download + total_existing
    print(f"📊 Total de fichiers: {total_files}")
    print(f"🔧 Téléchargements simultanés: {max_concurrent_downloads}")
    if enable_thread_bars:
        print("📋 Barres de progression par thread: Activées")
    else:
        print("📋 Barres de progression par thread: Désactivées (mode simplifié)")
    print()

    progress_bar_total = tqdm(
        total=total_files, desc="📈 Total", position=0, leave=True, initial=total_existing
    )

    if enable_thread_bars:
        try:
            for i in range(max_concurrent_downloads):
                thread_progress_bars[i] = tqdm(
                    total=1,
                    desc="",  # Description vide pour éviter les ":" automatiques
                    position=2 + i,
                    leave=True,
                    bar_format="🔄 Thread {}: En attente".format(i + 1),
                    disable=False,
                    dynamic_ncols=True,
                    ncols=100,
                )
        except Exception as e:
            print(f"⚠️  Erreur avec les barres multiples ({e}), utilisation du mode simplifié")
            enable_thread_bars = False
            thread_progress_bars.clear()

    semaphore = asyncio.Semaphore(max_concurrent_downloads)

    async def download_with_semaphore(file_info: FileInfo, folder_path: str) -> bool:
        global thread_counter
        async with semaphore:
            current_thread_id = thread_counter % max_concurrent_downloads
            thread_counter += 1

            _update_thread_bar(current_thread_id, f"⚡ Thread {current_thread_id+1}: Démarrage")

            try:
                return await download_file_from_api(
                    file_info, folder_path, client, current_thread_id, existing_files, token
                )
            finally:
                _update_thread_bar(current_thread_id, f"🔄 Thread {current_thread_id+1}: En attente")

    tasks = [download_with_semaphore(file_info, folder_path) for file_info, folder_path in new_files_list]

    await asyncio.gather(*tasks)

    if progress_bar_total:
        progress_bar_total.close()

    for thread_bar in thread_progress_bars.values():
        thread_bar.close()

    progress_bar_total = None
    thread_progress_bars.clear()

    print(f"\n✅ Téléchargement terminé ! {len(downloaded_files)} fichiers uniques téléchargés.")


def filter_files_by_date(files_list: list[FilePair], start_date: str) -> list[FilePair]:
    """
    Filtre les fichiers dont la date dateCreated est >= à start_date.

    Args:
        files_list: Liste de tuples (file_info, folder_path)
        start_date: Date de début au format YYYY-MM
    """
    # On ajoute "-01" pour avoir YYYY-MM-01 (comparaison lexicographique)
    start_date_normalized = f"{start_date}-01"
    filtered: list[FilePair] = []
    for file_info, folder_path in files_list:
        date_created = file_info.get("dateCreated", "")
        if date_created and date_created >= start_date_normalized:
            filtered.append((file_info, folder_path))
        elif not date_created:
            filtered.append((file_info, folder_path))
    return filtered


async def prepare_collection(
    folder_url: str,
    base_folder: str,
    start_date: str | None,
    client: httpx.AsyncClient,
    existing_files: set[str],
) -> tuple[list[FilePair], set[str], int]:
    """
    Prépare la collecte des fichiers : récupération distante (avec pagination et
    exploration récursive) + filtrage par date.

    Le scan local est effectué une fois en amont (par main) et passé via
    `existing_files` — évite de reparcourir tout l'arbre à chaque propriété.

    Args:
        folder_url: URL du dossier ou de l'entité
        base_folder: Dossier local de référence pour les chemins cibles
        start_date: Date de début au format YYYY-MM (ou None)
        client: Client HTTP asynchrone
        existing_files: Chemins complets déjà présents en local (calculé une fois)

    Returns:
        (all_files, existing_files, total_existing)
            - all_files: liste de (file_info, folder_path) — folder_path est
              l'arborescence locale cible (avec la même structure pour les
              deux modes)
            - existing_files: set des chemins complets déjà présents en local
            - total_existing: nombre de fichiers déjà présents (avant filtrage date)
    """
    print("🔍 Phase 2: Collecte des fichiers distants...")
    all_files, total_existing = await collect_all_files_recursive(
        folder_url, client, base_folder, existing_files=existing_files
    )
    print(f"✅ Phase 2 terminée: {len(all_files)} fichiers trouvés, {total_existing} déjà présents")

    if start_date:
        before_count = len(all_files)
        all_files = filter_files_by_date(all_files, start_date)
        # Recalcule total_existing sur la liste filtrée : des fichiers existants
        # peuvent être écartés par le filtre date, ce qui ferait dériver la
        # barre de progression si on gardait l'ancien compteur.
        total_existing = sum(not fi.get("_is_new", True) for fi, _ in all_files)
        print(
            f"📅 Filtrage par date >= {start_date}: {before_count} → {len(all_files)} fichiers ({before_count - len(all_files)} exclus)"
        )

    return all_files, existing_files, total_existing


def _has_login_externe(action: str | None) -> bool:
    """Prédicat pour trouver le formulaire de login (action contient 'login_externe')."""
    return bool(action and "login_externe" in action)


class AuthSession(TypedDict):
    phpsessid: str
    cabinet_groupe: str
    client: httpx.AsyncClient
    properties: list[Property]


# Cookies de session — émis/acceptés uniquement sur le portail extranet2.ics.fr.
# On fixe le domaine explicitement pour éviter le piège du "host-only cookie"
# qui ne serait pas envoyé à un sous-domaine éventuel.
_AUTH_DOMAIN = "extranet2.ics.fr"


def _parse_documents_html(html: str) -> list[Property]:
    """
    Parse le HTML de `documents.html` pour extraire la liste des propriétés
    accessibles à l'utilisateur courant.

    Chaque propriété est un dict (url, imme, copro, building_name, doc_type,
    label). Les doublons (même couple imme/copro) sont dédupliqués.

    Args:
        html: Contenu HTML brut de la page documents.html.

    Returns:
        Liste de propriétés (peut être vide si l'utilisateur n'a accès à
        aucun immeuble).
    """
    docs_soup = bs4.BeautifulSoup(html, "html.parser")

    properties: list[Property] = []
    seen: set[str] = set()

    # Structure : chaque <p class="main-text"> est dans un row avec les liens documents-syndic
    for main_text in docs_soup.find_all("p", class_="main-text"):
        building_name = re.sub(r"\s+", " ", main_text.get_text(strip=True))

        # Remonte au row parent qui contient aussi les liens documents-syndic
        parent_row = main_text.find_parent("div", class_="row")
        if not parent_row:
            continue

        for a in parent_row.find_all("a", href=re.compile(r"documents-syndic")):
            link = str(a.get("href", ""))
            if not link:
                continue
            if match := re.match(r"documents-syndic-(.+)-(.+)\.html", link):
                imme = match[1]
                copro = match[2]
                key = f"{imme}=={copro}"
                if key not in seen:
                    seen.add(key)
                    full_url = f"https://extranet2.ics.fr/V5/{link}"
                    doc_type = a.get_text(strip=True)
                    label = f"{building_name} — {doc_type}"
                    properties.append(
                        {
                            "url": full_url,
                            "imme": imme,
                            "copro": copro,
                            "building_name": building_name,
                            "doc_type": doc_type,
                            "label": label,
                        }
                    )

    return properties


# Type de retour de _resolve_auth_inputs : (kind, value1, value2)
#   - ("cli_cookies",   phpsessid, cabinet_groupe)
#   - ("login",         login,     password)
#   - ("env_cookies",   phpsessid, cabinet_groupe)
#   - ("error",         reason,    None)
# On utilise un tuple plat pour éviter d'introduire un NamedTuple juste pour ça.
AuthInputs: TypeAlias = tuple[str, str, str | None]


def _resolve_auth_inputs(cli_phpsessid: str | None, cli_cabinet_groupe: str | None) -> AuthInputs:
    """
    Implémente la règle de priorité : `CLI cookies > .env login > .env cookies`.

    Règles détaillées (cf. plan d'implémentation) :
      1. Si au moins un des flags CLI est passé → on tente le mode cookie.
         Chaque cookie = valeur CLI si fournie, sinon valeur .env.
         Si l'un manque après merge → ("error", "partial_cli_cookies", None).
      2. Sinon, si LOGIN_URL + LOGIN + PASSWORD sont dans .env → login.
         Si l'un manque (notamment LOGIN_URL absent) → on tombe à l'étape 3.
      3. Sinon, si PHPSESSID + CABINET_GROUPE sont dans .env → cookies .env.
         Si l'un manque → ("error", "partial_env_cookies", None).
      4. Sinon → ("error", "no_method", None).
    """
    env_p = os.getenv("PHPSESSID", "").strip()
    env_c = os.getenv("CABINET_GROUPE", "").strip()
    cli_p = (cli_phpsessid or "").strip()
    cli_c = (cli_cabinet_groupe or "").strip()

    # Étape 1 : CLI cookies — déclenché dès qu'AU MOINS UN flag est passé.
    if cli_p or cli_c:
        merged_p = cli_p or env_p
        merged_c = cli_c or env_c
        if merged_p and merged_c:
            return "cli_cookies", merged_p, merged_c
        return "error", "partial_cli_cookies", None

    # Étape 2 : Login .env — requiert les 3 vars (LOGIN_URL aussi).
    # Si LOGIN_URL manque (typique : le user veut bypasser le login), on tombe
    # à l'étape 3.
    login = os.getenv("LOGIN", "").strip()
    pwd = os.getenv("PASSWORD", "").strip()
    login_url = os.getenv("LOGIN_URL", "").strip()
    if login and pwd and login_url:
        return "login", login, pwd

    # Étape 3 : Cookies .env (fallback captcha, ou si LOGIN_URL absent).
    if env_p and env_c:
        return "env_cookies", env_p, env_c
    if env_p or env_c:
        return "error", "partial_env_cookies", None

    # Étape 4 : Rien.
    return "error", "no_method", None


def _print_auth_error(reason: str) -> None:
    """Affiche un message d'erreur explicite selon la raison d'échec d'auth."""
    if reason == "partial_cli_cookies":
        print("❌ --phpsessid ou --cabinet-groupe est incomplet : les deux flags sont requis")
        print("   (ou alors la valeur manquante doit être dans .env)")
    elif reason == "partial_env_cookies":
        print("❌ PHPSESSID et CABINET_GROUPE doivent être tous deux définis dans .env")
    elif reason == "no_method":
        print("❌ Aucune méthode d'authentification configurée.")
        print("   → Soit LOGIN_URL + LOGIN + PASSWORD dans .env (méthode standard)")
        print("   → Soit PHPSESSID + CABINET_GROUPE dans .env (fallback captcha)")
        print("   → Soit --phpsessid + --cabinet-groupe en CLI (override ponctuel)")
    else:
        print(f"❌ Erreur d'authentification : {reason}")


async def _authenticate_with_cookies(
    phpsessid: str,
    cabinet_groupe: str,
    source: str,
) -> AuthSession | None:
    """
    Authentification par injection de cookies dans un client HTTP neuf.

    Court-circuite le formulaire de login : on injecte directement PHPSESSID +
    CABINET_GROUPE dans le client, puis on tape documents.html. Si la session
    est encore valide côté serveur, on récupère les propriétés. Sinon (PHPSESSID
    expiré, IP différente, cookie révoqué…), le serveur redirige vers la page
    de login et on détecte ça via l'URL finale.

    Args:
        phpsessid: Valeur du cookie PHPSESSID.
        cabinet_groupe: Valeur du cookie CABINET_GROUPE.
        source: Label pour les logs ("CLI" ou ".env").

    Returns:
        AuthSession (client ouvert) ou None si la session est invalide.
    """
    print(f"🍪 Authentification par cookies (PHPSESSID={phpsessid[:8]}..., source={source})")

    # follow_redirects=True : si la session est morte, le serveur renvoie une
    # 302 vers la page de login. On veut suivre pour pouvoir détecter l'URL
    # finale dans `docs_resp.url` (sinon on aurait status_code=302 et on ne
    # saurait pas si c'est une vraie 302 légitime ou un échec d'auth).
    client = httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True)
    client.cookies.set("PHPSESSID", phpsessid, domain=_AUTH_DOMAIN, path="/")
    client.cookies.set("CABINET_GROUPE", cabinet_groupe, domain=_AUTH_DOMAIN, path="/")

    print("🌐 Récupération de la liste des propriétés...")
    docs_resp = await client.get("https://extranet2.ics.fr/V5/documents.html")

    # Détection de redirection vers la page de login (session invalide).
    final_url = str(docs_resp.url)
    if "connexion-cdg" in final_url or "login_externe" in final_url:
        print(f"❌ Session invalide — redirigé vers {final_url}")
        print("   Le PHPSESSID a peut-être expiré ou été révoqué. Relance avec un cookie frais.")
        await client.aclose()
        return None

    if docs_resp.status_code != 200:
        print(f"❌ Impossible d'accéder à la page documents (HTTP {docs_resp.status_code})")
        await client.aclose()
        return None

    properties = _parse_documents_html(docs_resp.text)

    print(f"✅ {len(properties)} propriété(s) trouvée(s)")
    for i, prop in enumerate(properties, 1):
        print(f"   {i}. {prop['label']}")

    return {
        "phpsessid": phpsessid,
        "cabinet_groupe": cabinet_groupe,
        "client": client,  # Client ouvert — l'appelant doit le fermer
        "properties": properties,
    }


async def _authenticate_with_login(login: str, password: str) -> AuthSession | None:
    """
    Ancien flux d'authentification par POST sur `login_externe`.

    Récupère PHPSESSID et CABINET_GROUPE côté serveur (via le redirect
    initialisation.html), puis liste les propriétés.

    Returns:
        AuthSession (client ouvert) ou None si échec.
    """
    login_url = os.getenv("LOGIN_URL", "")

    # Étape 1 : Récupérer la page de login pour extraire le formulaire (client temporaire)
    print(f"🌐 Chargement de la page de login : {login_url}")
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as temp_client:
        login_page_resp = await temp_client.get(login_url)

    if login_page_resp.status_code != 200:
        print(f"❌ Impossible de charger la page de login (HTTP {login_page_resp.status_code})")
        return None

    login_soup = bs4.BeautifulSoup(login_page_resp.text, "html.parser")
    form = login_soup.find("form", action=_has_login_externe)
    if not form:
        print("❌ Formulaire de connexion introuvable sur la page")
        return None

    form_action = str(form.get("action", ""))
    if not form_action.startswith("http"):
        form_action = urljoin(login_url, form_action)

    groupe_input = form.find("input", {"name": "groupe"})
    groupe = groupe_input.get("value", "") if groupe_input else ""

    print(f"🔐 Authentification en cours (groupe={groupe})...")

    # Étape 2 : POST des credentials avec un client dédié (pour éviter les conflits de cookies)
    client = httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=False)

    resp = await client.post(form_action, data={"login": login, "mdp": password, "groupe": groupe})

    if resp.status_code == 200:
        if "Identification incorrecte" in resp.text or "incorrect" in resp.text.lower():
            print("❌ Authentification échouée : identifiants incorrects")
        else:
            print("❌ Réponse inattendue du serveur de login")
        await client.aclose()
        return None

    if resp.status_code not in (301, 302, 303, 307):
        print(f"❌ Réponse inattendue du serveur (HTTP {resp.status_code})")
        await client.aclose()
        return None

    phpsessid = next(
        (
            cookie_value
            for cookie_name, cookie_value in resp.cookies.items()
            if cookie_name == "PHPSESSID"
        ),
        "",
    )
    if not phpsessid:
        print("❌ Impossible d'extraire le PHPSESSID de la réponse de login")
        await client.aclose()
        return None

    print(f"✅ PHPSESSID obtenu : {phpsessid[:8]}...")

    # Étape 4 : Suivre le redirect vers initialisation.html (pose le cookie CABINET_GROUPE)
    redirect_url = resp.headers.get("location", "")
    if not redirect_url:
        print("❌ Aucune redirection après le login")
        await client.aclose()
        return None

    if not redirect_url.startswith("http"):
        redirect_url = urljoin(form_action, redirect_url)

    print("🌐 Initialisation de la session...")
    await client.get(redirect_url, follow_redirects=True)

    cabinet_groupe = next(
        (c.value for c in client.cookies.jar if c.name == "CABINET_GROUPE"), ""
    )
    if not cabinet_groupe:
        print("⚠️  CABINET_GROUPE non trouvé dans les cookies")
        cabinet_groupe = ""

    print(f"✅ Session initialisée (CABINET_GROUPE={cabinet_groupe})")

    # Étape 5 : Récupérer la page documents.html pour lister les propriétés
    print("🌐 Récupération de la liste des propriétés...")
    docs_resp = await client.get("https://extranet2.ics.fr/V5/documents.html", follow_redirects=True)

    if docs_resp.status_code != 200:
        print(f"❌ Impossible d'accéder à la page documents (HTTP {docs_resp.status_code})")
        await client.aclose()
        return None

    properties = _parse_documents_html(docs_resp.text)

    print(f"✅ {len(properties)} propriété(s) trouvée(s)")
    for i, prop in enumerate(properties, 1):
        print(f"   {i}. {prop['label']}")

    return {
        "phpsessid": phpsessid,
        "cabinet_groupe": cabinet_groupe,
        "client": client,  # Client ouvert — l'appelant doit le fermer
        "properties": properties,
    }


async def authenticate(
    cli_phpsessid: str | None = None,
    cli_cabinet_groupe: str | None = None,
) -> AuthSession | None:
    """
    Authentification au portail syndic via l'une des trois méthodes possibles,
    dans l'ordre de priorité :
      1. Cookies injectés (CLI override ou .env) — utile quand le login est
         bloqué par un captcha.
      2. Login/mot de passe (POST login_externe) — méthode standard.
      3. Cookies via .env — utilisé quand LOGIN_URL/LOGIN/PASSWORD sont
         absents du .env.

    Les flags CLI (`--phpsessid`, `--cabinet-groupe`) fusionnent avec les
    valeurs du .env : CLI a priorité, mais on complète avec .env si un seul
    des deux est fourni en CLI.

    Args:
        cli_phpsessid: Valeur PHPSESSID passée en CLI (None si absent).
        cli_cabinet_groupe: Valeur CABINET_GROUPE passée en CLI (None si absent).

    Returns:
        AuthSession (client HTTP ouvert, à fermer par l'appelant) ou None
        si aucune méthode n'a pu aboutir.
    """
    inputs = _resolve_auth_inputs(cli_phpsessid, cli_cabinet_groupe)
    kind, v1, v2 = inputs

    if kind == "error":
        _print_auth_error(v1)
        return None

    if kind == "cli_cookies":
        assert v2 is not None  # pour le type-checker
        return await _authenticate_with_cookies(v1, v2, source="CLI")

    if kind == "env_cookies":
        assert v2 is not None
        return await _authenticate_with_cookies(v1, v2, source=".env")

    # kind == "login"
    return await _authenticate_with_login(v1, cast(str, v2))


async def get_property_details(client: httpx.AsyncClient, property_url: str) -> PropertyDetails | None:
    """
    Récupère les détails d'une propriété depuis sa page documents-syndic.
    Extrait les attributs du <side-menu-left>.

    Args:
        client: Client HTTP avec session active
        property_url: URL de la page documents-syndic-*

    Returns:
        dict: {cle, ics_login, ics_pwd, cabinet, imme, copro} ou None
    """
    resp = await client.get(property_url, follow_redirects=True)

    if resp.status_code != 200:
        print(f"❌ Impossible d'accéder à la page propriété (HTTP {resp.status_code})")
        return None

    # Regex plutôt que BS4 : les custom elements ne sont pas parsés correctement.
    match = re.search(r"<side-menu-left\s+([^>]+)>", resp.text)
    if not match:
        print("❌ Élément <side-menu-left> introuvable dans la page")
        return None

    attrs = dict(re.findall(r'(\w[\w-]*)="([^"]*)"', match.group(1)))

    cle = attrs.get("cle", "")
    ics_login = attrs.get("login", "")
    ics_pwd = attrs.get("pwd", "")
    cabinet = attrs.get("cabinet", "")
    imme = attrs.get("imme", "")
    copro = attrs.get("copro", "")

    if not cle:
        print("❌ Clé (cle) introuvable dans le side-menu-left")
        return None

    print(f"✅ Détails récupérés : cabinet={cabinet}, cle={cle[:12]}...")

    return {
        "cle": cle,
        "ics_login": ics_login,
        "ics_pwd": ics_pwd,
        "cabinet": cabinet,
        "imme": imme,
        "copro": copro,
    }


async def get_token(client: httpx.AsyncClient, property_details: PropertyDetails) -> str | None:
    """
    Appelle le webservice idservice pour obtenir un token de téléchargement.

    Args:
        client: Client HTTP avec session active
        property_details: Résultat de get_property_details()

    Returns:
        Token ou None si échec
    """
    cle = property_details["cle"]
    ics_login = property_details["ics_login"]
    ics_pwd = property_details["ics_pwd"]

    # Le webservice idservice inverse login et pwd par rapport au side-menu-left :
    # side-menu-left : login="ics06@ics.fr" pwd="ics"
    # idservice      : login=ics (pwd) mdp=ics06@ics.fr (login)
    url = (
        f"https://extranet2.ics.fr/webservice/idservice/index.php"
        f"?clePortefeuille={cle}"
        f"&login={ics_pwd}"
        f"&mdp={ics_login}"
        f"&nomProduit=Ged"
        f"&operation=get"
        f"&retour=json"
    )

    resp = await client.get(url)

    if resp.status_code != 200:
        print(f"❌ Erreur lors de la récupération du token (HTTP {resp.status_code})")
        return None

    data: dict[str, Any] = resp.json()

    if not data.get("success"):
        print(f"❌ Erreur du webservice : {data.get('erreur', 'erreur inconnue')}")
        return None

    # Le token peut être sous différentes clés selon la réponse
    token = data.get("token") or data.get("result") or data.get("cle") or data.get("id")

    if not token:
        print(f"❌ Token introuvable dans la réponse : {data}")
        return None

    print(f"✅ Token obtenu : {str(token)[:12]}...")
    return token


def _validate_since(value: str) -> str:
    """Type argparse : valide qu'une date est au format YYYY-MM."""
    if not re.match(r"^\d{4}-\d{2}$", value):
        raise argparse.ArgumentTypeError(
            f"Format invalide : {value!r}. Attendu : YYYY-MM (ex: 2024-01)"
        )
    return value


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse les arguments de la ligne de commande.

    Args:
        argv: Liste d'arguments à parser (défaut: sys.argv[1:]). Le paramètre
            est exposé pour faciliter les tests sans manipuler sys.argv.
    """
    parser = argparse.ArgumentParser(
        prog="download_syndic",
        description=(
            "Télécharge les fichiers mis à disposition sur le portail du Syndic (ICS). "
            "Sans option, le script guide l'utilisateur via des menus interactifs."
        ),
        # allow_abbrev=False : on veut qu'une typo type `--download` (au lieu de
        # `--download-all`) soit une erreur, pas un raccourci silencieux.
        allow_abbrev=False,
    )
    parser.add_argument(
        "--download-all",
        action="store_true",
        help=(
            "Mode non-interactif : "
            "sélectionne automatiquement toutes les propriétés, "
            "filtre à partir de la date --since (2000-01 par défaut) "
            "et lance le téléchargement sans confirmation. "
            "Aucune question n'est posée."
        ),
    )
    parser.add_argument(
        "--since",
        type=_validate_since,
        default="2000-01",
        metavar="YYYY-MM",
        help=(
            "Date de début pour le filtrage des documents au format YYYY-MM. "
            "En mode interactif : proposée comme valeur par défaut du prompt. "
            "En mode --download-all : appliquée directement. "
            "Défaut : 2000-01."
        ),
    )
    parser.add_argument(
        "--phpsessid",
        type=str,
        default=None,
        metavar="ID",
        help=(
            "Override la valeur PHPSESSID du .env et active l'authentification "
            "par cookies (skip le login/mdp). Le cookie manquant peut être "
            "complété depuis .env si besoin. Doit être combiné avec "
            "--cabinet-groupe ou avec PHPSESSID+CABINET_GROUPE dans .env."
        ),
    )
    parser.add_argument(
        "--cabinet-groupe",
        type=str,
        default=None,
        metavar="VAL",
        help=(
            "Override la valeur CABINET_GROUPE du .env. À combiner avec "
            "--phpsessid (ou avec PHPSESSID+CABINET_GROUPE dans .env) pour "
            "activer le mode cookie."
        ),
    )
    return parser.parse_args(argv)


async def main(argv: Sequence[str] = ()) -> None:
    # Note : on ne lit PAS sys.argv depuis main() — c'est le bloc
    # `if __name__ == "__main__":` qui passe sys.argv[1:] explicitement.
    # Cela évite que les args du runner de tests (pytest) soient parsées
    # par argparse quand main() est appelé depuis la suite de tests.
    args = parse_args(argv)
    download_all: bool = args.download_all
    start_date_default: str = args.since

    print("=== EXPLORATEUR SYNDIC ===")
    if download_all:
        print("🤖 Mode non-interactif (--download-all) : aucune question ne sera posée")
    print(f"Dossier de téléchargement: {download_folder}")
    print()

    print("=" * 50)
    print("🔐 Phase 1 : Authentification")
    print("=" * 50)

    session = await authenticate(
        cli_phpsessid=args.phpsessid,
        cli_cabinet_groupe=args.cabinet_groupe,
    )
    if not session:
        return

    client = session["client"]  # Client HTTP avec session active

    try:
        properties = session["properties"]

        # --- PHASE 2 : SÉLECTION DES PROPRIÉTÉS ---
        if download_all:
            selected_props = properties
            print(f"✅ Mode non-interactif : {len(properties)} propriété(s) sélectionnée(s)")
        else:
            selected_props = await select_properties(properties)

        if not selected_props:
            return

        # --- PHASE 3 : FILTRAGE PAR DATE (une seule fois pour toutes les propriétés) ---
        if download_all:
            start_date = start_date_default
            print(f"\n📅 Filtrage des documents à partir de : {start_date}")
        else:
            start_date = await prompt_start_date(default=start_date_default)

        # --- PHASE 4 : CHOIX DE L'ACTION (une seule fois pour toutes les propriétés) ---
        if download_all:
            action = "2"  # télécharger
            print("✅ Action sélectionnée : Télécharger (mode non-interactif)")
        else:
            action = await prompt_action()

        # --- PHASE 5 : SCAN LOCAL UNIQUE (avant la boucle des propriétés) ---
        # Le scan est partagé entre toutes les propriétés — évite de reparcourir
        # l'arbre N fois si l'utilisateur traite N copropriétés.
        print("🔍 Phase 1: Scan des fichiers existants...")
        existing_files = scan_existing_files(download_folder)
        print(f"✅ Scan terminé : {len(existing_files)} fichiers existants")

        for idx, prop in enumerate(selected_props, 1):
            print()
            print("=" * 50)
            print(f"🏢 Propriété {idx}/{len(selected_props)} : {prop['label']}")
            print("=" * 50)

            property_details = await get_property_details(client, prop["url"])
            if not property_details:
                print("⚠️  Impossible de récupérer les détails, propriété ignorée.")
                continue

            token = await get_token(client, property_details)
            if not token:
                print("⚠️  Impossible de récupérer le token, propriété ignorée.")
                continue

            # Discrimination structurelle de la vue via fallback automatique
            # (l'API refuse subType=COPROPRIETAIRE pour IMMEUBLE → 500).
            imme = property_details["imme"]
            copro = property_details["copro"]
            vos_url = build_entity_url(token, imme, copro, use_copro_filter=True)
            imm_url = build_entity_url(token, imme, copro, use_copro_filter=False)
            entity_url, vue_detectee = await get_entity_url_with_fallback(
                client, vos_url, imm_url, label=prop.get("label", "")
            )
            print(f"🏢 URL entité : imme={imme} copro={copro} vue={vue_detectee}")

            prop_subfolder = clean_file_name(prop["building_name"])
            prop_download_folder = os.path.join(download_folder, prop_subfolder)

            all_files, existing_files, total_existing = await prepare_collection(
                entity_url, prop_download_folder, start_date, client, existing_files
            )

            if action == "1":
                display_listing(all_files, existing_files, prop_download_folder)
            else:
                await run_download(
                    all_files, existing_files, token, prop_download_folder,
                    client, total_existing, auto_confirm=download_all,
                )

    finally:
        await client.aclose()


# --- Helpers d'interaction utilisateur (isolent la dépendance questionary) ---
# Note : on utilise ask_async() (et non ask()) car ask() démarre sa propre
# event loop via asyncio.run(), ce qui plante quand on est déjà dans une
# loop (cas de main() lancé par asyncio.run).


async def _select(
    message: str,
    choices: Sequence[Choice | str],
    default: Any = None,
) -> Any:
    """Liste de sélection avec navigation flèches. Renvoie None si Ctrl+C."""
    return await questionary.select(message, choices=choices, default=default).ask_async()


async def _text(
    message: str,
    default: str = "",
    validate: Callable[[str], bool | str] | None = None,
) -> str | None:
    """Saisie texte avec validation optionnelle. Renvoie None si Ctrl+C."""
    return await questionary.text(message, default=default, validate=validate).ask_async()


async def _confirm(message: str, default: bool = False) -> bool:
    """Confirmation y/N. Renvoie default si Ctrl+C."""
    return await questionary.confirm(message, default=default).ask_async()


async def select_properties(properties: list[Property]) -> list[Property]:
    """
    Demande à l'utilisateur de sélectionner une ou toutes les propriétés.
    L'option "TOUTES" (sélectionnée par défaut) ramène toutes les propriétés.
    """
    if len(properties) == 1:
        print(f"\n✅ Propriété unique détectée : {properties[0]['label']}")
        return properties

    # Choice "TOUTES" en premier — highlighted par défaut, Entrée suffit.
    all_choice = Choice("🌐 TOUTES les propriétés", value="__ALL__")
    prop_choices = [all_choice] + [Choice(p["label"], value=p) for p in properties]

    selected = await _select(
        f"Propriétés disponibles ({len(properties)}) — flèches ↑/↓ puis Entrée :",
        choices=prop_choices,
    )

    if selected is None or selected == "__ALL__":
        print(f"✅ Toutes les propriétés sélectionnées ({len(properties)})")
        return properties

    print(f"✅ Propriété sélectionnée : {selected['label']}")
    return [selected]


async def prompt_start_date(default: str = "2000-01") -> str:
    """Demande la date de début de filtrage. Défaut : '2000-01'."""

    def _validate(val: str) -> bool | str:
        if not val:
            return True  # vide → default
        if re.match(r"^\d{4}-\d{2}$", val):
            return True
        return "Format invalide. Attendu : YYYY-MM (ex: 2024-01)"

    result = await _text(
        "Date de début pour le filtrage (YYYY-MM) :",
        default=default,
        validate=_validate,
    )

    start_date = result or default
    print(f"\n📅 Filtrage des documents à partir de : {start_date}")
    return start_date


async def prompt_action() -> str:
    """
    Demande à l'utilisateur de choisir entre lister (1) ou télécharger (2).
    Par défaut : 1 (lister, mode à blanc recommandé).
    """
    choices = [
        Choice("Lister les fichiers (mode à blanc, recommandé)", value="1"),
        Choice("Télécharger les fichiers", value="2"),
    ]
    result = await _select("Que voulez-vous faire ?", choices=choices, default="1")

    action_choice = result or "1"
    label = "Lister" if action_choice == "1" else "Télécharger"
    print(f"✅ Action sélectionnée : {label}")
    return action_choice


def display_listing(all_files: list[FilePair], existing_files: set[str], prop_download_folder: str) -> None:
    """
    Affiche les fichiers groupés par sous-dossier avec leur statut
    (nouveau / déjà présent) et un résumé global.

    S'appuie sur le marqueur _is_new attaché à chaque file_info par
    collect_all_files_recursive — garantit la cohérence avec ce qui a
    été compté en phase de collecte et évite une lookup redondante
    dans le set existing_files.
    """
    print(f"\n🔍 Comparaison des fichiers en ligne avec : {prop_download_folder}")

    by_folder: dict[str, list[FilePair]] = defaultdict(list)
    for file_info, folder_path in all_files:
        rel = os.path.relpath(folder_path, prop_download_folder)
        by_folder[rel].append((file_info, folder_path))

    total_new = 0
    total_existing = 0
    for rel_path, files in sorted(by_folder.items()):
        new = 0
        existing = 0
        for file_info, _folder_path in files:
            if file_info.get("_is_new", True):
                new += 1
            else:
                existing += 1
        total_new += new
        total_existing += existing
        label = rel_path if rel_path != "." else "Racine"
        print(f"  📂 {label} : {new} nouveaux, {existing} existants")

        for file_info, _folder_path in files[:20]:
            constructed_name = file_info.get("_clean_name") or construct_file_name(file_info)
            is_new = file_info.get("_is_new", True)
            status = "🆕 nouveau" if is_new else "✅ présent"
            print(f"    {status} — {rel_path}/{constructed_name}")

        if len(files) > 20:
            print(f"    ... et {len(files) - 20} autres")

    print(
        f"\n📊 Résumé : {len(all_files)} fichiers en ligne, "
        f"{total_existing} déjà présents, {total_new} nouveaux"
    )


async def run_download(
    all_files: list[FilePair],
    existing_files: set[str],
    token: str,
    prop_download_folder: str,
    client: httpx.AsyncClient,
    total_existing: int,
    auto_confirm: bool = False,
) -> None:
    """Lance le téléchargement des fichiers collectés, après confirmation.

    Args:
        all_files: Liste de tuples (file_info, folder_path) à télécharger.
        existing_files: Set des chemins déjà présents localement.
        token: Token d'authentification.
        prop_download_folder: Dossier local de destination.
        client: Client HTTP authentifié.
        total_existing: Nombre de fichiers déjà présents (pour la barre).
        auto_confirm: Si True, saute la confirmation interactive (mode
            --download-all). Défaut False.
    """
    print(f"\n⬇️  Téléchargement dans : {prop_download_folder}")
    if auto_confirm:
        print("🚀 Mode non-interactif : confirmation automatique")
        confirmed = True
    else:
        confirmed = await _confirm("Confirmer le téléchargement ?", default=False)

    if not confirmed:
        print("Téléchargement annulé pour cette propriété.")
        return

    print("Démarrage du téléchargement...")
    await download_files_from_list(all_files, client, existing_files, token, total_existing)
    print(f"✅ Téléchargement terminé pour cette propriété ({len(downloaded_files)} fichiers uniques au total).")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
