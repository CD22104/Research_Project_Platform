#!/bin/bash
# Copy the board tool (facts pack and record check) to the shared disk, so Claude Code hooks on the other
# servers can run it by path:  /usr/bin/python3 /shared/ssd/dchen/_shared/tools/board/boardtool.py brief --hook
# The repo copy is the one to edit; run the tests before publishing. Standard library only.
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"
DST="${1:-/shared/ssd/dchen/_shared/tools/board}"
TMP="$DST.tmp.$$"
mkdir -p "$TMP"
cp "$SRC/skill/research-board/scripts/boardtool.py" "$SRC/skill/research-board/scripts/projects.py" "$TMP/"
echo '{"projects_root": "/shared/ssd/dchen", "servers": []}' > "$TMP/servers.json"
echo "由 a100:~/gpu-board 发布（commit $(git -C "$SRC" rev-parse --short HEAD)），不要手改。在其他服务器上只用 brief 和 check。" > "$TMP/SOURCE.txt"
rm -rf "$DST.old"
[ -d "$DST" ] && mv "$DST" "$DST.old"
mv "$TMP" "$DST"
rm -rf "$DST.old"
echo "published $DST"
