# Voice-to-Map

Challenge Lysk, hackathon EDTH Paris 2026 (20–22 novembre).

Les unités annoncent leur position à la voix (« Alpha 1, je suis au sud de l'église »). En quelques secondes, leur symbole bouge sur une carte partagée par toute l'équipe, avec l'heure, le trajet par les rues et la marge d'erreur. La même voix sert à signaler des ennemis, les neutraliser, former des escouades ou demander du renfort.

Plan, architecture et décisions : https://claude.ai/artifact/FxDnkHTYujgVLEJAjhvZs2

---

## Démarrage rapide

```bash
git clone https://github.com/Dry34/voice-to-map.git
cd voice-to-map
./scripts/start.sh            # http://localhost:8000
```

Au premier lancement, le script crée l'environnement Python, installe les dépendances et crée le fichier `.env`. Ajoutez-y votre clé Mistral (voir [Configuration](#configuration)), puis relancez.

Pour que les autres appareils (téléphones, PC de l'équipe) puissent se connecter :

```bash
./scripts/start.sh --tunnel   # affiche un lien public https://….trycloudflare.com
```

Ouvrez ensuite la page d'accueil, puis **Ouvrir la carte**, prenez une unité (par exemple `ALPHA 1`) et maintenez le bouton **Maintenir pour parler**.

`Ctrl+C` arrête le serveur et le tunnel.

---

## Installation pas à pas

### Prérequis

| Outil | Version | Pourquoi |
|---|---|---|
| Python | 3.11 ou plus récent | le serveur (FastAPI) |
| git | toute version | récupérer le code |
| Navigateur | Chrome, Edge, Safari ou Firefox récent | micro, signatures Ed25519, application installable |
| cloudflared | facultatif | lien HTTPS public pour les autres appareils |
| Clé API Mistral | facultatif mais recommandé | transcription vocale et compréhension des phrases |

Installer cloudflared : `brew install cloudflared` (macOS), sinon [la page de téléchargement Cloudflare](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/).

### 1. Récupérer le code

```bash
git clone https://github.com/Dry34/voice-to-map.git
cd voice-to-map
```

### 2. Environnement Python et dépendances

macOS / Linux :

```bash
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt
```

Windows (PowerShell) :

```powershell
py -3 -m venv backend\.venv
backend\.venv\Scripts\pip install -r backend\requirements.txt
```

### 3. Configuration

```bash
cp .env.example .env          # Windows : copy .env.example .env
```

Puis ouvrez `.env` et renseignez au minimum `MISTRAL_API_KEY`. Le détail de chaque variable est plus bas.

### 4. Lancer le serveur

macOS / Linux :

```bash
cd backend
.venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Windows :

```powershell
cd backend
.venv\Scripts\uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Le serveur sert aussi le site : il n'y a rien à compiler.

| Adresse | Page |
|---|---|
| http://localhost:8000 | page d'accueil |
| http://localhost:8000/app.html | la carte |
| http://localhost:8000/docs | documentation interactive de l'API |

`--reload` relance le serveur à chaque modification d'un fichier Python. Les fichiers du site (HTML, CSS, JS) sont pris en compte au prochain rechargement de la page.

### 5. Lien public pour l'équipe (facultatif)

Dans un deuxième terminal, serveur lancé :

```bash
cloudflared tunnel --no-autoupdate --protocol http2 --url http://localhost:8000
```

L'adresse `https://….trycloudflare.com` s'affiche au bout de quelques secondes. Elle change à chaque lancement du tunnel, et ne fonctionne que tant que l'ordinateur reste allumé et connecté.

> Le micro et les signatures ne fonctionnent qu'en HTTPS ou sur `localhost`. Un autre appareil qui ouvre `http://192.168.x.x:8000` verra la carte mais ne pourra ni parler ni prendre d'unité : utilisez le tunnel.

---

## Configuration

Toutes les variables sont dans `.env` (modèle : `.env.example`). **Ne jamais committer `.env`** : il contient la clé API.

| Variable | Valeur par défaut | Rôle |
|---|---|---|
| `MISTRAL_API_KEY` | vide | clé de [console.mistral.ai](https://console.mistral.ai) (API Keys). Sans clé, l'analyse passe par des règles locales, moins fines, et le micro ne marche pas |
| `MISTRAL_MODEL` | `voxtral-small-2507` | modèle qui comprend les phrases (60 requêtes par minute sur notre clé) |
| `STT_MODEL` | `voxtral-mini-latest` | modèle de transcription de la voix |
| `STT_LANGUAGE` | `fr` | `fr` transcrit bien le français et l'anglais ; vide = détection automatique |
| `NODE_MODE` | `central` | rôle du serveur (`central` ou `relais`) |
| `DB_PATH` | `data/events.db` | base SQLite du journal |
| `ZONE_NAME` | `Paris centre` | nom de la zone affiché |
| `ZONE_LAT`, `ZONE_LON` | `48.8584`, `2.3470` | centre de la carte et des recherches |
| `ZONE_RADIUS_M` | `2000` | rayon de recherche des repères génériques (« l'église », « la pharmacie ») |

Après une modification de `.env`, relancez le serveur.
