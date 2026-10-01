#!/bin/bash
# Run one mock scenario end-to-end through a real agent.
#
#   bash run_scenario.sh next_day_mixed
#   bash run_scenario.sh t1h_window --now 2026-09-23T09:00:00+08:00
#
# The agent runs asynchronously in its own conversation. Once it finishes,
# grade it with:
#
#   python3 check_output.py --scenario next_day_mixed --show
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCENARIO="${1:?usage: run_scenario.sh <scenario> [--now ISO]}"
shift || true

OUTBOX="/tmp/meeting_prep_outbox/${SCENARIO}"
rm -rf "${OUTBOX}"
mkdir -p "${OUTBOX}"

PROMPT="$(python3 "${DIR}/build_test_prompt.py" --scenario "${SCENARIO}" "$@")"

echo "Launching agent for scenario '${SCENARIO}'..."
agentapi new-conversation --title "MeetingPrep test: ${SCENARIO}" -- "${PROMPT}"
echo
echo "When the run finishes, grade it with:"
echo "  python3 ${DIR}/check_output.py --scenario ${SCENARIO} --show"
