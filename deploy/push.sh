#!/usr/bin/env bash
# Run this from your LOCAL machine to push code to the VPS.
# Usage: bash deploy/push.sh
set -e

SERVER="root@72.62.97.102"
REMOTE_DIR="/opt/policy-maker"

echo "==> Syncing code to $SERVER:$REMOTE_DIR"

# Ensure remote dirs exist
ssh "$SERVER" "mkdir -p $REMOTE_DIR/src/server $REMOTE_DIR/deploy"

rsync -avz --delete \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.env' \
  --exclude 'policy_maker.db' \
  src/server/ \
  "$SERVER:$REMOTE_DIR/src/server/"

rsync -avz deploy/ "$SERVER:$REMOTE_DIR/deploy/"

echo ""
echo "==> Done. If this is the first deploy, SSH in and run:"
echo "    bash $REMOTE_DIR/deploy/server_setup.sh"
echo ""
echo "==> To restart the service after a code update:"
echo "    ssh $SERVER systemctl restart policy-maker"
