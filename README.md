# Voice-to-Map

Challenge Lysk · EDTH Paris 2026 (20–22 novembre).

Une app web qui écoute de courtes annonces vocales (« I am south of the church ») et déplace en quelques secondes le marqueur de l'unité sur une carte partagée, avec horodatage et historique rejouable.

## Architecture

- **Central** (`backend/`) : FastAPI, WebSocket, journal SQLite chaîné. Source de vérité.
- **Relais** : le même backend en mode relais, autonome quand le central est injoignable, puis resynchronisé.
- **Front** (`frontend/`) : carte Leaflet, push-to-talk, historique et replay.
- **Pipeline** : transcription (Voxtral) → intention (Mistral, JSON) → géo-résolution (Overpass / Nominatim) → événement → carte.

Plan et décisions : https://claude.ai/artifact/FxDnkHTYujgVLEJAjhvZs2

## Équipe

| | Rôle |
|---|---|
| A | Front |
| B | Backend |
| C | IA |
| D | Géo |

## Configuration

Copier `.env.example` en `.env` et renseigner la clé Mistral. Ne jamais committer `.env`.
