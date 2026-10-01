#!/bin/bash
# Install and personalize the Smart Meeting Prep & Dossier Generator sidecars.
#
# Usage:
#   ./install.sh --email you@company.com [--timezone Asia/Singapore]
#
# If --email is omitted, defaults to `git config user.email`.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_ROOT="${HOME}/.gemini/config/sidecars"
USER_EMAIL="$(git config user.email || true)"
TIMEZONE="Asia/Singapore"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --email)
      USER_EMAIL="$2"
      shift 2
      ;;
    --timezone)
      TIMEZONE="$2"
      shift 2
      ;;
    --target)
      TARGET_ROOT="$2"
      shift 2
      ;;
    -h|--help)
      echo "Usage: $0 --email <you@company.com> [--timezone <IANA_TZ>] [--target <sidecars_dir>]"
      exit 0
      ;;
    *)
      echo "Unknown flag: $1" >&2
      exit 1
      ;;
  esac
done

if [[ -z "${USER_EMAIL}" || "${USER_EMAIL}" != *"@"* ]]; then
  echo "Error: Please provide your email via --email you@company.com" >&2
  exit 1
fi

USER_DOMAIN="${USER_EMAIL#*@}"

for SIDECAR in meeting-prep-dossier meeting-prep-reminder; do
  DEST_DIR="${TARGET_ROOT}/${SIDECAR}"
  mkdir -p "${DEST_DIR}"
  sed \
    -e "s/__USER_EMAIL__/${USER_EMAIL}/g" \
    -e "s/__USER_DOMAIN__/${USER_DOMAIN}/g" \
    -e "s|__USER_TIMEZONE__|${TIMEZONE}|g" \
    "${REPO_DIR}/sidecars/${SIDECAR}/sidecar.json" > "${DEST_DIR}/sidecar.json"
  echo "Installed ${DEST_DIR}/sidecar.json (email=${USER_EMAIL}, domain=${USER_DOMAIN}, tz=${TIMEZONE})"
done

echo
echo "Done! Open your Jetski Automations dashboard (sidecar://dashboard) and enable:"
echo "  1. Meeting Prep Dossier (next business day)"
echo "  2. Meeting Prep Reminder (T-1 hour)"
