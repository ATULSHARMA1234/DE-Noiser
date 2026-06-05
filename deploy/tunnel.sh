#!/usr/bin/env bash
#
# Expose the local backend API on a free public HTTPS URL via a Cloudflare Quick
# Tunnel (no Cloudflare account, no domain, no credit card). Use this to connect a
# Vercel-hosted frontend to a backend running on your own machine.
#
#   ./deploy/tunnel.sh
#
# Requires the main stack to be up (./deploy/bootstrap.sh) and healthy first.
#
set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE="docker compose -f docker-compose.yml -f docker-compose.tunnel.yml"

echo "→ Starting Cloudflare quick tunnel to the API..."
$COMPOSE up -d cloudflared

echo "→ Waiting for the public URL (a few seconds)..."
URL=""
for _ in $(seq 1 30); do
  URL=$($COMPOSE logs cloudflared 2>/dev/null | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | head -1)
  [ -n "$URL" ] && break
  sleep 2
done

if [ -z "$URL" ]; then
  echo "✗ Could not find the tunnel URL yet. Check:  $COMPOSE logs cloudflared"
  exit 1
fi

WSS="wss://${URL#https://}"
cat <<EOF

  ┌─ Backend is live on the public internet ───────────────────────────────────
  │  Public URL:   $URL
  │  Health:       $URL/health
  │
  │  1) In Vercel → Project → Settings → Environment Variables, set:
  │       NEXT_PUBLIC_API_BASE = $URL
  │       NEXT_PUBLIC_WS_BASE  = $WSS
  │     then redeploy the Vercel project.
  │
  │  2) Allow your Vercel origin through CORS — edit .env:
  │       CORS_ALLOWED_ORIGINS=https://<your-app>.vercel.app
  │     then apply:  docker compose up -d api
  └─────────────────────────────────────────────────────────────────────────────

  Notes:
  • The backend is live only while this machine + tunnel keep running.
  • Quick-tunnel URLs change every restart. For a stable URL, use a named tunnel
    (free Cloudflare account) — see DEPLOY.md.
  • Stop the tunnel:  $COMPOSE stop cloudflared
EOF
