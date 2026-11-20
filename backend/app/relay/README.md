# Relais (prévu samedi, responsable B)

Le relais est le même backend lancé avec `NODE_MODE=relais` sur un portable du réseau local.

Ce qui reste à faire :

1. **Autonomie** : sans accès au central, il reçoit les annonces, les analyse avec un Mistral open-weight local (Ollama) ou les règles de `app/intent/rules.py`, et les place avec le cache `data/landmarks/zone.json`.
2. **Bascule côté navigateur** : le front essaie le central, puis le relais, et garde une file locale si aucun ne répond.
3. **Synchro** (dimanche) : à la reconnexion, le relais envoie ses événements au central, qui vérifie les signatures, trie par horodatage et conserve `origin_node`.
