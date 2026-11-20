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

Ouvrir http://localhost:8000. Le backend sert aussi le front.

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
│   │   ├── speech/stt.py       transcription Voxtral (samedi)          [B]
│   │   ├── intent/
│   │   │   ├── extractor.py    Mistral en JSON, repli sur les règles   [C]
│   │   │   └── rules.py        analyse par règles (EN/FR)              [C]
│   │   ├── geo/
│   │   │   ├── osm.py          cache de zone, Overpass, Nominatim      [D]
│   │   │   ├── resolver.py     intention → point + incertitude         [D]
│   │   │   └── geometry.py     distances, caps, projections            [D]
│   │   └── relay/README.md     mode relais (samedi)                    [B]
│   ├── tests/
│   └── requirements.txt
├── frontend/                   carte Leaflet, sans build               [A]
│   ├── index.html
│   ├── css/style.css
│   └── js/
│       ├── app.js              formulaire, historique, intégrité
│       ├── map.js              marqueur, cercle d'incertitude, tracé par unité
│       └── socket.js           WebSocket avec reconnexion
├── scripts/fetch_landmarks.py  extraction des repères OSM de la zone   [D]
└── data/                       base SQLite (ignorée par git) et cache des repères
```

## API

| Méthode | Route | Rôle |
|---|---|---|
| `POST` | `/api/reports` | `{"unit": "ALPHA 2", "text": "I am south of the church."}` → événement créé |
| `GET` | `/api/events?unit=` | historique complet ou par unité |
| `GET` | `/api/integrity` | rejoue la chaîne de hash : `{"ok", "count", "broken_at"}` |
| `GET` | `/api/config` | zone, mode du nœud, moteur d'analyse |
| `WS` | `/ws` | `snapshot` à la connexion, puis un message `event` par annonce |

## Ce qui marche ce soir

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
