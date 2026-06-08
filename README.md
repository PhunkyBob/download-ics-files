# download-ics-files

Télécharge les fichiers mis à disposition sur le portail du Syndic qui utilise le système ICS.

## Prérequis

- [uv](https://docs.astral.sh/uv/) : gestionnaire de paquets et runner Python.
  Installation sous Windows (PowerShell) :

  ```powershell
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```

  Sous macOS / Linux :

  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

  `uv` se charge ensuite d'installer Python ≥ 3.12 et les dépendances déclarées dans `pyproject.toml` à la première exécution.

## Utilisation

- Copier / coller le fichier `.env.sample` en le renommant `.env`
- Renseigner dans le fichier `.env` :
  - `LOGIN_URL` : URL de la page de connexion du portail syndic
  - `LOGIN` : identifiant de connexion
  - `PASSWORD` : mot de passe de connexion
  - `DOWNLOAD_FOLDER` : dossier local de destination des fichiers
- (Optionnel) `HTTP_TIMEOUT` : timeout HTTP en secondes (défaut `60`)
- (Optionnel) `HTTP_MAX_RETRIES` : nombre de tentatives sur timeout (défaut `3`)
- Exécuter le script :

```cmd
uv run download_syndic.py
```

Le script guide ensuite l'utilisateur :

1. Authentification automatique sur le portail syndic.
2. Si plusieurs propriétés (copropriétés) sont rattachées au compte, choix d'une propriété ou de toutes.
3. Saisie d'une date de début de filtrage au format `YYYY-MM` (défaut : `2000-01`). Seuls les documents créés à partir de cette date sont pris en compte.
4. Choix de l'action :
   - `1` : lister les fichiers (mode à blanc, recommandé pour un premier passage) ;
   - `2` : télécharger les fichiers (avec confirmation avant lancement).

### Options de la ligne de commande

```cmd
uv run download_syndic.py [--download-all] [--since YYYY-MM]
```

- `--download-all` : mode **non-interactif** adapté aux scripts / à la CI.
  Aucune question n'est posée : toutes les propriétés rattachées au compte sont traitées, le téléchargement est lancé automatiquement, et chaque  téléchargement est confirmé d'office (`yes`).
  Un message indique en début d'exécution que le mode non-interactif est actif.
- `--since YYYY-MM` : date de début pour le filtrage des documents.
  En mode interactif, elle est proposée comme valeur par défaut du prompt (l'utilisateur peut encore modifier). En mode `--download-all`, elle est appliquée telle quelle. Défaut : `2000-01`.

Exemples :

```cmd
# Mode interactif classique, comportement inchangé
uv run download_syndic.py

# Mode non-interactif : télécharge tout, sans poser de question
uv run download_syndic.py --download-all

# Mode non-interactif avec date de filtrage personnalisée
uv run download_syndic.py --download-all --since 2024-01

# Mode interactif avec une autre date proposée par défaut
uv run download_syndic.py --since 2024-01
```

## Fonctionnement

- Les fichiers sont téléchargés dans le dossier défini par `DOWNLOAD_FOLDER`. Chaque propriété obtient un sous-dossier nommé d'après l'immeuble.
- Le téléchargement est récursif : tous les sous-dossiers sont explorés.
- En cas de doublons de nom, le `nomGed` est ajouté entre parenthèses ; un suffixe numérique `_N` est ajouté en dernier recours.
- Les fichiers déjà présents dans le répertoire de destination ne sont pas re-téléchargés (scan local unique partagé entre les propriétés).
- L'authentification se fait via le formulaire de login du portail (récupération automatique du `PHPSESSID` et du `CABINET_GROUPE`).
- Une barre de progression globale ainsi que des barres par thread de téléchargement sont affichées.
- Le script détecte automatiquement la vue API à utiliser (`VOS DOCUMENTS` ou `DOCUMENTS DE L'IMMEUBLE`) par tentative / fallback.
- Les timeouts HTTP font l'objet de retries exponentiels.

## Notes

- Le script utilise un client `httpx` asynchrone avec un maximum de 10 téléchargements simultanés (paramétrable via `max_concurrent_downloads` dans le code).
- Aucune session n'est persistée : il faut se reconnecter à chaque exécution.
