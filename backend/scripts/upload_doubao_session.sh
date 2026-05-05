#!/bin/bash
# Upload local doubao session to vm03's browser-service.
#
# Usage:
#   bash backend/scripts/upload_doubao_session.sh
#
# Assumes ~/.ssh/config has `21v-bastion` host configured, and that
# the user has already run `python backend/scripts/doubao_login.py`
# locally to produce backend/data/browser_sessions/doubao.json.

set -euo pipefail

SESSION_FILE="backend/data/browser_sessions/doubao.json"
REMOTE_PATH="/opt/browser-service/data/browser_sessions/doubao.json"
VM_IP="172.80.40.103"
VM_PASS="REDACTED_VM_PASSWORD"

if [[ ! -f "$SESSION_FILE" ]]; then
    echo "Missing $SESSION_FILE."
    echo "Run first: .venv/bin/python backend/scripts/doubao_login.py"
    exit 1
fi

echo "Local session size: $(wc -c < "$SESSION_FILE") bytes"
echo "Cookies in session:  $(jq '.cookies | length' "$SESSION_FILE")"
echo "Origins in session:  $(jq '.origins | length' "$SESSION_FILE")"
echo

echo "Uploading to vm03 via 21v-bastion..."
SSHPASS="$VM_PASS" sshpass -e scp \
    -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    -o LogLevel=ERROR -o PreferredAuthentications=password -o PubkeyAuthentication=no \
    -o ProxyJump=21v-bastion \
    "$SESSION_FILE" "root@${VM_IP}:${REMOTE_PATH}"

echo
echo "Verifying on vm03..."
SSHPASS="$VM_PASS" sshpass -e ssh \
    -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    -o LogLevel=ERROR -o PreferredAuthentications=password -o PubkeyAuthentication=no \
    -J 21v-bastion "root@${VM_IP}" \
    "ls -la ${REMOTE_PATH}; curl -s http://127.0.0.1:8092/sessions/doubao"
echo
echo "Done. Now test with:"
echo "  curl -X POST http://172.80.40.103:8092/search -H 'Content-Type: application/json' -d '{\"engine\":\"doubao\",\"query\":\"小米汽车\"}'"
