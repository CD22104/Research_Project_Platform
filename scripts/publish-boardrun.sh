#!/bin/bash
# Copy the run-record module to the shared disk, where every project uses it by path
# (same path on all servers):  uv add --editable /shared/ssd/dchen/_shared/tools/boardrun
# The repo copy (boardrun/) is the one to edit; run the tests before publishing.
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)/skill/research-board/scripts/boardrun"
DST="${1:-/shared/ssd/dchen/_shared/tools/boardrun}"
TMP="$DST.tmp.$$"
mkdir -p "$TMP/boardrun"
cp "$SRC/pyproject.toml" "$TMP/"
cp "$SRC/boardrun/__init__.py" "$SRC/boardrun/__main__.py" "$TMP/boardrun/"
echo "由 a100:~/gpu-board/boardrun 发布（commit $(git -C "$SRC" rev-parse --short HEAD)），不要手改。用法见 boardrun/__init__.py 开头。" > "$TMP/SOURCE.txt"
rm -rf "$DST.old"
[ -d "$DST" ] && mv "$DST" "$DST.old"
mv "$TMP" "$DST"   # swap in the new copy at once; the shared disk is soft-mounted
rm -rf "$DST.old"
echo "published $DST"
