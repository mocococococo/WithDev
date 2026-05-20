#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PROJECT_ID="withdev-dev"
REGION="asia-northeast1"
CLOUD_RUN_SERVICE="withdev-dev"
ARTIFACT_REGISTRY_REPOSITORY="withdev-dev"
ARTIFACT_IMAGE_NAME="frontend"
ENV_FILE="frontend/.env.local"

info() {
  printf '[withdev] %s\n' "$1"
}

fail() {
  printf '[withdev] ERROR: %s\n' "$1" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "$1 is required but was not found."
}

load_vite_env() {
  local line key value

  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"

    case "$line" in
      '' | \#*) continue ;;
    esac

    line="${line#export }"
    key="${line%%=*}"
    value="${line#*=}"

    if [ "$key" = "$line" ]; then
      continue
    fi

    case "$key" in
      VITE_*) ;;
      *) continue ;;
    esac

    case "$value" in
      \"*\") value="${value#\"}"; value="${value%\"}" ;;
      \'*\') value="${value#\'}"; value="${value%\'}" ;;
    esac

    export "$key=$value"
  done < "$ENV_FILE"
}

require_env() {
  local name="$1"
  [ -n "${!name:-}" ] || fail "$name is missing in $ENV_FILE."
}

require_command git
require_command gcloud
require_command docker

[ -f "$ENV_FILE" ] || fail "$ENV_FILE was not found. Create it from frontend/.env.example first."

load_vite_env

require_env VITE_FIREBASE_API_KEY
require_env VITE_FIREBASE_AUTH_DOMAIN
require_env VITE_FIREBASE_PROJECT_ID
require_env VITE_FIREBASE_STORAGE_BUCKET
require_env VITE_FIREBASE_MESSAGING_SENDER_ID
require_env VITE_FIREBASE_APP_ID

GIT_SHA="$(git rev-parse --short=12 HEAD)"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REGISTRY_REPOSITORY}/${ARTIFACT_IMAGE_NAME}:${GIT_SHA}"

info "Deploying frontend only to development."
info "Project: ${PROJECT_ID}"
info "Cloud Run service: ${CLOUD_RUN_SERVICE}"
info "Image: ${IMAGE}"

gcloud config set project "$PROJECT_ID" >/dev/null
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

docker build \
  --build-arg VITE_APP_ENV=development \
  --build-arg VITE_GIT_SHA="$GIT_SHA" \
  --build-arg VITE_FIREBASE_API_KEY="$VITE_FIREBASE_API_KEY" \
  --build-arg VITE_FIREBASE_AUTH_DOMAIN="$VITE_FIREBASE_AUTH_DOMAIN" \
  --build-arg VITE_FIREBASE_PROJECT_ID="$VITE_FIREBASE_PROJECT_ID" \
  --build-arg VITE_FIREBASE_STORAGE_BUCKET="$VITE_FIREBASE_STORAGE_BUCKET" \
  --build-arg VITE_FIREBASE_MESSAGING_SENDER_ID="$VITE_FIREBASE_MESSAGING_SENDER_ID" \
  --build-arg VITE_FIREBASE_APP_ID="$VITE_FIREBASE_APP_ID" \
  --tag "$IMAGE" \
  frontend

docker push "$IMAGE"

gcloud run deploy "$CLOUD_RUN_SERVICE" \
  --image "$IMAGE" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --platform managed \
  --allow-unauthenticated \
  --port 8080 \
  --quiet

SERVICE_URL="$(gcloud run services describe "$CLOUD_RUN_SERVICE" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --platform managed \
  --format 'value(status.url)')"

info "Development frontend deployment completed."
info "URL: ${SERVICE_URL}"
