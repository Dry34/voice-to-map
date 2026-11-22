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

---

## Utilisation

1. **Prendre une unité.** Sur la carte, tapez un nom (`ALPHA 1`, `TOM 2`…) puis **Prendre**. Le navigateur crée sa clé : ce terminal est désormais le seul à pouvoir parler pour cette unité. **Poste de commandement** permet de tout voir sans unité.
2. **Parler.** Maintenez **Maintenir pour parler** (ou la barre espace), parlez, relâchez. On peut aussi écrire l'annonce.
3. **Suivre.** Toutes les unités, les ennemis et l'historique se mettent à jour en direct sur tous les écrans.

Exemples d'annonces (en français ou en anglais) :

| On dit | La carte |
|---|---|
| « Je suis au sud de l'église. » | place l'unité 120 m au sud de l'église la plus proche |
| « Je me dirige rue de Turbigo puis aux Arts et Métiers. » | trajet par les rues, étape par étape |
| « Je suis la route et je tourne à droite au prochain bloc. » | un pâté de maisons devant, puis à droite |
| « Je marche 60 m vers le sud. » / « Je recule. » | déplacement relatif, demi-tour |
| « Contact, cinq ennemis près de l'Hôtel-Dieu. » | E1 à E5, alignés dans la rue la plus proche |
| « Ennemi à 6h, 100 mètres. » | derrière l'unité (12h = sens de marche) |
| « E3 neutralisé. » / « J'élimine les ennemis autour de moi. » | ennemis grisés |
| « Moi et Tom 2, on est en groupe. » | escouade qui se déplace ensemble |
| « Je pars tout seul. » | quitte l'escouade |
| « Je regarde à 3h. » | la flèche tourne de 90° |
| « À l'aide, on est pris à partie ! » | alerte aux unités à moins de 3 km |
| « Confirmé. » | valide la dernière annonce « à confirmer » |

---

## Commandes utiles

Depuis la racine du projet (sous Windows, remplacez `backend/.venv/bin/python` par `backend\.venv\Scripts\python`).

| Commande | Effet |
|---|---|
| `./scripts/start.sh` | lance le serveur (`--tunnel` pour le lien public, `PORT=8001` pour un autre port) |
| `backend/.venv/bin/python scripts/clear_map.py` | remet la carte à zéro : plus d'unités, d'ennemis ni d'escouades ; l'historique affiché repart de zéro (le journal complet est conservé) |
| `backend/.venv/bin/python scripts/release_unit.py --list` | liste les unités réservées |
| `backend/.venv/bin/python scripts/release_unit.py "ALPHA 1"` | libère une unité bloquée (navigateur effacé, autre appareil) |
| `backend/.venv/bin/python scripts/fetch_landmarks.py` | met en cache les repères de la zone (plus rapide, utile hors ligne) |
| `cd backend && .venv/bin/python -m pytest -q` | lance les tests |
| `kill $(lsof -tiTCP:8000 -sTCP:LISTEN)` | arrête un serveur resté ouvert sur le port 8000 |
| `curl localhost:8000/api/integrity` | vérifie la chaîne du journal et les signatures |

---

## Dépannage

