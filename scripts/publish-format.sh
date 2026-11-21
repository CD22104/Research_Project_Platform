#!/bin/bash
# Copy the board format spec to the shared disk so sessions on every server can read it.
# The repo copy (docs/board-format.md) is the one to edit; the shared copy is generated.
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)/docs/board-format.md"
DST="${1:-/shared/ssd/dchen/_shared/BOARD_FORMAT.md}"
TMP="$DST.tmp.$$"
{ echo "<!-- 由 a100:~/gpu-board/docs/board-format.md 生成（commit $(git -C "$(dirname "$SRC")" rev-parse --short HEAD)），不要手改；改正式版后重新运行 scripts/publish-format.sh -->"; echo; cat "$SRC"; } > "$TMP"
mv -f "$TMP" "$DST"   # write then rename: the shared disk is soft-mounted
echo "published $DST ($(wc -l < "$DST") lines)"
