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


ROUTES = {
    "overview": "#/overview", "gpu": "#/gpu", "todo": "#/todo", "new": "#/new",
    "p_overview": "#/p/demo/overview", "p_exp": "#/p/demo/experiments", "p_exp_sel": "#/p/demo/experiments/2026-11-10-lr-sweep",
    "p_exp_track": "#/p/demo/experiments?track=t-loss", "p_exp_todo": "#/p/demo/experiments?todo=1",
    "p_roadmap": "#/p/demo/roadmap", "p_runs": "#/p/demo/runs", "p_run": "#/p/demo/runs/r001",
    "p_examples": "#/p/demo/examples", "p_docs": "#/p/demo/docs", "missing": "#/p/nope/overview",
    "legacy_project": "project.html?p=demo", "legacy_name": "project.html?name=demo", "legacy_projects": "#projects", "root": "",
}


def main_html(dom):
    m = re.search(r'<div class="page" id="main">(.*)</div></main>', dom, re.S)
    return m.group(1) if m else ""


def title(dom):
    return re.search(r"<title>(.*?)</title>", dom).group(1)


@unittest.skipUnless(CHROME, "Chromium not installed")
class Pages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.proc, cls.base, cls.log = start_server(REPO, cls.tmp)
        cls.pages = {k: dump(cls.base + v, 4000) for k, v in ROUTES.items()}

    @classmethod
    def tearDownClass(cls):
        stop_server(cls.proc, cls.log)
        shutil.rmtree(cls.tmp)

    def page(self, key):
        return self.pages[key][0]

    def test_no_js_errors_anywhere(self):
        for k, (dom, errors) in self.pages.items():
            with self.subTest(route=k):
                self.assertEqual(errors, [])
                self.assertEqual(len(re.findall(r"<script\b", dom)), 2)  # common.js + app.js, nothing injected from records

    def test_sidebar(self):
        d = self.page("p_overview")
        self.assertIn('<a class="brand" href="#/overview">CD的研究控制台</a>', d)
        self.assertIn('<span class="lbl">GPU</span><span class="cnt">0 空闲</span>', d)
        self.assertIn('<span class="lbl">待办</span><span class="cnt">2</span>', d)
        self.assertRegex(d, r'<a class="sitem on" href="#/p/demo/overview"[^>]*><span class="dot d-run"></span><span class="lbl">演示项目</span>')
        for t in ["只有标题的项目", "broken", "＋ 新建项目"]:
            self.assertIn(t, d)
        self.assertIn('<a class="sitem on" href="#/overview"><span class="lbl">总览</span>', self.page("overview"))

    def test_titles_and_legacy_addresses(self):
        self.assertEqual(title(self.page("overview")), "总览 · CD的研究控制台")
        self.assertEqual(title(self.page("gpu")), "(0 空) CD的研究控制台")
        self.assertEqual(title(self.page("todo")), "待办 · CD的研究控制台")
        for k in ["p_overview", "legacy_project", "legacy_name"]:
            self.assertEqual(title(self.page(k)), "演示项目 · CD的研究控制台", k)
        for k in ["legacy_projects", "root"]:
            self.assertIn('<h1 class="pt">总览</h1>', self.page(k), k)

    def test_project_header_and_tabs(self):
        d = main_html(self.page("p_overview"))
        self.assertRegex(d, r'<div class="phead"><h1>演示项目</h1><span class="pill t-run">进行中</span>')
        self.assertIn('<a class="ptab on" href="#/p/demo/overview">概览</a>', d)
        self.assertIn('<a class="ptab" href="#/p/demo/experiments">实验<span class="cnt">7</span></a>', d)
        for t in ["路线", "运行", "样例", "文档"]:
            self.assertIn(">" + t, d)

    def test_project_overview(self):
        d = main_html(self.page("p_overview"))
        self.assertIn('<p class="concl">预热 500 步后验证集准确率稳定提升约 2 个点。</p>', d)
        self.assertRegex(d, r'<div class="k">支线</div><div class="v">4</div>\s*<div class="s"><b>1</b> 条进行中')
        self.assertRegex(d, r'<div class="k">尝试</div><div class="v">7</div>')
        self.assertRegex(d, r'最好的 acc · 测试集</div>\s*<div class="v">0.8100</div><div class="s">来自运行 <b>r001</b>（488/602）')
        self.assertIn('href="#/p/demo/runs/r001"', d)
        self.assertRegex(d, r'<div class="k">正在跑</div><div class="v">1</div>')
        self.assertIn("等你处理 <span class=\"meta\">2 条</span>", d)
        self.assertIn('<span class="todo">待你确认结论</span>', d)
        self.assertIn("最近动态", d)
        self.assertIn("runs/r003/run.json", d)                       # unreadable files listed on the overview
        self.assertNotIn("FULL-TEXT-MARKER", d)

    def test_experiments_master_detail(self):
        d = main_html(self.page("p_exp"))
        self.assertEqual(len(re.findall(r'<a class="li', d)), 7)
        self.assertEqual(len(re.findall(r'<a class="li on"', d)), 1)
        self.assertIn('<option value="t-loss">损失函数（3）</option>', d)
        self.assertIn(">等你处理 2</a>", d)
        sel = main_html(self.page("p_exp_sel"))
        self.assertRegex(sel, r'<a class="li on" href="#/p/demo/experiments/2026-11-10-lr-sweep">')
        heads = re.findall(r'<div class="sec[^"]*"><h3>(.*?)</h3>', sel)
        self.assertEqual(heads[:5], ["假设", "预期", "改了什么", "结果", "结论和原因"])
        self.assertIn("关联的运行（1）", sel)
        self.assertRegex(sel, r'<th>分组</th><th class="num">acc</th><th class="num">f1</th>')   # display primary_metrics
        self.assertIn('<td class="num">0.8100 <span class="small">(488/602)</span></td>', sel)  # display ratio
        self.assertEqual(len(re.findall(r'<a class="li', main_html(self.page("p_exp_track")))), 3)
        self.assertEqual(len(re.findall(r'<a class="li', main_html(self.page("p_exp_todo")))), 2)

    def test_runs_and_data_table(self):
        d = main_html(self.page("p_runs"))
        main, _, data = d.partition('<h3 class="tt">数据')
        self.assertIn("<b>r001</b>", main)
        self.assertNotIn("<b>r005</b>", main)
        self.assertIn("<b>r005</b>", data)
        self.assertIn("display 里有用不上的设置：不认识的字段 colour", d)
        run = main_html(self.page("p_run"))
        self.assertIn('<tr class="run open" data-dir="runs/r001" data-key="r001">', run)
        self.assertRegex(run, r'<table class="kv">.*<th>warmup</th><td class="num">500</td>')   # config as key/value rows
        self.assertNotIn('{"lr"', run)                                                        # never a one-line dict

    def test_examples_and_docs(self):
        ex = main_html(self.page("p_examples"))
        self.assertIn("展开完整原文", ex)
        self.assertNotIn("FULL-TEXT-MARKER", ex)
        self.assertIn('&lt;script&gt;alert("xss in walkthrough")&lt;/script&gt;', ex)
        docs = main_html(self.page("p_docs"))
        for g in ["方案", "版本说明", "支线说明"]:
            self.assertIn(f">{g}</div>", docs)
        self.assertIn('&lt;script&gt;alert("xss in plan")&lt;/script&gt;', docs)
        self.assertIn('<a href="https://example.org/paper" target="_blank" rel="noopener noreferrer">论文</a>', docs)
        self.assertNotIn('href="javascript:', docs.lower())

    def test_roadmap(self):
        d = main_html(self.page("p_roadmap"))
        self.assertRegex(d, r'(?s)<b>v1</b>.*旧基线整理.*<span class="merge">并入此版本</span>.*<b>v2</b>')
        self.assertRegex(d, r'(?s)学习率调度</b>.*<span class="merge">→ 并入 v2</span>')
        self.assertNotIn("没有挂到版本上的支线", d)

    def test_global_pages(self):
        ov = main_html(self.page("overview"))
        self.assertEqual(len(re.findall(r'<a class="pcard"', ov)), 3)
        self.assertLess(ov.index("演示项目"), ov.index("broken"))                    # 进行中 first
        self.assertIn("正在跑 r002 · xtrah200 卡 2,3", ov)
        todo = main_html(self.page("todo"))
        self.assertIn("<b>演示项目</b>：1 条尝试等你确认结论，1 条尝试缺结论和原因", todo)
        self.assertIn('href="#/p/demo/experiments?todo=1"', todo)
        self.assertIn("现在能跑的卡", main_html(self.page("gpu")))
        self.assertIn("新建项目 &lt;名字&gt;", main_html(self.page("new")))
        self.assertIn("没有这个项目", main_html(self.page("missing")))