| Symptôme | Cause et solution |
|---|---|
| « Le port 8000 est déjà utilisé » | un serveur tourne déjà : `kill $(lsof -tiTCP:8000 -sTCP:LISTEN)`, ou `PORT=8001 ./scripts/start.sh` |
| Erreur Cloudflare 530 sur le lien public | l'ordinateur qui fait tourner le serveur a perdu internet ou s'est mis en veille. Le tunnel se reconnecte tout seul ; sinon relancez-le (nouvelle adresse) |
| Le micro ne marche pas sur un autre appareil | il faut HTTPS : utilisez le lien du tunnel, pas l'adresse `http://192.168…` |
| « Ce navigateur ne gère pas les signatures Ed25519 » | mettez à jour le navigateur (Chrome 137+, Safari 17+, Firefox 129+) |
| « … est utilisée par un terminal connecté » | l'unité est ouverte dans un autre onglet. Fermez-le, ou attendez 20 s après sa fermeture |
| Une annonce reste « à confirmer » | lieu introuvable ou phrase ambiguë : bouton **Confirmer** si un lieu a été trouvé, sinon redites-la plus précisément |
| Pas de son à l'alerte | cliquez une fois dans la page (les navigateurs bloquent le son avant), vérifiez le volume, et que l'unité est à moins de 3 km |
| La page ne change pas après une mise à jour | rechargement forcé : Cmd+Shift+R (Mac) ou Ctrl+Shift+R |
| Erreurs « Overpass indisponible » dans le terminal | le service OSM public est saturé ; le serveur bascule tout seul sur Photon et Nominatim |
| Analyse en mode `rules` (affiché dans l'en-tête) | pas de clé Mistral, ou quota atteint : vérifiez `MISTRAL_API_KEY` |

---

## Fonctionnalités

- **Un terminal = une unité.** Clé Ed25519 créée par le navigateur (non exportable). Chaque annonce et chaque action est signée ; le serveur refuse ce qui ne vient pas du terminal de l'unité. Une unité dont l'onglet a planté peut être reprise après 20 s.
- **Compréhension du langage.** Transcription Voxtral, analyse Mistral en JSON (plusieurs faits par phrase, correction des noms mal transcrits), repli sur des règles locales.
- **Géocodage.** Cache de zone, puis Overpass, Photon et Nominatim, avec vérification du nom. Lieux célèbres prioritaires, rues mal transcrites retrouvées par proximité. Les unités s'arrêtent toujours sur la rue.
- **Déplacements.** Itinéraires piétons par les rues (OSRM), déplacements relatifs (« 60 m au sud », « à droite au prochain bloc », demi-tour), flèche de direction, horaire militaire.
- **Ennemis.** Numérotés (E1, E2…), groupés dans la rue, regroupés en cluster au dézoom, déplaçables, neutralisables un par un, par liste, par direction ou « autour de moi ». Ennemi vu sans distance : 50 m devant.
- **Escouades.** Formées à la voix, déplacées ensemble, une seule ligne d'historique ; encart en haut à gauche.
- **Renfort.** Alerte aux unités à moins de 3 km : bandeau, bips, annonce vocale, bouton « J'y vais ».
- **Hors ligne.** Application installable qui s'ouvre sans réseau avec la dernière carte connue ; annonces et actions gardées puis envoyées au retour du réseau, avec leur heure d'origine (jusqu'à 24 h, doublons refusés).
- **Journal infalsifiable.** SQLite en ajout seul (UPDATE et DELETE refusés), chaîné par SHA-256, signatures revérifiées par « Vérifier l'intégrité ».
- **Interface.** Page d'accueil, français / anglais, thème clair / sombre, une couleur par unité.

---

## Structure

```
voice-to-map/
├── scripts/
│   ├── start.sh                lancement en une commande (serveur + tunnel)
│   ├── clear_map.py            remise à zéro de la carte
│   ├── release_unit.py         libérer une unité bloquée
│   └── fetch_landmarks.py      cache des repères OSM de la zone
├── backend/                    FastAPI
│   ├── app/
│   │   ├── main.py             point d'entrée, sert aussi le site
│   │   ├── config.py           lecture du .env
│   │   ├── state.py            journal, registre des unités, hub WebSocket
│   │   ├── pipeline.py         texte → intentions → lieux → événements → diffusion
│   │   ├── sim.py              simulation de marche des ennemis (coupée par défaut)
│   │   ├── api/routes.py       routes HTTP et WebSocket
│   │   ├── store/journal.py    journal en ajout seul, chaîné SHA-256
│   │   ├── store/units.py      unité → clé publique, couleur
│   │   ├── security/signing.py signatures Ed25519, annonces en différé
│   │   ├── speech/stt.py       transcription Voxtral
│   │   ├── intent/extractor.py analyse Mistral (JSON)
│   │   ├── intent/rules.py     analyse par règles (repli)
│   │   └── geo/                OSM (osm.py), résolution (resolver.py), géométrie, itinéraires (routing.py)
│   ├── tests/
│   └── requirements.txt
├── frontend/                   sans étape de build
│   ├── index.html              page d'accueil
│   ├── app.html                la carte
│   ├── sw.js                   service worker (hors ligne)
│   ├── manifest.webmanifest    application installable
│   ├── css/                    style.css (carte), landing.css (accueil)
│   └── js/
│       ├── app.js              panneau, historique, alertes, hors ligne
│       ├── map.js              unités, ennemis, escouades, flèches
│       ├── identity.js         clés, réservation, envois signés
│       ├── outbox.js           boîte d'envoi hors ligne
│       ├── ptt.js              push-to-talk
│       ├── socket.js           WebSocket avec reconnexion
│       ├── prefs.js            langue et thème
│       └── landing.js          page d'accueil
└── data/                       base SQLite (ignorée par git), cache des repères
```

---

## API

Toutes les routes `POST` qui modifient la carte sont signées par le terminal de l'unité (`unit`, `ts`, `nonce`, `signature`), avec `queued: true` pour une action envoyée en différé. Documentation interactive : http://localhost:8000/docs.

| Méthode | Route | Rôle |
|---|---|---|
| `GET` | `/api/config` | zone, mode du nœud, moteur d'analyse |
| `POST` | `/api/units/claim` | réserver une unité pour la clé publique du terminal |
| `POST` | `/api/units/release` | libérer son unité |
| `GET` | `/api/units` | unités réservées, avec leur couleur |
| `POST` | `/api/reports` | annonce écrite `{text}` |
| `POST` | `/api/reports/audio` | annonce vocale (formulaire avec le fichier `audio`, 5 Mo maximum) |
| `POST` | `/api/reports/confirm` | confirmer une annonce « à confirmer » |
| `POST` | `/api/alerts` | demander du renfort ; `/api/alerts/clear` pour la fin d'alerte |
| `GET` | `/api/groups` | escouades ; `POST /api/groups/leave` pour quitter la sienne |
| `GET` | `/api/enemies` | état des ennemis ; `POST /api/enemies/kill` pour en neutraliser un |
| `GET` | `/api/events?unit=` | journal complet, ou d'une unité |
| `GET` | `/api/integrity` | vérification de la chaîne et des signatures |
| `WS` | `/ws` | état depuis la dernière remise à zéro, puis un message par événement |

---

## Équipe

| Personne | Rôle | Dossiers |
|---|---|---|
| Joan | Front | `frontend/` |
| Kenzo | Backend | `backend/app/api`, `store`, `security`, `speech` |
| Thibaut | IA | `backend/app/intent`, `pipeline.py` |
| Tom | Géo et carte | `backend/app/geo`, `frontend/js/map.js`, `scripts/` |
