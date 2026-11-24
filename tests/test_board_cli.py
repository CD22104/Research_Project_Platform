"""Facts pack and the two Claude Code hooks (boardtool.py brief / check). Run: python3 -m unittest -v"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
import boardtool as T  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures", "projects")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "projects")
        shutil.copytree(FIXTURES, self.root, symlinks=True)
        self.proj = os.path.join(self.root, "demo")
        self.cfg = os.path.join(self.tmp, "servers.json")
        with open(self.cfg, "w") as f:
            json.dump({"projects_root": self.root, "state_dir": os.path.join(self.tmp, "state"), "servers": []}, f)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def hook(self, cmd, data):
        t0 = time.time()
        p = subprocess.run([sys.executable, os.path.join(REPO, "boardtool.py"), cmd, "--hook"], input=json.dumps(data),
                           capture_output=True, text=True, env=dict(os.environ, GPUBOARD_CONFIG=self.cfg), timeout=20)
        return p.returncode, p.stdout, p.stderr, time.time() - t0


class Brief(Base):
    """Agreed case 5."""

    def section(self, text, title):
        part = text.split("## " + title, 1)[1]
        return part.split("\n## ", 1)[0]

    def test_contents(self):
        text = T.brief(self.proj)
        self.assertIn("【事实包】演示项目（demo）", text)
        adopted = self.section(text, "已采纳的决定")
        self.assertIn("[warmup-first] 先定预热，再试 loss：先把预热并入主线", adopted)
        self.assertNotIn("old-plan", adopted)                        # replaced
        self.assertNotIn("scale-later", adopted)                     # still a candidate
        self.assertIn("候选决定，等用户在控制台采纳：", self.section(text, "等用户的"))
        tasks = self.section(text, "进行中的任务")
        self.assertIn("[f1-metric] 进行中 · 执行：加 F1 指标并重算旧结果。下一步：重算 r002、r003。", tasks)
        self.assertIn("[report-r1] 待开始", tasks)
        self.assertIn("r002 @xtrah200", self.section(text, "在跑的运行"))
        self.assertIn("项目 CLAUDE.md", self.section(text, "项目规则"))
        self.assertIn("预热有用，并入主线。", self.section(text, "最近的结论"))

    def test_console_adoption_counts_at_once_and_replies_are_counted(self):
        with open(os.path.join(self.proj, "board", "answers.jsonl"), "a") as f:
            f.write(json.dumps({"id": "d:scale-later", "answer": "采纳", "text": "", "at": "2026-11-24T00:00:00Z"}) + "\n")
            f.write(json.dumps({"id": "t3", "answer": "现在补", "text": "", "at": "2026-11-24T00:00:00Z"}) + "\n")
        text = T.brief(self.proj)
        self.assertIn("[scale-later]", self.section(text, "已采纳的决定"))
        self.assertIn("未处理的回复 2 条（d:scale-later、t3）", text)

    def test_rules_section_and_size_cap(self):
        with open(os.path.join(self.proj, "CLAUDE.md"), "w") as f:
            f.write("# demo\n\n## 长期约束\n- 测试集不参与选模型。\n\n## 其他\nx\n")
        self.assertIn("- 测试集不参与选模型。", self.section(T.brief(self.proj), "项目规则"))
        for n in (700, 1200):
            self.assertLessEqual(len(T.brief(self.proj, max_chars=n)), n)


class Hooks(Base):
    """Agreed case 7."""

    def test_session_start(self):
        code, out, err, dt = self.hook("brief", {"cwd": self.tmp, "source": "startup"})
        self.assertEqual((code, out, err), (0, "", ""))                # not in a project: nothing
        self.assertLess(dt, 2)
        code, out, err, dt = self.hook("brief", {"cwd": os.path.join(self.proj, "board", "tasks"), "source": "compact"})
        self.assertEqual(code, 0)
        self.assertIn("【事实包】演示项目", out)
        code, out, _, _ = self.hook("brief", {"cwd": os.path.join(FIXTURES, "demo")})   # outside projects_root
        self.assertEqual((code, out), (0, ""))
        p = subprocess.run([sys.executable, os.path.join(REPO, "boardtool.py"), "brief", "--hook"], input="not json",
                           capture_output=True, text=True, env=dict(os.environ, GPUBOARD_CONFIG=self.cfg))
        self.assertEqual(p.returncode, 0)                                # bad input never fails the session

    def test_post_edit_check(self):
        good = os.path.join(self.proj, "board", "attempts", "2026-11-10-lr-sweep.md")
        bad = os.path.join(self.proj, "board", "attempts", "2026-11-15-focal.md")
        self.assertEqual(self.hook("check", {"tool_name": "Edit", "tool_input": {"file_path": good}})[:3], (0, "", ""))
        code, out, err, dt = self.hook("check", {"tool_name": "Edit", "tool_input": {"file_path": bad}})
        self.assertEqual(code, 2)
        self.assertIn("记录检查：board/attempts/2026-11-15-focal.md 有", err)
        self.assertIn("- 没有「一页纸」一节", err)
        self.assertNotIn("lr-sweep", err)                                # only the edited file
        self.assertLess(dt, 2)
        self.assertEqual(self.hook("check", {"tool_input": {"file_path": os.path.join(self.proj, "src.py")}})[:3], (0, "", ""))
        outside = os.path.join(FIXTURES, "demo", "board", "attempts", "2026-11-15-focal.md")
        self.assertEqual(self.hook("check", {"tool_input": {"file_path": outside}})[:3], (0, "", ""))
        self.assertEqual(self.hook("check", {})[:3], (0, "", ""))

    def test_cli_check(self):
        probs = T.check(self.proj)
        self.assertTrue(any(c["where"] == "tasks/broken-task.md" for c in probs))
        only = T.check(self.proj, os.path.join(self.proj, "board", "tasks", "broken-task.md"))
        self.assertEqual({c["where"] for c in only}, {"tasks/broken-task.md"})


if __name__ == "__main__":
    unittest.main()