CLICKS = """<script>
const wait = ms => new Promise(r => setTimeout(r, ms));
const th = () => [...document.querySelector('#main table.runs').querySelectorAll('th')].map(t => t.textContent).slice(5);
(async () => {
  const out = {};
  await wait(800);
  location.hash = '#/p/demo/runs'; await wait(600);
  document.querySelector('[data-allcols="main"]').click(); await wait(50);
  out.all_cols = th();
  document.querySelector('button[data-view="main"][data-group=""]').click(); await wait(50);
  out.other_group_cols = th();
  document.querySelector('tr.run[data-dir="runs/r002"]').click(); await wait(50);
  out.r002_open = !!document.querySelector('tr.run.open[data-dir="runs/r002"]') && !!document.querySelector('tr.rdetail');
  document.querySelector('a[data-bare="runs/r004"]').click(); await wait(50);
  out.bare_detail = document.getElementById('main').innerHTML.includes('samples.jsonl 前 2 行');
  location.hash = '#/p/demo/examples'; await wait(300);
  document.querySelector('[data-example]').click(); await wait(50);
  out.example_open = document.getElementById('main').innerHTML.includes('FULL-TEXT-MARKER');
  location.hash = '#/p/demo/experiments'; await wait(300);
  const sel = document.querySelector('select[data-qtrack]'); sel.value = 't-loss'; sel.dispatchEvent(new Event('change', {bubbles: true})); await wait(300);
  out.track_hash = location.hash;
  out.track_items = document.querySelectorAll('#main a.li').length;
  document.querySelectorAll('#main a.li')[1].click(); await wait(300);
  out.selected_after_click = document.querySelector('#main a.li.on .name').textContent;
  location.hash = '#/gpu'; await wait(300);
  out.sidebar_on = document.querySelector('#side a.sitem.on .lbl').textContent;
  const pre = document.createElement('pre'); pre.id = 'clicks'; pre.textContent = JSON.stringify(out); document.body.appendChild(pre);
})();
<\/script>
</body>"""


