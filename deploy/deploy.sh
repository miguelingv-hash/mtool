#!/usr/bin/env bash
#
# deploy/deploy.sh — Script de despliegue idempotente
#
# Lo invoca el workflow de GitHub Actions, pero también puedes ejecutarlo
# manualmente por SSH:
#
#   ssh ec2-user@<IP> 'cd ~/corporate-app && ./deploy/deploy.sh'
#
# Opciones (variables de entorno):
#   FORCE_REBUILD=1   → fuerza --no-cache (más lento pero limpia caché Docker)
#   NO_PULL=1         → salta git pull (útil si llamas tras un pull manual)
#
set -euo pipefail

cd "$(dirname "$0")/.."   # ir a la raíz del repo (~/corporate-app)
REPO_ROOT="$(pwd)"
COMPOSE_FILE="deploy/docker-compose.yml"
ENV_FILE="deploy/.env.production"

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  🚀 DEPLOY · Corporate App · $(date -Iseconds)"
echo "════════════════════════════════════════════════════════════════"
echo "  Repo:        $REPO_ROOT"
echo "  Compose:     $COMPOSE_FILE"
echo "  Env file:    $ENV_FILE"
echo "════════════════════════════════════════════════════════════════"
echo ""

# Validaciones previas
if [ ! -f "$ENV_FILE" ]; then
  echo "❌ No existe $ENV_FILE. Créalo a partir de deploy/.env.production.example"
  exit 1
fi

if ! command -v docker >/dev/null; then
  echo "❌ docker no está instalado en esta máquina"
  exit 1
fi

# 1) Descarta cambios locales no committeados (PDFs generados, uploads, etc.)
if [ -z "${NO_PULL:-}" ]; then
  echo "▶ [1/5] git fetch + reset"
  git fetch origin
  git reset --hard origin/main
else
  echo "▶ [1/5] (saltado por NO_PULL=1)"
fi

# 2) Detecta qué hay que rebuildear según los archivos que cambiaron
PREV_HEAD="${GITHUB_PREVIOUS_SHA:-HEAD~1}"
echo ""
echo "▶ [2/5] detectando cambios desde $PREV_HEAD"

BUILD_BACKEND=0
BUILD_FRONTEND=0

if git rev-parse --verify "$PREV_HEAD" >/dev/null 2>&1; then
  CHANGED="$(git diff --name-only "$PREV_HEAD" HEAD || true)"
else
  CHANGED=""
fi

if [ -n "${FORCE_REBUILD:-}" ] || [ -z "$CHANGED" ]; then
  # Primer deploy o forzado → build todo
  BUILD_BACKEND=1
  BUILD_FRONTEND=1
  echo "  → build completo (FORCE_REBUILD o primer deploy)"
else
  echo "$CHANGED" | grep -E '^(backend/|deploy/backend\.Dockerfile)' >/dev/null && BUILD_BACKEND=1 || true
  echo "$CHANGED" | grep -E '^(frontend/|deploy/frontend\.Dockerfile|deploy/Caddyfile)' >/dev/null && BUILD_FRONTEND=1 || true

  [ "$BUILD_BACKEND" = "1" ] && echo "  → backend cambió: rebuild"
  [ "$BUILD_FRONTEND" = "1" ] && echo "  → frontend cambió: rebuild"
  [ "$BUILD_BACKEND" = "0" ] && [ "$BUILD_FRONTEND" = "0" ] && echo "  → solo cambios fuera de backend/frontend: no rebuild"
fi

# 3) Build
echo ""
echo "▶ [3/5] build"

BUILD_FLAGS=""
[ -n "${FORCE_REBUILD:-}" ] && BUILD_FLAGS="--no-cache"

if [ "$BUILD_BACKEND" = "1" ]; then
  docker-compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" build $BUILD_FLAGS backend
fi
if [ "$BUILD_FRONTEND" = "1" ]; then
  docker-compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" build $BUILD_FLAGS frontend
fi

# 4) Up (siempre, para asegurar que está corriendo)
echo ""
echo "▶ [4/5] up -d"

if [ "$BUILD_BACKEND" = "1" ] || [ "$BUILD_FRONTEND" = "1" ]; then
  docker-compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d --force-recreate
else
  docker-compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d
fi

# 5) Healthcheck
echo ""
echo "▶ [5/5] healthcheck"
sleep 8

docker-compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" ps

# Smoke test: backend responde
APP_URL="$(grep '^APP_URL=' "$ENV_FILE" | cut -d'=' -f2 | tr -d ' ')"
if [ -n "$APP_URL" ]; then
  echo ""
  echo "▶ smoke test: $APP_URL/api/auth/me"
  HTTP_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$APP_URL/api/auth/me" || echo "000")"
  if [ "$HTTP_CODE" = "401" ]; then
    echo "  ✅ Backend responde (401 esperado sin sesión)"
  else
    echo "  ⚠️  HTTP $HTTP_CODE (esperaba 401) — revisa logs:"
    docker-compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" logs --tail=30 backend
    exit 1
  fi
fi

# Limpia imágenes viejas para que no se llene el disco EBS
docker image prune -f >/dev/null 2>&1 || true

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  ✅ DEPLOY COMPLETADO · $(date -Iseconds)"
echo "════════════════════════════════════════════════════════════════"
