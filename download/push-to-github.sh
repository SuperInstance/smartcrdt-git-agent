#!/bin/bash
# Push SmartCRDT Git-Agent and SmartCRDT fleet updates to GitHub
# 
# Usage: 
#   export GITHUB_TOKEN="your_personal_access_token"
#   bash push-to-github.sh
#
# The token needs: repo scope (public_repo for public repos)

set -e

if [ -z "$GITHUB_TOKEN" ]; then
    echo "ERROR: GITHUB_TOKEN environment variable not set"
    echo "Please run: export GITHUB_TOKEN='your_github_personal_access_token'"
    echo ""
    echo "To create a token:"
    echo "  1. Go to https://github.com/settings/tokens"
    echo "  2. Click 'Generate new token (classic)'"
    echo "  3. Select 'repo' scope"
    echo "  4. Generate and copy the token"
    exit 1
fi

# Configure git to use token
git config --global credential.helper store
echo "https://$GITHUB_TOKEN:x-oauth-basic@github.com" > ~/.git-credentials
chmod 600 ~/.git-credentials

echo "=== Step 1: Push smartcrdt-git-agent ==="
SMARTCRDT_AGENT_DIR="/home/z/my-project/download/smartcrdt-git-agent"
cd "$SMARTCRDT_AGENT_DIR"

# Create the repo on GitHub if it doesn't exist
REPO_EXISTS=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "Authorization: token $GITHUB_TOKEN" \
    "https://api.github.com/repos/SuperInstance/smartcrdt-git-agent")

if [ "$REPO_EXISTS" = "404" ]; then
    echo "Creating SuperInstance/smartcrdt-git-agent on GitHub..."
    curl -s -X POST \
        -H "Authorization: token $GITHUB_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{"name":"smartcrdt-git-agent","description":"Fleet Co-Captain for the SmartCRDT Monorepo — CRDT-aware commit narration, fleet bridge, workshop","public":true,"has_issues":true,"has_wiki":true}' \
        "https://api.github.com/orgs/SuperInstance/repos" | python3 -c "import sys,json; d=json.load(sys.stdin); print('Created:', d.get('full_name', d.get('message','error')))"
fi

# Set remote and push
git remote remove origin 2>/dev/null || true
git remote add origin "https://$GITHUB_TOKEN:x-oauth-basic@github.com/SuperInstance/smartcrdt-git-agent.git"
git push -u origin main
echo "smartcrdt-git-agent pushed successfully!"

echo ""
echo "=== Step 2: Push SmartCRDT fleet updates ==="
SMARTCRDT_DIR="/home/z/my-project/smartcrdt-work"
cd "$SMARTCRDT_DIR"

# Ensure remote is configured
git remote set-url origin "https://$GITHUB_TOKEN:x-oauth-basic@github.com/SuperInstance/SmartCRDT.git"

# Push the branch
git push -u origin super-z/fleet-updates
echo "SmartCRDT fleet-updates branch pushed successfully!"

echo ""
echo "=== Done! ==="
echo "smartcrdt-git-agent: https://github.com/SuperInstance/smartcrdt-git-agent"
echo "SmartCRDT PR: https://github.com/SuperInstance/SmartCRDT/compare/main...super-z/fleet-updates"
