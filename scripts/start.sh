#!/usr/bin/env bash
# Lance Voice-to-Map en une commande.
#
#   ./scripts/start.sh             serveur sur http://localhost:8000
#   ./scripts/start.sh --tunnel    + lien public HTTPS (Cloudflare) pour les autres appareils
#   PORT=8001 ./scripts/start.sh   autre port
#
# Crée l'environnement Python et le .env au premier lancement. Ctrl+C arrête tout.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${PORT:-8000}"
TUNNEL=false
[[ "${1:-}" == "--tunnel" ]] && TUNNEL=true

cd "$ROOT"

# 1. Python 3.11+
PY="${PYTHON:-python3}"
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
  echo "Python 3.11 ou plus récent est nécessaire (trouvé : $("$PY" --version 2>&1))." >&2
  exit 1
fi

# 2. Environnement et dépendances (une seule fois, puis à chaque changement de requirements.txt)
if [[ ! -x backend/.venv/bin/python ]]; then
  echo "Création de l'environnement Python…"
  "$PY" -m venv backend/.venv
fi
if [[ ! -f backend/.venv/.installed ]] || [[ backend/requirements.txt -nt backend/.venv/.installed ]]; then
  echo "Installation des dépendances…"
  backend/.venv/bin/pip install -q --upgrade pip
  backend/.venv/bin/pip install -q -r backend/requirements.txt
  touch backend/.venv/.installed
fi

# 3. Configuration
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Fichier .env créé. Ajoutez votre clé dans MISTRAL_API_KEY (sans clé, l'analyse passe par les règles locales)."
fi

# 4. Port libre
if lsof -tiTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Le port $PORT est déjà utilisé (un serveur tourne peut-être déjà)." >&2
  echo "Pour l'arrêter : kill \$(lsof -tiTCP:$PORT -sTCP:LISTEN)" >&2
  exit 1
fi

# 5. Serveur (et tunnel)
cleanup() { kill 0 2>/dev/null || true; }
trap cleanup EXIT INT TERM

(cd backend && exec .venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port "$PORT") &

for _ in $(seq 40); do
  curl -s -o /dev/null "http://localhost:$PORT/api/config" && break
  sleep 0.5
done
echo
echo "Voice-to-Map tourne : http://localhost:$PORT  (carte : http://localhost:$PORT/app.html)"

if $TUNNEL; then
  if ! command -v cloudflared >/dev/null; then
    echo "cloudflared n'est pas installé : brew install cloudflared (macOS) ou https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/" >&2
  else
    LOG="$(mktemp)"
    cloudflared tunnel --no-autoupdate --protocol http2 --url "http://localhost:$PORT" >"$LOG" 2>&1 &
    for _ in $(seq 60); do
      URL="$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$LOG" | head -1 || true)"
      [[ -n "$URL" ]] && break
      sleep 1
    done
    if [[ -n "${URL:-}" ]]; then
      echo "Lien public (à envoyer à l'équipe) : $URL"
      echo "Il peut mettre 10 à 30 secondes à répondre la première fois."
    else
      echo "Le tunnel n'a pas donné d'adresse. Journal : $LOG" >&2
    fi
  fi
fi

echo "Ctrl+C pour tout arrêter."
wait
