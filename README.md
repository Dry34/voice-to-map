# Voice-to-Map

Challenge Lysk · EDTH Paris 2026 (20–22 novembre).

Une app web qui écoute de courtes annonces vocales (« I am south of the church ») et déplace en quelques secondes le marqueur de l'unité sur une carte partagée, avec horodatage et historique rejouable.

**Plan, architecture et décisions :** https://claude.ai/artifact/FxDnkHTYujgVLEJAjhvZs2

## Démarrer

```bash
cp .env.example .env              # puis renseigner MISTRAL_API_KEY (facultatif)
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload
```

Ouvrir http://localhost:8000 (page d'accueil), puis « Ouvrir la carte » (http://localhost:8000/app.html). Le backend sert aussi le front.

Sans clé Mistral, l'analyse passe par les règles de `backend/app/intent/rules.py`. Le bandeau en haut du panneau indique le moteur utilisé (`mistral` ou `rules`).

Tests : `cd backend && .venv/bin/python -m pytest -q`

Cache des repères de la zone (recommandé, et indispensable pour le relais) :

```bash
backend/.venv/bin/python scripts/fetch_landmarks.py
```

## Structure

```
voice-to-map/
├── backend/                    FastAPI : le central (et le relais, même code)
│   ├── app/
│   │   ├── main.py             point d'entrée, sert aussi le front
│   │   ├── config.py           lecture du .env
│   │   ├── state.py            journal + hub WebSocket partagés
│   │   ├── pipeline.py         texte → intention → lieu → événement → diffusion
│   │   ├── api/routes.py       routes HTTP et WebSocket                [B]
│   │   ├── store/journal.py    journal SQLite en ajout seul, chaîné SHA-256  [B]
│   │   ├── store/units.py      registre unité → clé publique           [B]
│   │   ├── security/signing.py vérification des signatures Ed25519     [B]
│   │   ├── speech/stt.py       transcription Voxtral                   [B]
│   │   ├── intent/
│   │   │   ├── extractor.py    Mistral en JSON, repli sur les règles   [C]
│   │   │   └── rules.py        analyse par règles (EN/FR)              [C]
│   │   ├── geo/
│   │   │   ├── osm.py          cache de zone, Overpass, Photon, Nominatim [D]
│   │   │   ├── resolver.py     intention → point + incertitude         [D]
│   │   │   └── geometry.py     distances, caps, projections            [D]
│   │   └── relay/README.md     mode relais (samedi)                    [B]
│   ├── tests/
│   └── requirements.txt
├── frontend/                   carte Leaflet, sans build               [A]
│   ├── index.html              page d'accueil (démo animée, fonctionnement, sécurité)
│   ├── app.html                la carte
│   ├── js/prefs.js             langue FR/EN et thème clair/sombre (accueil et carte)
│   ├── css/style.css
│   └── js/
│       ├── app.js              formulaire, historique, intégrité
│       ├── identity.js         clés Ed25519 du terminal, réservation d'unité, envoi signé
│       ├── ptt.js              push-to-talk (bouton maintenu ou barre espace)
│       ├── map.js              marqueur, cercle d'incertitude, tracé par unité
│       └── socket.js           WebSocket avec reconnexion
├── scripts/fetch_landmarks.py  extraction des repères OSM de la zone   [D]
└── data/                       base SQLite (ignorée par git) et cache des repères
```

## API

| Méthode | Route | Rôle |
|---|---|---|
| `POST` | `/api/units/claim` | réserve une unité pour la clé publique du terminal (signé) |
| `POST` | `/api/units/release` | libère l'unité (signé par son terminal) |
| `GET` | `/api/units` | unités déjà réservées |
| `GET` | `/api/groups` | escouades |
| `POST` | `/api/groups/leave` | quitter son escouade (signé) |
| `GET` | `/api/enemies` | état des ennemis (vivant, dernière position) |
| `POST` | `/api/enemies/kill` | neutraliser un ennemi (signé) |
| `POST` | `/api/reports` | `{unit, text, ts, nonce, signature}` → événement créé |
| `POST` | `/api/reports/audio` | formulaire `unit`, `ts`, `nonce`, `signature` + fichier `audio` (webm, m4a, ogg, 5 Mo max) → transcription Voxtral puis même traitement |
| `GET` | `/api/events?unit=` | historique complet ou par unité |
| `GET` | `/api/integrity` | rejoue la chaîne de hash : `{"ok", "count", "broken_at"}` |
| `GET` | `/api/config` | zone, mode du nœud, moteur d'analyse |
| `WS` | `/ws` | `snapshot` à la connexion, puis un message `event` par annonce |

