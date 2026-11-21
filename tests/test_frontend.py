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
    # keep the tags but drop script source, so assertions only see rendered content
    dom = re.sub(r"(<script\b[^>]*>).*?(</script>)", r"\1\2", p.stdout, flags=re.S)
    return dom, errors


def start_server(repo, tmp):
    """server.py from `repo` on a free 127.0.0.1 port, serving a copy of the fixtures; waits for the first scan."""
    root = os.path.join(tmp, "projects")
    shutil.copytree(FIXTURES, root, symlinks=True)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    cfg = os.path.join(tmp, "servers.json")
    with open(cfg, "w") as f:  # no "socket": plain 127.0.0.1 port, fixtures only
        json.dump({"port": port, "servers": [], "projects_root": root, "projects_interval_seconds": 3600}, f)
    base = "http://127.0.0.1:%d/" % port
    log = open(os.path.join(tmp, "server.log"), "w")
    proc = subprocess.Popen([sys.executable, os.path.join(repo, "server.py")],
                            env=dict(os.environ, GPUBOARD_CONFIG=cfg), stdout=log, stderr=log)
    for _ in range(100):
        try:
            with urllib.request.urlopen(base + "api/projects", timeout=2) as r:
                if json.load(r)["scan"]["finished"]:
                    break
        except OSError:
            pass
        time.sleep(0.1)
    return proc, base, log


def stop_server(proc, log):
    proc.terminate()
    proc.wait(5)
    log.close()


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
        cls.proc, cls.base, cls.log = start_server(REPO, cls.tmp)
        cls.demo, cls.demo_errors = dump(cls.base + "project.html?p=demo")

    @classmethod
    def tearDownClass(cls):
        stop_server(cls.proc, cls.log)
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
        self.assertNotIn("FULL-TEXT-MARKER", self.demo)          # folded at "--- / # 完整原文"
        self.assertIn("<button data-example=\"\">展开完整原文</button>", self.demo)

    def test_path(self):
        d = self.demo
        for s in ["走过的路", "加入学习率预热", "学习率调度", "数据增强 C# 实验", "损失函数",
                  "没有归属支线的尝试", "lr-sweep", "aug-mixup", "focal"]:
            self.assertIn(s, d)
        self.assertIn('<span class="todo">待你确认结论</span>', d)
        self.assertIn('<span class="faint">未写预期</span>', d)
        self.assertNotIn("<details class=\"att\" id=\"att-attempts/2026-11-10-lr-sweep.md\" open", d)  # collapsed
        self.assertNotIn("没有挂到版本上的支线", d)                 # t-old is placed under v1 via merged_tracks
        self.assertRegex(d, r'(?s)<b>v1</b>.*旧基线整理.*<span class="merge">并入此版本</span>.*<b>v2</b>')
        self.assertRegex(d, r'(?s)学习率调度</b>.*<span class="merge">→ 并入 v2</span>')
        self.assertIn('<div class="gist"><span class="small">假设</span> 旧脚本的基线结果可以直接沿用。</div>', d)
        self.assertNotIn("<details class=\"tbody\" id=\"trk-tracks/t-old.md\" open", d)   # track note folded
        self.assertIn('<span class="pill t-part">部分成功</span>', d)
        self.assertIn('<span class="pill t-other">搁置中</span>', d)                       # unknown status, neutral
        self.assertIn('<span class="todo">待你填写结论和原因</span>', d)

    def test_evidence(self):
        d = self.demo
        self.assertIn("5 次运行", d)
        self.assertRegex(d, r'data-view="main" data-group="测试集">测试集 <span class="small">2</span>')
        self.assertIn(">其他指标 <span", d)
        self.assertIn("本组 2 次运行", d)
        self.assertRegex(d, r'<th class="n">acc</th><th class="n">f1</th></tr>')  # primary_metrics only
        self.assertIn('0.8100 <span class="small">(488/602)</span>', d)            # ratio acc = correct / n
        self.assertIn('显示全部列（4）', d)
        self.assertIn("还没有指标的运行：", d)
        self.assertIn("display 里有用不上的设置：不认识的字段 colour", d)

    def test_data_runs_have_their_own_table(self):
        d = self.demo
        main, _, data = d.partition('<h3 class="tt">数据')
        self.assertTrue(data, "no data table")
        self.assertNotIn("<b>r005</b>", main)
        self.assertIn("<b>r005</b>", data)
        self.assertRegex(data, r'<th class="n">rows</th><th class="n">allow</th><th class="n">ask</th><th class="n">deny</th>')

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
        d, errors = dump(self.base + "#projects")
        self.assertEqual(errors, [])
        self.assertIn("项目 <span class=\"meta\">3 个", d)
        self.assertIn("演示项目", d)
        self.assertIn("运行中 r002 · xtrah200 卡 2,3", d)
        self.assertIn("1 个记录文件读取有问题", d)
        self.assertIn('href="project.html?p=demo"', d)

    def test_tabs(self):
        gpu, errors = dump(self.base)
        self.assertEqual(errors, [])
        self.assertIn('<div id="tab-projects" hidden="">', gpu)       # GPU tab is the default
        self.assertNotIn('<div id="tab-gpu" hidden', gpu)
        self.assertIn('<a class="brand" href="./#gpu">GPU 看板</a>', gpu)
        self.assertIn('<a class="navtab on" href="./#gpu">GPU <span class="cnt">0 空闲</span></a>', gpu)
        self.assertNotIn("演示项目", gpu.split('<div id="tab-projects"')[0])  # no project list in the GPU tab
        proj, errors = dump(self.base + "#projects")
        self.assertEqual(errors, [])
        self.assertIn('<div id="tab-gpu" hidden="">', proj)
        self.assertIn('<a class="navtab on" href="./#projects">项目 <span class="cnt">3</span></a>', proj)
        self.assertIn("<title>项目 · GPU 看板</title>", proj)

    def test_project_page_top_bar(self):
        d = self.demo
        self.assertIn('<a class="navtab on" href="./#projects">项目 <span class="cnt">3</span></a>', d)
        self.assertIn('<span class="crumb"><a href="./#projects">项目</a> /</span>', d)
        self.assertIn("<title>演示项目 · GPU 看板</title>", d)
        self.assertNotIn("← GPU 看板", d)

    def test_missing_project(self):
        d, errors = dump(self.base + "project.html?p=nope")
        self.assertEqual(errors, [])
        self.assertIn("没有这个项目", d)