@unittest.skipUnless(CHROME, "Chromium not installed")
class Interactions(unittest.TestCase):
    """Clicks on a copy of the repo whose index.html has a test script appended."""

    def test_clicks(self):
        tmp = tempfile.mkdtemp()
        try:
            repo = os.path.join(tmp, "repo")
            os.mkdir(repo)
            for f in ["server.py", "projects.py", "index.html", "project.html", "common.js", "app.js"]:
                shutil.copy(os.path.join(REPO, f), repo)
            page = os.path.join(repo, "index.html")
            with open(page, encoding="utf-8") as f:
                text = f.read()
            with open(page, "w", encoding="utf-8") as f:
                f.write(text.replace("</body>", CLICKS.replace("<\\/script>", "</" + "script>"), 1))
            proc, base, log = start_server(repo, tmp)
            try:
                dom, errors = dump(base + "#/overview", 9000)
            finally:
                stop_server(proc, log)
        finally:
            shutil.rmtree(tmp)
        self.assertEqual(errors, [])
        m = re.search(r'<pre id="clicks">(.*?)</pre>', dom, re.S)
        self.assertTrue(m, "click script did not finish")
        out = json.loads(html.unescape(m.group(1)))
        self.assertEqual(out["all_cols"], ["acc", "f1", "correct", "n"])
        self.assertEqual(out["other_group_cols"], ["少数类召回", "train_loss"])
        self.assertTrue(out["r002_open"])
        self.assertTrue(out["bare_detail"])
        self.assertTrue(out["example_open"])
        self.assertEqual(out["track_hash"], "#/p/demo/experiments?track=t-loss")
        self.assertEqual(out["track_items"], 3)
        self.assertEqual(out["selected_after_click"], "focal-gamma1")
        self.assertEqual(out["sidebar_on"], "GPU")


if __name__ == "__main__":
    unittest.main()
