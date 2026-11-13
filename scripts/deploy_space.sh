#!/bin/bash
# Publish the current commit to a Hugging Face Docker Space (the public live URL).
#   HF_TOKEN=hf_... HF_SPACE=<user>/gigbid scripts/deploy_space.sh
# The Space repo gets: the tracked source at HEAD, infra/docker/space.Dockerfile as its Dockerfile and a README
# with the Space front-matter. Secrets (GROQ_API_KEY, REVIEWER_PASSWORD, APP_SECRET_KEY, MCP_TOKENS,
# MCP_CLIENT_TOKEN, GIGSIM_API_TOKENS, GIGSIM_API_TOKEN) are set in the Space settings, never in the repo.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${HF_TOKEN:?set HF_TOKEN}" "${HF_SPACE:?set HF_SPACE=user/space}"
out=$(mktemp -d)
git archive HEAD | tar -x -C "$out"
cp infra/docker/space.Dockerfile "$out/Dockerfile"
mv "$out/README.md" "$out/REPO_README.md"
cat > "$out/README.md" <<MD
---
title: GigBid
emoji: 🧭
colorFrom: indigo
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
short_description: Multi-agent freelancer copilot (LangGraph, MCP, RAG)
---
GigBid live demo. Source, docs and results: https://github.com/saliqlabar/gigbid (deployed commit $(git rev-parse --short HEAD)).
MD
cd "$out"
git init -q -b main && git add -A && git -c user.name="$(git -C "$OLDPWD" config user.name)" \
  -c user.email="$(git -C "$OLDPWD" config user.email)" commit -qm "Deploy $(git -C "$OLDPWD" rev-parse --short HEAD)"
git push -q --force "https://user:${HF_TOKEN}@huggingface.co/spaces/${HF_SPACE}" main
echo "pushed to https://huggingface.co/spaces/${HF_SPACE}"
rm -rf "$out"
