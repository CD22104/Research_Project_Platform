"""Sharing: the scan for personal information, the repository being clean of it, and the export.
Run: python3 -m unittest -v"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "scripts"))
sys.path.insert(0, os.path.join(REPO, "skill", "research-board", "scripts"))
import export as X  # noqa: E402
import rbconfig  # noqa: E402


def real_config():
    try:
        return rbconfig.load()
    except rbconfig.ConfigError:
        return None


# parts that describe one person's setup and are replaced or left out on export
PRIVATE = {os.path.join("platform", "design.js"), os.path.join("platform", "docs", "guide.md")}


class Export(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_scan(self):
        os.makedirs(os.path.join(self.tmp, "a", "b"))
        with open(os.path.join(self.tmp, "a", "b", "x.md"), "w", encoding="utf-8") as f:
            f.write("ok\nmy-lab-host is here\n")
        with open(os.path.join(self.tmp, "a", "skip.md"), "w", encoding="utf-8") as f:
            f.write("my-lab-host\n")
        self.assertEqual(X.scan(self.tmp, ["my-lab-host"], skip={os.path.join("a", "skip.md")}),
                         [(os.path.join("a", "b", "x.md"), 2, "my-lab-host")])

    def test_private_words(self):
        root = os.path.join(self.tmp, "projects")
        os.makedirs(os.path.join(root, "secret-project"))
        os.makedirs(os.path.join(root, "_shared"))
        open(os.path.join(root, "README.md"), "w").close()
        cfg = {"servers": [{"name": "labx-a100", "host": "labx-a100.lab.example"}], "projects_root": root, "console": {"name": "某人的控制台"}}
        words = X.private_words(cfg)
        for w in ("labx-a100", "labx-a100.lab.example", root, "某人的控制台", "secret-project", os.path.expanduser("~")):
            self.assertIn(w, words)
        for w in ("_shared", "README.md"):                                    # only project folders
            self.assertNotIn(w, words)

    def test_repository_has_no_personal_information(self):
        cfg = real_config()
        if cfg is None:
            self.skipTest("no configuration on this machine")
        words = X.private_words(cfg)
        found = []
        for part in ("skill", "platform", "tests", "scripts"):
            found += [(os.path.join(part, f), n, w) for f, n, w in X.scan(os.path.join(REPO, part), words)
                      if os.path.join(part, f) not in PRIVATE]
        for f in ("README.md", "config.example.json"):
            found += [x for x in X.scan(REPO, words) if x[0] == f]
        self.assertEqual(found, [])

    def test_export(self):
        cfg = real_config()
        dest = os.path.join(self.tmp, "share")
        self.assertEqual(X.export(dest, cfg=cfg), 0)
        for gone in ("CLAUDE.md", "NOTES.md", "server.py", "boardtool.py", "board"):
            self.assertFalse(os.path.exists(os.path.join(dest, gone)), gone)
        for rel, text in X.REPLACE.items():
            with open(os.path.join(dest, rel), encoding="utf-8") as f:
                self.assertEqual(f.read(), text)
        self.assertTrue(os.path.isfile(os.path.join(dest, "skill", "research-board", "SKILL.md")))
        self.assertFalse(os.path.exists(os.path.join(dest, "servers.json")))
        log = subprocess.run(["git", "-C", dest, "log", "--format=%an %s"], capture_output=True, text=True).stdout.splitlines()
        self.assertEqual(log, ["research-board research-board: the console and the skill"])   # one commit, no personal author
        self.assertEqual(X.scan(dest, X.private_words(cfg)), [])

    def test_export_stops_on_personal_information(self):
        dest = os.path.join(self.tmp, "share")
        self.assertEqual(X.export(dest, extra=["research-board"], cfg=real_config()), 1)   # a word that is everywhere
        self.assertFalse(os.path.exists(dest))


if __name__ == "__main__":
    unittest.main()
