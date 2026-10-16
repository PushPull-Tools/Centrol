#!/usr/bin/env bash
# Remplace le propriétaire du dépôt GitHub (OWNER) dans tous les fichiers.
# Usage : ./tools/set-github.sh ton-compte-github
set -euo pipefail
[ $# -eq 1 ] && [[ "$1" =~ ^[A-Za-z0-9-]+$ ]] || { echo "Usage : $0 ton-compte-github"; exit 1; }
cd "$(dirname "$0")/.."
grep -rlI "OWNER"  --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=tools . | xargs sed -i "s#OWNER/centrol#$1/centrol#g; s#@OWNER#@$1#g"
sed -i "s#^CENTROL_REPO=.*#CENTROL_REPO=$1/centrol#" .env.example
echo "Dépôt réglé sur github.com/$1/centrol"
