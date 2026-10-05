#!/bin/bash
# Install and personalise the Smart Meeting Prep & Dossier Generator sidecars.
#
# Usage:
#   ./install.sh --email you@company.com [--timezone Asia/Singapore] [--target ~/.gemini/config/sidecars]
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_ROOT="${HOME}/.gemini/config/sidecars"
USER_EMAIL="$(git config user.email || true)"
TIMEZONE="Asia/Singapore"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --email)    [[ $# -ge 2 ]] || { echo "Error: --email requires a value" >&2; exit 1; }; USER_EMAIL="$2"; shift 2 ;;
    --timezone) [[ $# -ge 2 ]] || { echo "Error: --timezone requires a value" >&2; exit 1; }; TIMEZONE="$2"; shift 2 ;;
    --target)   [[ $# -ge 2 ]] || { echo "Error: --target requires a value" >&2; exit 1; }; TARGET_ROOT="$2"; shift 2 ;;
    -h|--help)  echo "Usage: $0 --email <you@company.com> [--timezone <IANA_TZ>] [--target <sidecars_dir>]"; exit 0 ;;
    *)          echo "Unknown flag: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "${USER_EMAIL}" || "${USER_EMAIL}" != *"@"* ]]; then
  echo "Error: Please provide a valid email via --email you@company.com" >&2
  exit 1
fi

USER_DOMAIN="${USER_EMAIL#*@}"

for SIDECAR in meeting-prep-dossier meeting-prep-reminder; do
  DEST_DIR="${TARGET_ROOT}/${SIDECAR}"
  mkdir -p "${DEST_DIR}"
  python3 - "${REPO_DIR}/sidecars/${SIDECAR}/sidecar.json" "${DEST_DIR}/sidecar.json" "${USER_EMAIL}" "${USER_DOMAIN}" "${TIMEZONE}" <<'PY'
import pathlib, sys
src, dst, email, domain, tz = sys.argv[1:6]
text = pathlib.Path(src).read_text(encoding="utf-8")
text = text.replace("__USER_EMAIL__", email).replace("__USER_DOMAIN__", domain).replace("__USER_TIMEZONE__", tz)
pathlib.Path(dst).write_text(text, encoding="utf-8")
PY
  echo "Installed ${DEST_DIR}/sidecar.json (email=${USER_EMAIL}, domain=${USER_DOMAIN}, tz=${TIMEZONE})"
done

if [[ -d "${REPO_DIR}/test" ]]; then
  mkdir -p "${TARGET_ROOT}/meeting-prep-dossier/test/fixtures"
  cp -f "${REPO_DIR}"/test/*.py "${REPO_DIR}"/test/*.sh "${REPO_DIR}/test/README.md" "${TARGET_ROOT}/meeting-prep-dossier/test/"
  cp -f "${REPO_DIR}/test/fixtures/world.json" "${TARGET_ROOT}/meeting-prep-dossier/test/fixtures/world.json"
  echo "Synchronised test harness to ${TARGET_ROOT}/meeting-prep-dossier/test/"
fi

echo
echo "Done! Open your Jetski Automations dashboard (sidecar://dashboard) and enable:"
echo "  1. Meeting Prep Dossier (next business day)"
echo "  2. Meeting Prep Reminder (T-1 hour)"
