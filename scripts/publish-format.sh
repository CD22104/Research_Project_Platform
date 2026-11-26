#!/bin/bash
# Copy the board format spec to the shared disk so sessions on every server can read it.
# The repo copy (docs/board-format.md) is the one to edit; the shared copy is generated.
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)/skill/research-board/references/board-format.md"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# default: <shared_dir>/BOARD_FORMAT.md from the configuration (rbconfig); a copy for old projects whose board/FORMAT.md points there
DST="${1:-$(python3 -c "import sys; sys.path.insert(0, '$ROOT/skill/research-board/scripts'); import rbconfig; print(rbconfig.load()['shared_dir'])")/BOARD_FORMAT.md}"
TMP="$DST.tmp.$$"
{ echo "<!-- 由 $(hostname -s):$SRC 生成（commit $(git -C "$(dirname "$SRC")" rev-parse --short HEAD)），不要手改；改正式版后重新运行 scripts/publish-format.sh -->"; echo; cat "$SRC"; } > "$TMP"
mv -f "$TMP" "$DST"   # write then rename: the shared disk is soft-mounted
echo "published $DST ($(wc -l < "$DST") lines)"