CLICKS = """<script>
setTimeout(() => {
  const out = {}, ev = () => document.getElementById('evidence');
  document.querySelector('[data-example]').click();
  out.example_open = document.getElementById('example').innerHTML.includes('FULL-TEXT-MARKER');
  document.querySelector('[data-allcols="main"]').click();
  out.all_cols = [...ev().querySelector('table.runs').querySelectorAll('th')].map(t => t.textContent).slice(5);
  document.querySelector('button[data-view="main"][data-group=""]').click();
  out.other_group_cols = [...ev().querySelector('table.runs').querySelectorAll('th')].map(t => t.textContent).slice(5);
  document.querySelector('a[data-run="r002"]').click();
  out.r002_open = !!ev().querySelector('tr.run.open[data-dir="runs/r002"]');
  out.group_after_jump = ev().querySelector('button.tab.on').dataset.group;
  document.querySelector('a[data-bare="runs/r004"]').click();
  out.bare_detail = ev().innerHTML.includes('samples.jsonl 前 2 行');
  const pre = document.createElement('pre'); pre.id = 'clicks'; pre.textContent = JSON.stringify(out); document.body.appendChild(pre);
}, 1000);
<\/script>
</body>"""


@unittest.skipUnless(CHROME, "Chromium not installed")
class Interactions(unittest.TestCase):
    """Clicks on a copy of the repo whose project.html has a test script appended."""

    def test_clicks(self):
        tmp = tempfile.mkdtemp()
        try:
            repo = os.path.join(tmp, "repo")
            os.mkdir(repo)
            for f in ["server.py", "projects.py", "index.html", "project.html", "common.js"]:
                shutil.copy(os.path.join(REPO, f), repo)
            page = os.path.join(repo, "project.html")
            with open(page, encoding="utf-8") as f:
                text = f.read()
            with open(page, "w", encoding="utf-8") as f:
                f.write(text.replace("</body>", CLICKS.replace("<\\/script>", "</" + "script>"), 1))
            proc, base, log = start_server(repo, tmp)
            try:
                dom, errors = dump(base + "project.html?p=demo")
            finally:
                stop_server(proc, log)
        finally:
            shutil.rmtree(tmp)
        self.assertEqual(errors, [])
        out = json.loads(html.unescape(re.search(r'<pre id="clicks">(.*?)</pre>', dom, re.S).group(1)))
        self.assertTrue(out["example_open"])
        self.assertEqual(out["all_cols"], ["acc", "f1", "correct", "n"])
        self.assertEqual(out["other_group_cols"], ["少数类召回", "train_loss"])  # columns in order of first appearance, newest run first
        self.assertTrue(out["r002_open"])
        self.assertEqual(out["group_after_jump"], "")   # r002 has numbers in the current group, so no switch
        self.assertTrue(out["bare_detail"])


if __name__ == "__main__":
    unittest.main()
