#!/bin/bash
# Copy the run-record module to the shared disk, where every project uses it by path
# (same path on all servers):  uv add --editable <tools_dir>/boardrun
# The repo copy (boardrun/) is the one to edit; run the tests before publishing.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/skill/research-board/scripts/boardrun"
# default: <tools_dir>/boardrun from the configuration (rbconfig)
DST="${1:-$(python3 -c "import sys; sys.path.insert(0, '$ROOT/skill/research-board/scripts'); import rbconfig; print(rbconfig.load()['tools_dir'])")/boardrun}"
TMP="$DST.tmp.$$"
mkdir -p "$TMP/boardrun"
cp "$SRC/pyproject.toml" "$TMP/"
cp "$SRC/boardrun/__init__.py" "$SRC/boardrun/__main__.py" "$TMP/boardrun/"
echo "由 $(hostname -s):$SRC 发布（commit $(git -C "$SRC" rev-parse --short HEAD)），不要手改。用法见 boardrun/__init__.py 开头。" > "$TMP/SOURCE.txt"
rm -rf "$DST.old"
[ -d "$DST" ] && mv "$DST" "$DST.old"
mv "$TMP" "$DST"   # swap in the new copy at once; the shared disk is soft-mounted
rm -rf "$DST.old"
echo "published $DST"
