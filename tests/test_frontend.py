"""Frontend tests in headless Chromium (skipped if Chromium is missing).

- common.js unit checks: tests/frontend_unit.html, results read back from the DOM
- end to end: server.py on a throwaway 127.0.0.1 port serving the fixtures,
  pages rendered with --dump-dom after their fetches complete
"""
import html
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, "fixtures", "projects")
CHROME = shutil.which("chromium-browser") or shutil.which("chromium") or shutil.which("google-chrome")


def dump(url, budget_ms=8000):
    """Rendered DOM of url, plus console lines that look like JS errors."""
    p = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--enable-logging=stderr",
                        "--v=0", "--virtual-time-budget=%d" % budget_ms, "--dump-dom", url],
                       capture_output=True, text=True, timeout=120)
    errors = [l for l in p.stderr.splitlines() if "CONSOLE" in l and re.search(r"Uncaught|Error", l)]
    return p.stdout, errors


@unittest.skipUnless(CHROME, "Chromium not installed")
class CommonJs(unittest.TestCase):
    def test_unit_page(self):
        dom, errors = dump("file://" + os.path.join(HERE, "frontend_unit.html"), 3000)
        self.assertEqual(errors, [])
        m = re.search(r'<pre id="result">(.*?)</pre>', dom, re.S)
        results = json.loads(html.unescape(m.group(1)))
        self.assertGreaterEqual(len(results), 30)
        failed = [r for r in results if not r["ok"]]
        self.assertEqual(failed, [], json.dumps(failed, ensure_ascii=False, indent=1))


@unittest.skipUnless(CHROME, "Chromium not installed")
class Pages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        root = os.path.join(cls.tmp, "projects")
        shutil.copytree(FIXTURES, root, symlinks=True)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            cls.port = s.getsockname()[1]
        cfg = os.path.join(cls.tmp, "servers.json")
        with open(cfg, "w") as f:  # no "socket": plain 127.0.0.1 port, fixtures only
            json.dump({"port": cls.port, "servers": [], "projects_root": root, "projects_interval_seconds": 3600}, f)
        cls.base = "http://127.0.0.1:%d/" % cls.port
        cls.log = open(os.path.join(cls.tmp, "server.log"), "w")
        cls.proc = subprocess.Popen([sys.executable, os.path.join(REPO, "server.py")],
                                    env=dict(os.environ, GPUBOARD_CONFIG=cfg), stdout=cls.log, stderr=cls.log)
        for _ in range(100):
            try:
                with urllib.request.urlopen(cls.base + "api/projects", timeout=2) as r:
                    if json.load(r)["scan"]["finished"]:
                        break
            except OSError:
                pass
            time.sleep(0.1)
        cls.demo, cls.demo_errors = dump(cls.base + "project.html?p=demo")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(5)
        cls.log.close()
        shutil.rmtree(cls.tmp)

    def test_no_js_errors(self):
        self.assertEqual(self.demo_errors, [])

    def test_summary(self):
        d = self.demo
        self.assertIn('<h1 id="title">演示项目</h1>', d)
        self.assertIn("学习率预热能否稳定小模型训练？", d)
        self.assertIn("预热 500 步后验证集准确率稳定提升约 2 个点。", d)
        self.assertIn("当前版本 <b>v2</b>", d)
        self.assertIn("无法确认是否在跑", d)          # r002 liveness: xtrah200 not polled in tests
        self.assertIn("等你处理", d)
        for kind in ["方案：", "版本：", "支线：", "尝试：", "运行：", "样例："]:
            self.assertIn(kind, d)

    def test_example(self):
        self.assertIn("一个例子", self.demo)
        self.assertIn("模型输出 <strong>正面</strong>", self.demo)
        self.assertIn("probe.jsonl 前 2 行", self.demo)

    def test_path(self):
        d = self.demo
        for s in ["走过的路", "加入学习率预热", "学习率调度", "数据增强 C# 实验", "损失函数",
                  "没有归属支线的尝试", "lr-sweep", "aug-mixup", "focal"]:
            self.assertIn(s, d)
        self.assertIn('<span class="todo">待你确认结论</span>', d)
        self.assertIn('<span class="faint">未写预期</span>', d)
        self.assertNotIn("<details class=\"att\" id=\"att-attempts/2026-11-10-lr-sweep.md\" open", d)  # collapsed

    def test_evidence(self):
        d = self.demo
        self.assertIn("4 次运行", d)
        self.assertRegex(d, r'data-group="测试集"[^>]*>测试集<')
        self.assertIn(">其他指标<", d)
        self.assertRegex(d, r'<th class="n">acc</th><th class="n">f1</th>')  # group prefix stripped
        self.assertIn(">0.81<", d)

    def test_errors_listed(self):
        self.assertIn("读取有问题的文件", self.demo)
        self.assertIn("runs/r003/run.json", self.demo)

    def test_untrusted_text_stays_text(self):
        d = self.demo
        self.assertEqual(len(re.findall(r"<script\b", d)), 2)      # common.js + the page's own
        self.assertNotIn('href="javascript:', d.lower())
        self.assertIn("&lt;script&gt;alert(\"xss in walkthrough\")&lt;/script&gt;", d)
        self.assertIn("&lt;script&gt;alert(\"xss in plan\")&lt;/script&gt;", d)    # plan.md, rendered as text
        self.assertIn('<a href="https://example.org/paper" target="_blank" rel="noopener noreferrer">论文</a>', d)
        self.assertIn("危险链接", d)                                             # javascript: link kept as text only
        self.assertIn("lr = base_lr * min(1, step / warmup)  # &lt;b&gt;not bold&lt;/b&gt;", d)

    def test_plan_collapsed(self):
        self.assertRegex(self.demo, r'<details class="sub" id="plan"><summary>整体方案（plan\.md，')

    def test_index_projects_section(self):
        d, errors = dump(self.base)
        self.assertEqual(errors, [])
        self.assertIn("项目 <span class=\"meta\">3 个", d)
        self.assertIn("演示项目", d)
        self.assertIn("运行中 r002 · xtrah200 卡 2,3", d)
        self.assertIn("1 个记录文件读取有问题", d)
        self.assertIn('href="project.html?p=demo"', d)

    def test_missing_project(self):
        d, errors = dump(self.base + "project.html?p=nope")
        self.assertEqual(errors, [])
        self.assertIn("没有这个项目", d)


if __name__ == "__main__":
    unittest.main()
