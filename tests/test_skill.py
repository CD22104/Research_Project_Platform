"""The skill as it is installed on each machine: its file list, SKILL.md, and that a copy runs on its own.
Run: python3 -m unittest -v"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SKILL = os.path.join(REPO, "skill", "research-board")
sys.path.insert(0, os.path.join(SKILL, "scripts"))
import projects as P  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures", "projects")
# the sentence the global rules use to say when to load the skill; SKILL.md must trigger on the same words
TRIGGER = "在有 board/ 的项目里工作、新建/结束/暂停项目、启动长任务时，先加载 skill research-board"


def make_manifest(*args):
    return subprocess.run([sys.executable, os.path.join(SKILL, "scripts", "make-manifest"), *args], capture_output=True, text=True, timeout=30)


class Skill(unittest.TestCase):
    def test_file_list_is_up_to_date(self):
        p = make_manifest(SKILL, "--check")
        self.assertEqual(p.returncode, 0, p.stderr + "\n(run skill/research-board/scripts/make-manifest and commit)")

    def test_check_notices_a_changed_file(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        copy = os.path.join(tmp, "research-board")
        shutil.copytree(SKILL, copy, ignore=shutil.ignore_patterns("__pycache__"))
        with open(os.path.join(copy, "references", "long-runs.md"), "a", encoding="utf-8") as f:
            f.write("\n改了一行\n")
        p = make_manifest(copy, "--check")
        self.assertEqual(p.returncode, 1)
        self.assertIn("out of date: references/long-runs.md", p.stderr)

    def test_skill_md(self):
        with open(os.path.join(SKILL, "SKILL.md"), encoding="utf-8") as f:
            text = f.read()
        meta, body = P.parse_front_matter(text)
        self.assertEqual(meta.get("name"), "research-board")
        self.assertIn(TRIGGER, meta.get("description") or "")
        refs = sorted(f for f in os.listdir(os.path.join(SKILL, "references")) if f.endswith(".md"))
        self.assertEqual(len(refs), 7)
        for f in refs:                                              # every reference is reachable from SKILL.md, and only those
            self.assertIn("`references/%s`" % f, body)
        for name in set(__import__("re").findall(r"`references/([^`]+)`", body)):
            self.assertIn(name, refs)

    def test_an_installed_copy_runs_on_its_own(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        installed = os.path.join(tmp, "home", ".claude", "skills", "research-board")
        shutil.copytree(SKILL, installed, ignore=shutil.ignore_patterns("__pycache__"))
        root = os.path.join(tmp, "projects")
        shutil.copytree(FIXTURES, root, symlinks=True)
        cfg = os.path.join(tmp, "config.json")
        with open(cfg, "w") as f:
            json.dump({"projects_root": root, "servers": [], "state_dir": os.path.join(tmp, "state")}, f)
        env = dict(os.environ, RESEARCH_BOARD_CONFIG=cfg)
        board = os.path.join(installed, "scripts", "board")
        p = subprocess.run(["/bin/sh", board, "brief", os.path.join(root, "demo")], capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("【事实包】演示项目（demo）", p.stdout)
        self.assertIn(os.path.join(installed, "references", "board-format.md"), p.stdout)   # points into the installed copy
        self.assertIn(os.path.join(installed, "scripts", "board") + " task-begin", p.stdout)
        p = subprocess.run(["/bin/sh", board, "check", os.path.join(root, "minimal")], capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        p = subprocess.run(["/bin/sh", board, "reconcile", "--dry-run"], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertFalse([x for x in os.listdir(installed) if x not in ("SKILL.md", "VERSION", "MANIFEST.sha256", "references", "scripts")])


if __name__ == "__main__":
    unittest.main()
