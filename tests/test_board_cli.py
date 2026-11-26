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
SCRIPTS = os.path.join(REPO, "skill", "research-board", "scripts")
sys.path.insert(0, SCRIPTS)
import boardtool as T  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures", "projects")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "projects")
        shutil.copytree(FIXTURES, self.root, symlinks=True)
        self.proj = os.path.join(self.root, "demo")
        self.cfg = os.path.join(self.tmp, "config.json")
        with open(self.cfg, "w") as f:
            json.dump({"projects_root": self.root, "state_dir": os.path.join(self.tmp, "state"), "servers": []}, f)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def hook(self, cmd, data):
        t0 = time.time()
        p = subprocess.run([sys.executable, os.path.join(SCRIPTS, "boardtool.py"), cmd, "--hook"], input=json.dumps(data),
                           capture_output=True, text=True, env=dict(os.environ, RESEARCH_BOARD_CONFIG=self.cfg), timeout=20)
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
        self.assertIn("r002 @gpu-h200", self.section(text, "在跑的运行"))
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

    def test_papers(self):
        text = T.brief(self.proj)
        sec = self.section(text, "论文")
        self.assertIn("共 6 篇，用户读过 1 篇，必读未读 2 篇", sec)
        self.assertIn("- 必读未读：[goyal-large-batch] Accurate, Large Minibatch SGD: Training ImageNet in 1 Hour\n", sec)
        self.assertIn("- 必读未读：[radam] On the Variance of the Adaptive Learning Rate and Beyond（未核实）", sec)   # no `checked`
        self.assertIn("- 用户读过：[focal-loss] Focal Loss for Dense Object Detection", sec)
        for absent in ("old-survey", "sgdr", "mixup"):                  # dropped; or neither 必读 nor read
            self.assertNotIn(absent, sec)
        self.assertIn("board/papers.jsonl", self.section(text, "提醒"))   # sessions are told to keep the list
        self.assertIn("board/examples/data/<数据集 id>.jsonl", self.section(text, "提醒"))   # and to add dataset samples
        self.assertIn("kind: 简报", self.section(text, "提醒"))                                # and to write briefs
        self.assertIn("title", self.section(text, "提醒"))
        self.assertIn("method", self.section(text, "提醒"))
        short = T.brief(self.proj, max_chars=len(text) - 40)           # the papers are the first list to be shortened
        self.assertNotIn("用户读过：", self.section(short, "论文"))
        self.assertIn("预热有用，并入主线。", self.section(short, "最近的结论"))
        os.remove(os.path.join(self.proj, "board", "papers.jsonl"))
        self.assertEqual(self.section(T.brief(self.proj), "论文").split("\n", 1)[1].strip(), "（还没有）")


class Forwarders(Base):
    """While crontab, ~/bin/board and the a100 hooks still call the repository root, those files forward to the skill."""

    def test_root_board_and_boardtool_forward(self):
        for cmd in (["/bin/sh", os.path.join(REPO, "board")], [sys.executable, os.path.join(REPO, "boardtool.py")]):
            with self.subTest(cmd=cmd[-1]):
                p = subprocess.run(cmd + ["brief", self.proj], capture_output=True, text=True, timeout=30,
                                   env=dict(os.environ, RESEARCH_BOARD_CONFIG=self.cfg))
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertIn("【事实包】演示项目（demo）", p.stdout)


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
        p = subprocess.run([sys.executable, os.path.join(SCRIPTS, "boardtool.py"), "brief", "--hook"], input="not json",
                           capture_output=True, text=True, env=dict(os.environ, RESEARCH_BOARD_CONFIG=self.cfg))
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

    def test_post_edit_check_of_papers(self):
        papers = os.path.join(self.proj, "board", "papers.jsonl")
        self.assertEqual(self.hook("check", {"tool_name": "Edit", "tool_input": {"file_path": papers}})[:3], (0, "", ""))
        with open(papers, "a", encoding="utf-8") as f:   # a paper's problems are filed as "papers.jsonl · <id>"
            f.write(json.dumps({"id": "nowhy", "title": "T", "url": "https://x.org/1"}) + "\nnot json\n")
        code, _, err, _ = self.hook("check", {"tool_name": "Write", "tool_input": {"file_path": papers}})
        self.assertEqual(code, 2)
        self.assertIn("记录检查：board/papers.jsonl 有 2 个问题", err)
        self.assertIn("- nowhy：缺 why：一句话写为什么读、和本项目什么关系", err)
        self.assertIn("- 1 行无法解析或缺少 id", err)


if __name__ == "__main__":
    unittest.main()
