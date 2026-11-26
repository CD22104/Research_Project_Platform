#!/usr/bin/env python3
"""Export a copy of the repository that can be shared: the tracked files only, without the private ones, in a new
git repository with a single commit, after a scan for personal information.

  python3 scripts/export.py DEST [--words WORD ...]

What is left out or replaced (they describe one person's setup): CLAUDE.md and NOTES.md (the console's own
development notes), the forwarding files at the root, platform/design.js and platform/docs/guide.md (replaced
by short generic versions). The scan looks for: host names, the projects root, the home directory and user name,
the console's name (from the configuration), the names of the projects under the projects root, the git author
name and email, and any words in ~/.config/research-board/private-words.txt (one per line). If anything is found
the export stops and lists where; nothing is committed.
"""
import argparse
import glob
import os
import pwd
import re
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "skill", "research-board", "scripts"))
import rbconfig  # noqa: E402

LEAVE_OUT = ["CLAUDE.md", "NOTES.md", "server.py", "boardtool.py", "board"]   # at the root only
REPLACE = {
    "platform/design.js": "function pageDesign() {\n  return '<h1 class=\"pt\">设计思路</h1><div class=\"card\"><div class=\"pad\">"
                          "这套做法的设计思路见仓库的 README.md 和 skill/research-board/SKILL.md。</div></div>';\n}\n",
    "platform/docs/guide.md": "# 使用说明\n\n控制台怎么用、项目怎么推进，见仓库的 README.md 和 skill/research-board/SKILL.md。\n"
                              "个人设置（项目根目录、服务器、控制台的名字和地址）写在 ~/.config/research-board/config.json，示例见 config.example.json。\n",
}
WORDS_FILE = os.path.expanduser("~/.config/research-board/private-words.txt")


def private_words(cfg=None, extra=()):
    """Strings that must not appear in a shared copy. cfg: rbconfig.load() or None (then only the rest)."""
    words = set(extra)
    if cfg:
        for s in cfg["servers"]:
            words.update([s["name"], s["host"]])
        words.update([cfg["projects_root"], cfg["console"]["name"]])
        try:
            words.update(n for n in os.listdir(cfg["projects_root"])   # project folders, not files like README.md
                         if not n.startswith((".", "_")) and os.path.isdir(os.path.join(cfg["projects_root"], n)))
        except OSError:
            pass
    home = os.path.expanduser("~")
    words.update([home, pwd.getpwuid(os.getuid()).pw_name])
    for key in ("user.name", "user.email"):
        v = subprocess.run(["git", "-C", REPO, "config", key], capture_output=True, text=True).stdout.strip()
        if v:
            words.add(v)
    if os.path.exists(WORDS_FILE):
        with open(WORDS_FILE, encoding="utf-8") as f:
            words.update(x.strip() for x in f if x.strip() and not x.startswith("#"))
    return sorted(w for w in words if isinstance(w, str) and len(w) >= 3)


def scan(root, words, skip=()):
    """[(file, line number, word)] for every word found in the text files under root (skip: relative paths)."""
    found = []
    pats = [(w, re.compile(re.escape(w))) for w in words]
    for path in sorted(glob.glob(os.path.join(root, "**", "*"), recursive=True)):
        rel = os.path.relpath(path, root)
        if not os.path.isfile(path) or rel in skip or rel.startswith(".git" + os.sep) or "__pycache__" in rel:
            continue
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.read().split("\n")
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(lines, 1):
            for w, p in pats:
                if p.search(line):
                    found.append((rel, i, w))
    return found


def export(dest, extra=(), cfg=None):
    """-> 0 when the copy is made and committed, 1 when the scan found something (dest is then removed)."""
    if os.path.exists(dest) and os.listdir(dest):
        print("export: %s is not empty" % dest, file=sys.stderr)
        return 2
    os.makedirs(dest, exist_ok=True)
    # the tracked files as they are in the working tree: nothing untracked or ignored (configs, state, logs) can come along
    tracked = subprocess.run(["git", "-C", REPO, "ls-files", "-z"], capture_output=True, check=True).stdout.decode().split("\0")
    for rel in filter(None, tracked):
        src = os.path.join(REPO, rel)
        if os.path.isfile(src):
            os.makedirs(os.path.dirname(os.path.join(dest, rel)), exist_ok=True)
            shutil.copy2(src, os.path.join(dest, rel))
    for rel in LEAVE_OUT:
        p = os.path.join(dest, rel)
        if os.path.isfile(p):
            os.remove(p)
    for rel, text in REPLACE.items():
        with open(os.path.join(dest, rel), "w", encoding="utf-8") as f:
            f.write(text)
    found = scan(dest, private_words(cfg, extra))
    if found:
        for rel, line, word in found[:200]:
            print("export: %s:%d contains %r" % (rel, line, word), file=sys.stderr)
        print("export: %d places with personal information; nothing exported" % len(found), file=sys.stderr)
        shutil.rmtree(dest)
        return 1
    env = dict(os.environ, GIT_AUTHOR_NAME="research-board", GIT_AUTHOR_EMAIL="research-board@example.invalid",
               GIT_COMMITTER_NAME="research-board", GIT_COMMITTER_EMAIL="research-board@example.invalid")
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"], ["git", "commit", "-q", "-m", "research-board: the console and the skill"]):
        subprocess.run(cmd, cwd=dest, check=True, env=env)
    print("exported to %s (one commit)" % dest)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dest")
    ap.add_argument("--words", nargs="*", default=[], help="more words that must not appear")
    a = ap.parse_args(argv)
    try:
        cfg = rbconfig.load()
    except rbconfig.ConfigError as e:
        print("export: %s" % e, file=sys.stderr)
        return 2
    return export(os.path.abspath(a.dest), a.words, cfg)


if __name__ == "__main__":
    sys.exit(main())