## Ce qui marche

- Un terminal = une unité. Au premier lancement, le navigateur crée une paire de clés Ed25519 (clé privée non exportable, gardée dans IndexedDB) et réserve son unité. Chaque annonce est signée : le serveur refuse une annonce pour ALPHA 1 qui ne vient pas du terminal d'ALPHA 1, et un indicatif prononcé différent est ignoré. « Quitter l'unité » retire son point de toutes les cartes. La carte montre toutes les unités, la sienne en avant. « Poste de commandement » : tout voir sans unité. Unité bloquée (clé perdue) : `scripts/release_unit.py "ALPHA 1"`.
- Ennemis : « Contact, 5 ennemis près de l'Hôtel-Dieu » crée E1…E5, alignés sur la rue la plus proche, regroupés en cluster au dézoom. « E3 au sud du pont » le déplace, « E3 neutralisé » (ou le bouton sur le losange, signé) le grise. « J'ai tué l'ennemi » sans numéro : le plus proche.
- « Je vais vers Bravo 2 » : l'unité est placée sur la dernière position de BRAVO 2.
- Plusieurs faits par annonce : « 4 ennemis à Notre-Dame, et moi je suis à Châtelet ». Neutralisations multiples : « E1, E2 et E3 », « les cinq ennemis », « autour de moi », « tous ».
- Escouades : « Moi et Tom 2, on est en groupe » ; quand l'un bouge, l'escouade suit (6 m d'écart). « Je quitte le groupe » ou le bouton dans l'encart en haut à gauche.
- Déplacements tracés par les rues (itinéraire piéton OSRM). « Je marche 60 m vers le sud ». Horaire militaire : « ennemi à 6h, 100 m » (12h = sens de marche).
- Une couleur par unité, attribuée par le serveur. Une unité dont l'onglet a planté peut être reprise après 20 s.
- Flèche de direction par unité (sens de marche, « je regarde à 3h »). Navigation relative : « je suis la route et tourne à droite au prochain bloc ». Ennemis « sur le chemin » répartis le long du trajet.
- Page d'accueil, interface en français et en anglais (drapeau), mode clair et sombre (soleil / lune). Liens directs : `?lang=en`, `?theme=dark`.
- La remise à zéro vide aussi l'historique affiché ; « Effacer (local) » ne vide que son propre écran.
- Annonce « à confirmer » avec un lieu trouvé : bouton « Confirmer », ou « confirmé » à la voix. Les unités s'arrêtent toujours sur la rue (jamais dans un bâtiment). Ennemi vu sans distance : 50 m devant, dans la rue.
- Remise à zéro de la carte (unités et ennemis, l'historique reste) : `backend/.venv/bin/python scripts/clear_map.py`.

- Push-to-talk : maintenir le bouton (ou la barre espace), parler, relâcher. L'audio part à Voxtral (`STT_MODEL`, `STT_LANGUAGE=fr`, qui comprend aussi l'anglais), puis suit le même chemin qu'une annonce tapée.
- Un indicatif prononcé (« Alpha 1, … ») remplace l'unité choisie dans le formulaire.
- Analyse avec `voxtral-small-2507` en mode JSON (60 requêtes par minute sur notre clé), repli automatique sur les règles locales en cas d'erreur ou de quota.
- Géocodage : cache de zone, puis Overpass (coupé 2 min après un échec), puis Photon et Nominatim. Les noms propres sont cherchés jusqu'à 10 km du centre.

- Une annonce tapée est analysée, placée et diffusée en direct à tous les navigateurs ouverts.
- Relations gérées : `at`, `near`, cardinales (`south_of`…), `towards`, `along`, `intersection`.
- Journal en ajout seul : SQLite refuse `UPDATE` et `DELETE`, et le bouton « Vérifier l'intégrité » rejoue la chaîne.
- Une annonce non placée est enregistrée « à confirmer » au lieu de déplacer le marqueur.

## Équipe

| | Rôle | Dossiers |
|---|---|---|
| A | Front | `frontend/` |
| B | Backend | `backend/app/api`, `store`, `speech`, `relay` |
| C | IA | `backend/app/intent` |
| D | Géo | `backend/app/geo`, `scripts/` |

## Configuration

Toutes les variables sont dans `.env.example`. Ne jamais committer `.env`.
