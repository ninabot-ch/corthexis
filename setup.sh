#!/bin/sh
# CortHeXis — first-run setup of the Docker install (idempotent).
#
#   ./setup.sh [NOTES_FOLDER]
#
# 1. writes .env from .env.example: a random token, your uid/gid, the notes folder;
# 2. builds the image;
# 3. asks the licence question of the default embedding model (Gemma Terms of Use) and
#    downloads the model you chose — EmbeddingGemma-300m, or multilingual-e5-base (MIT).
# Unattended: CORTHEXIS_ACCEPT_GEMMA_TERMS=1|0 ./setup.sh /path/to/notes
# Then: docker compose up -d  → http://localhost:8420
set -eu
cd "$(dirname "$0")"

say() { printf '%s\n' "$*"; }
setvar() {  # setvar KEY VALUE — replace or append KEY=VALUE in .env
  k=$1; v=$2
  if grep -q "^$k=" .env; then
    tmp=$(mktemp); awk -v k="$k" -v v="$v" 'BEGIN{FS=OFS="="} $1==k{$0=k"="v} {print}' .env >"$tmp" && cat "$tmp" >.env && rm -f "$tmp"
  else
    printf '%s=%s\n' "$k" "$v" >>.env
  fi
}
getvar() { sed -n "s/^$1=//p" .env | tail -n1; }

command -v docker >/dev/null 2>&1 || { say "docker is required (https://docs.docker.com/get-docker/)"; exit 1; }
docker compose version >/dev/null 2>&1 || { say "docker compose v2 is required"; exit 1; }

[ -f .env ] || { cp .env.example .env; chmod 600 .env; say "created .env"; }

if [ -z "$(getvar CORTHEXIS_TOKEN)" ]; then
  tok=$(od -An -tx1 -N32 /dev/urandom | tr -d ' \n')
  setvar CORTHEXIS_TOKEN "$tok"
  say "generated CORTHEXIS_TOKEN (in .env)"
fi

if [ $# -ge 1 ]; then
  notes=$1
  [ -d "$notes" ] || { say "no such folder: $notes"; exit 1; }
  notes=$(cd "$notes" && pwd)
  setvar CORTHEXIS_NOTES "$notes"
fi
notes=$(getvar CORTHEXIS_NOTES)
case "$notes" in ./*|../*) notes_abs=$(cd "$notes" 2>/dev/null && pwd || echo "$notes") ;; *) notes_abs=$notes ;; esac
if [ -d "$notes_abs" ]; then
  setvar CORTHEXIS_UID "$(stat -c %u "$notes_abs" 2>/dev/null || stat -f %u "$notes_abs")"
  setvar CORTHEXIS_GID "$(stat -c %g "$notes_abs" 2>/dev/null || stat -f %g "$notes_abs")"
fi
[ -n "${CORTHEXIS_ACCEPT_GEMMA_TERMS:-}" ] && setvar CORTHEXIS_ACCEPT_GEMMA_TERMS "$CORTHEXIS_ACCEPT_GEMMA_TERMS"
say "notes folder: $notes_abs (uid $(getvar CORTHEXIS_UID))"

say "building the image…"
docker compose build -q corthexis

say ""
if [ -t 0 ] && [ -z "$(getvar CORTHEXIS_ACCEPT_GEMMA_TERMS)" ]; then
  docker compose run --rm corthexis-embed-fetch python /opt/corthexis/models.py setup --interactive
else
  docker compose run --rm corthexis-embed-fetch python /opt/corthexis/models.py setup
fi

say ""
say "ready. Start it:   docker compose up -d"
say "dashboard:         $(getvar CORTHEXIS_PUBLIC_URL)  (token: grep CORTHEXIS_TOKEN .env)"
say "connect an agent:  see README.md, \"Connect your agent\""
