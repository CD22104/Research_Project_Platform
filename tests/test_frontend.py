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
from urllib.parse import unquote

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


def start_server(repo, tmp, prepare=None):
    """server.py from `repo` on a free 127.0.0.1 port, serving a copy of the fixtures (plus whatever prepare(root)
    adds to the copy); waits for the first scan."""
    root = os.path.join(tmp, "projects")
    shutil.copytree(FIXTURES, root, symlinks=True)
    if prepare:
        prepare(root)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    cfg = os.path.join(tmp, "servers.json")
    with open(cfg, "w") as f:  # no "socket": plain 127.0.0.1 port, fixtures only
        json.dump({"port": port, "servers": [], "projects_root": root, "projects_interval_seconds": 3600,
                   "state_dir": os.path.join(tmp, "state"), "sessions": {"enabled": False}}, f)
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
    "overview": "#/overview", "gpu": "#/gpu", "todo": "#/todo", "new": "#/new", "missing": "#/p/nope/overview",
    "p_overview": "#/p/demo/flow", "p_setup": "#/p/demo/setup", "p_setup_anchor": "#/p/demo/setup/ds:测试集",
    "p_road": "#/p/demo/roadmap", "p_road_t": "#/p/demo/flow/t:t-loss", "p_road_a": "#/p/demo/flow/a:2026-11-10-lr-sweep",
    "p_old_overview": "#/p/demo/overview", "p_road_v": "#/p/demo/roadmap/v:v2", "p_failed": "#/p/demo/flow/a:2026-11-12-aug-mixup",
    "legacy_exp": "#/p/demo/experiments/2026-11-15-focal", "legacy_track": "#/p/demo/experiments?track=t-lr",
    "p_runs": "#/p/demo/runs", "p_run": "#/p/demo/runs/r001", "p_runs_data": "#/p/demo/runs?view=data",
    "p_examples": "#/p/demo/examples", "p_docs": "#/p/demo/docs", "p_doc_notes": "#/p/demo/docs/NOTES.md",
    "p_doc_review": "#/p/demo/docs/focal-review.md", "p_doc_fig": "#/p/demo/docs/lr-warmup.html", "p_doc_untitled": "#/p/demo/docs/untitled.html", "p_method": "#/p/demo/method", "p_method_warmup": "#/p/demo/method/warmup",
    "p_method_ramp": "#/p/demo/method/warmup-ramp",
    "p_home": "#/p/demo/home", "p_tasks": "#/p/demo/tasks", "p_task": "#/p/demo/tasks/f1-metric", "p_decisions": "#/p/demo/decisions",
    "p_decision": "#/p/demo/decisions/scale-later", "p_minutes": "#/p/demo/decisions/doc:2026-11-20-discussion.md",
    "p_timeline": "#/p/demo/timeline", "p_upkeep": "#/p/demo/upkeep",
    "p_papers": "#/p/demo/papers", "p_papers_unread": "#/p/demo/papers?f=unread", "p_paper": "#/p/demo/papers/radam", "sessions": "#/sessions", "design": "#/design",
    "legacy_project": "project.html?p=demo", "legacy_projects": "#projects", "root": "", "guide": "#/guide",
}


def main_html(dom):
    m = re.search(r'<div class="page" id="main">(.*)</div></main>', dom, re.S)
    return m.group(1) if m else ""


def title(dom):
    return re.search(r"<title>(.*?)</title>", dom).group(1)


def make_grid(root):
    """A project whose runs form a grid (condition × strategy × model), for the runs table and the cross table."""
    b = os.path.join(root, "grid", "board")
    os.makedirs(os.path.join(b, "attempts"))
    def put(rel, obj):
        os.makedirs(os.path.dirname(os.path.join(b, rel)), exist_ok=True)
        with open(os.path.join(b, rel), "w", encoding="utf-8") as f:
            f.write(obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False))
    put("project.json", {"title": "网格实验", "status": "进行中", "display": {
        "headline": {"group": "ag", "metric": "false_allow", "better": "lower"}, "primary_metrics": ["false_allow", "false_block", "cost"]}})
    put("setup.json", {"goal": "g", "datasets": [{"id": "ag", "name": "AuthGate"}], "metrics": [
        {"id": "false_allow", "name": "错误放行", "better": "lower"}, {"id": "false_block", "name": "误拦", "better": "lower"},
        {"id": "cost", "name": "每条花费", "better": "lower"}, {"id": "n", "name": "条数"}]})
    for aid in ("2026-11-25-info", "2026-11-25-other"):
        put("attempts/%s.md" % aid, "---\nid: %s\ntrack: null\ndate: 2026-11-25\nstatus: 进行中\n---\n## 例子\nx\n" % aid)
    img = '<img src=x onerror="window.__pwned=1">'
    rows = [("g01", "info", "action_only", "direct", "haiku", .15, .86), ("g02", "info", "action_only", "decompose", "haiku", .10, .80),
            ("g03", "info", "default", "direct", "haiku", .20, .00), ("g04", "info", "default", "decompose", "haiku", .25, .02),
            ("g05", "info", "signals", "direct", "haiku", .30, .05), ("g06", "info", "signals", "direct", "haiku", .40, .07),
            ("g07", "info", "default", "direct", "sonnet", .05, .00), ("g08", "info", "action_only", "direct", "sonnet", .50, .90),
            ("g09", "other", "default", "direct", "haiku", .12, .01), ("g10", "info", img, "direct", "haiku", .33, .10)]
    for i, (rid, att, cond, strat, model, fa, fb) in enumerate(rows):
        config = {"bench": "authgate", "condition": cond, "strategy": strat, "model": model, "n_samples": 200}
        if rid in ("g05", "g06"):
            config["seed"] = int(rid[-1]) - 4
        n = 20 if rid == "g08" else 200                                   # g08 is a spot check
        put("runs/%s/run.json" % rid, {"id": rid, "kind": "eval", "attempt": "2026-11-25-" + att, "status": "被中断" if rid == "g07" else "完成",
                                        "started": "2026-11-25T%02d:00:00Z" % (i + 1), "config": config,
                                        "metrics": {"ag": {"n": n, "of": 200, "false_allow": fa, "false_block": fb, "cost": round(0.002 + i * 0.0001, 4)}}})


GRID_ROUTES = {"t": "#/p/grid/runs", "worst": "#/p/grid/runs?sort=m:false_allow&desc=1", "where": "#/p/grid/runs?where=model:haiku",
               "att": "#/p/grid/runs?att=2026-11-25-other", "st": "#/p/grid/runs?st=%E8%A2%AB%E4%B8%AD%E6%96%AD",
               "pv": "#/p/grid/runs?view=pivot", "pv2": "#/p/grid/runs?view=pivot&att=2026-11-25-info&rows=model&cols=condition&metric=false_block"}
RUN_ROWS = r'<tr class="run[^"]*" data-href="#/p/grid/runs/(g\d+)'


@unittest.skipUnless(CHROME, "Chromium not installed")
class RunsTable(unittest.TestCase):
    """The runs page as one table: settings that differ, metrics, sorting and filters in the address."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.proc, cls.base, cls.log = start_server(REPO, cls.tmp, prepare=make_grid)
        cls.pages = {k: dump(cls.base + v, 4000) for k, v in GRID_ROUTES.items()}

    @classmethod
    def tearDownClass(cls):
        stop_server(cls.proc, cls.log)
        shutil.rmtree(cls.tmp)

    def m(self, key):
        self.assertEqual(self.pages[key][1], [])                              # no JS errors
        return main_html(self.pages[key][0])

    def test_table(self):
        t = self.m("t")
        self.assertNotIn('class="bars"', t)
        heads = [re.sub(r"<[^>]+>", "", h) for h in re.findall(r"<th[^>]*>(.*?)</th>", t)]
        self.assertEqual(heads, ["运行", "实验", "condition", "strategy", "model", "错误放行 ▲", "误拦", "每条花费", "开始"])   # seed: 2 of 10 runs
        self.assertEqual(re.findall(RUN_ROWS, t), ["g07", "g02", "g09", "g01", "g03", "g04", "g05", "g10", "g06", "g08"])  # headline, lower first
        self.assertIn("这些运行共同的设置：<code>bench</code> = authgate，<code>n_samples</code> = 200", t)
        row = lambda rid: re.search(r'data-href="#/p/grid/runs/%s".*?</tr>' % rid, t, re.S).group(0)
        self.assertIn('<td class="n mcell best">', row("g07"))                 # the best error rate
        self.assertIn('<span class="pill t-', row("g07"))                       # 被中断 shows; 完成 does not
        self.assertNotIn('<span class="pill t-', row("g01"))
        self.assertIn('mcell spot" title="抽查：只评了 20/200 条，不参与比较"', row("g08"))
        self.assertIn('href="#/p/grid/runs?where=condition%3Aaction_only"', row("g01"))   # a value filters the table
        self.assertIn('href="#/p/grid/runs?sort=m%3Afalse_allow&amp;desc=1">错误放行 ▲</a>', t)   # the sorted header flips
        self.assertIn("&lt;img src=x onerror=", row("g10"))                     # settings are text
        self.assertNotIn("<img", t)

    def test_sort_and_filters(self):
        self.assertEqual(re.findall(RUN_ROWS, self.m("worst"))[:2], ["g08", "g06"])
        w = self.m("where")
        self.assertEqual(set(re.findall(RUN_ROWS, w)), {"g01", "g02", "g03", "g04", "g05", "g06", "g09", "g10"})
        self.assertIn('title="去掉这个条件">model = haiku ✕</a>', w)
        self.assertEqual(re.findall(RUN_ROWS, self.m("att")), ["g09"])
        self.assertEqual(re.findall(RUN_ROWS, self.m("st")), ["g07"])
        self.assertIn('<option value="被中断" selected="">', self.m("st"))


    def test_cross_table(self):
        pv = self.m("pv")
        self.assertIn('<a class="chip on" href="#/p/grid/runs?view=pivot">交叉表</a>', pv)
        self.assertIn("这些运行来自 2 个实验", pv)                               # a hint to pick one
        table = re.search(r'<table class="pivot">.*?</table>', pv, re.S).group(0)
        heads = re.findall(r"<tr>(.*?)</tr>", table, re.S)[:2]
        self.assertEqual(re.findall(r'<th[^>]*colspan="(\d+)"[^>]*>(\w+)</th>', heads[0]), [("2", "direct"), ("1", "decompose")])   # strategy, then model
        self.assertEqual(re.findall(r"<th[^>]*>(\w+)</th>", heads[1]), ["haiku", "sonnet", "haiku"])
        body = re.findall(r'<tr class="pvr"><th[^>]*>(.*?)</th>(.*?)</tr>', table, re.S)
        self.assertEqual([h for h, _ in body], ["action_only", "default", "signals", '&lt;img src=x onerror="window.__pwned=1"&gt;'])  # order of first run
        cells = {h: re.findall(r"<td[^>]*>(.*?)</td>", row, re.S) for h, row in body}
        text = lambda h, i: re.sub(r"<[^>]+>", " ", cells[h][i]).split()
        self.assertEqual(text("default", 0), ["0.1600", "×2"])                    # g03 0.20 and g09 0.12 averaged
        self.assertEqual(text("signals", 0), ["0.3500", "×2"])
        self.assertEqual(text("signals", 1), ["—"])                               # no run there
        row = lambda h: re.search(r'<tr class="pvr"><th[^>]*>%s</th>.*?</tr>' % h, table, re.S).group(0)
        self.assertEqual(row("default").count('class="pv best"'), 1)              # 0.05 (g07), the lowest
        self.assertIn('class="pv spot"', row("action_only"))                      # g08 is a spot check
        self.assertIn('href="#/p/grid/runs?where=condition%3Adefault%3Bstrategy%3Adirect%3Bmodel%3Ahaiku"', table)   # a cell opens its runs
        self.assertNotIn("<img", pv)
        self.assertIn('<select data-rq="rows">', pv)
        pv2 = self.m("pv2")
        self.assertNotIn("这些运行来自", pv2)
        table2 = re.search(r'<table class="pivot">.*?</table>', pv2, re.S).group(0)
        self.assertEqual([h for h in re.findall(r'<tr class="pvr"><th[^>]*>(.*?)</th>', table2)], ["haiku", "sonnet"])
        self.assertIn("误拦", re.search(r'<select data-rq="metric">.*?</select>', pv2, re.S).group(0).split("selected")[1][:40])


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

    def m(self, key):
        return main_html(self.pages[key][0])

    def test_no_js_errors_and_no_injected_scripts(self):
        for k, (dom, errors) in self.pages.items():
            with self.subTest(route=k):
                self.assertEqual(errors, [])
                self.assertEqual(len(re.findall(r"<script\b", dom)), 4)  # common.js + work.js + design.js + app.js only

    def test_sidebar_and_titles(self):
        d = self.pages["p_overview"][0]
        self.assertIn('<a class="brand" href="#/overview">CD的研究控制台</a>', d)
        self.assertIn('<span class="lbl">待办</span><span class="cnt">7</span>', d)   # 2 pending attempts + 2 open to-dos + 2 candidate decisions + 1 global
        self.assertRegex(d, r'<a class="sitem on" href="#/p/demo/home"[^>]*><span class="dot d-run"></span><span class="lbl">演示项目</span>')
        self.assertEqual(title(self.pages["gpu"][0]), "(0 空) CD的研究控制台")
        for k in ["p_overview", "legacy_project"]:
            self.assertEqual(title(self.pages[k][0]), "演示项目 · CD的研究控制台", k)
        for k in ["legacy_projects", "root"]:
            self.assertIn('<h1 class="pt">今天</h1>', self.m(k), k)

    def test_tabs_follow_the_hierarchy(self):
        tabs = re.findall(r'<a class="ptab[^"]*" href="#/p/demo/(\w+)">([^<]+)', self.m("p_overview"))
        self.assertEqual(tabs, [("home", "概况"), ("method", "方法"), ("flow", "实验"), ("runs", "运行"), ("examples", "样例"), ("setup", "实验设置"),
                                ("docs", "文档"), ("papers", "论文"), ("tasks", "任务"), ("decisions", "讨论与决定"), ("sessions", "会话"), ("timeline", "时间线"), ("upkeep", "记录维护")])
        self.assertIn('<span class="pgrp">研究</span>', self.m("p_overview"))      # two views: research, then work
        self.assertIn('<span class="pgrp">工作</span>', self.m("p_overview"))
        self.assertIn('<a class="ptab on" href="#/p/demo/flow">', self.m("p_road"))    # the old roadmap is the flow
        self.assertIn('<a class="ptab on" href="#/p/demo/home">', self.m("p_old_overview"))   # the old overview is 概况

    def test_overview(self):                                    # the flow with nothing selected shows the project summary
        d = self.m("p_overview")
        self.assertIn("<h2>项目概况</h2>", d)
        self.assertIn("来自 project.json", d)
        self.assertIn('<p class="concl">预热 500 步后验证集准确率稳定提升约 2 个点。</p>', d)
        self.assertRegex(d, r'最好的准确率 · 同分布测试集</div>\s*<div class="v">0.8100</div><div class="s">来自 <b>线性预热 500 步</b>（488/602）')
        self.assertIn('href="#/p/demo/setup/ds%3A%E6%B5%8B%E8%AF%95%E9%9B%86"', d)          # summary links to the definition
        self.assertIn("弄清预热能否让小模型训练更稳定", d)
        self.assertIn('<span class="pill t-todo">卡着进度</span>', d)
        self.assertIn("补 dev 集评测", d)
        self.assertNotIn("确认 focal 的结论", d)                                              # done (last row wins)
        self.assertIn('<a href="#/p/demo/upkeep">记录维护：', d)                            # record problems moved out of the way
        self.assertNotIn("没有「例子」小节", d)
        self.assertIn("没有「例子」小节", self.m("p_upkeep"))
        self.assertNotIn("Tiny-6L</b>", d)                                                    # full tables live on the setup tab

    def test_project_head_and_home(self):
        d = self.m("p_home")
        hero = d.split('<div class="hero">', 1)[1].split("</nav>", 1)[0]
        self.assertIn("学习率预热能否稳定小模型训练？", hero)                                # the question
        self.assertIn('<a href="#/p/demo/decisions/warmup-first">先定预热，再试 loss</a>', hero)   # adopted decisions only
        self.assertNotIn("old-plan", hero)
        self.assertIn('<span class="pill t-run">进行中</span> <a href="#/p/demo/tasks/f1-metric">', hero)   # next tasks
        self.assertIn("预热有用，并入主线。", hero)                                         # latest evidence
        self.assertIn("最近的变化", d)                                                     # first visit in this browser
        self.assertIn('data-key="demo|d:scale-later"', d)                                  # waiting for you, adoptable here
        self.assertIn("下一步：重算 r002、r003。", d)                                       # what is going on
        self.assertIn('<div class="onepager">', d)

    def test_work_view(self):
        tasks = self.m("p_tasks")
        self.assertEqual(re.findall(r'<div class="kh"><span class="pill t-\w+">(\S+)</span>', tasks), ["待开始", "进行中", "等运行", "等你", "可以继续"])
        self.assertIn('<a class="tcard" href="#/p/demo/tasks/f1-metric">', tasks)
        task = self.m("p_task")
        self.assertIn("<code>src/eval/</code>", task)                                     # scope
        self.assertIn('<a href="#/p/demo/flow/a%3A2026-11-15-focal">', task)                # hangs on an attempt
        self.assertIn("<h3>交接</h3>", task)
        self.assertIn('在 App 的「demo」文件夹里新开一个对话，说「<code>做 f1-metric</code>」<button class="copy" data-c="做 f1-metric">复制</button>', task)
        dec = self.m("p_decision")
        self.assertIn('data-key="demo|d:scale-later"', dec)
        self.assertIn("点了「采纳」立即生效", dec)
        decs = self.m("p_decisions")
        self.assertIn('被「先定预热，再试 loss」替代', decs)                                 # replaced decisions say by what
        self.assertIn("讨论纪要 · 1", decs)
        self.assertIn("和 loss 两条支线谁先做", self.m("p_minutes"))
        tl = self.m("p_timeline")
        self.assertIn("新任务「加 F1 指标并重算旧结果」", tl)
        self.assertIn("定版 v2", tl)
        self.assertIn('<li class="day">', tl)
        self.assertIn("所有会话", self.m("sessions"))

    def test_design_page(self):
        d = self.m("design")
        self.assertEqual(len(re.findall(r'<section class="dz-sec" id="dz-', d)), 19)      # every section of the table of contents
        self.assertEqual(len(re.findall(r'<a href="#/design" data-goto="dz-', d)), 19)
        self.assertIn('<button id="dz-export"', d)
        self.assertEqual(len(re.findall(r'<svg class="dz-svg"', d)), 2)
        self.assertIn("进行中的项目", d)                                                  # live numbers from the console
        self.assertIn('<span class="lbl">设计思路</span>', self.pages["design"][0])
        self.assertEqual(title(self.pages["design"][0]), "设计思路 · CD的研究控制台")

    def test_setup_tab(self):
        d = self.m("p_setup")
        for a in ['data-anchor="ds:测试集"', 'data-anchor="mt:acc"', 'data-anchor="model:tiny"', 'data-anchor="method:warmup"']:
            self.assertIn(a, d)
        self.assertIn("越高越好", d)
        self.assertIn('<tr data-anchor="ds:测试集" class="flash">', self.m("p_setup_anchor"))

    def test_flow_graph_and_version(self):
        d = self.m("p_road_v")
        rows = re.findall(r'<a class="frow k-(\w+)[^"]*" href="#/p/demo/flow(?:/([^"?]+))?"', d)
        self.assertEqual([(k, unquote(i)) for k, i in rows], [
            ("start", ""), ("track", "t:t-old"), ("version", "v:v1"), ("track", "t:t-lr"), ("attempt", "a:2026-11-10-lr-sweep"),
            ("track", "t:t-aug"), ("attempt", "a:2026-11-12-aug-mixup"), ("version", "v:v2"), ("track", "t:t-loss"),
            ("attempt", "a:2026-11-15-focal"), ("attempt", "a:2026-11-16-focal-gamma1"), ("attempt", "a:2026-11-17-odd-status"),
            ("track", ""), ("attempt", "a:2026-11-18-no-frontmatter"), ("attempt", "a:2026-11-19-unclosed")])   # merged steps above their version
        self.assertIn("没有归属支线的尝试", d)
        self.assertRegex(d, r'<a class="frow k-version on" href="#/p/demo/flow/v%3Av2"')
        self.assertIn("版本 v2 · 加入学习率预热", d)
        self.assertIn('<path class="fe merge', d)                                      # t-lr merges into v2
        self.assertIn('<path class="fe fork', d)
        self.assertIn('<path class="fend bad"', d)                                     # the abandoned track ends with a cross
        self.assertIn('class="fg a-todo"', d)                                          # the 待确认 step
        self.assertIn('<span class="ff todo">等你确认</span>', d)

    def test_flow_track(self):
        d = self.m("p_road_t")
        self.assertIn('<a href="#/p/demo/flow/v%3Av2">版本 v2</a>', d)
        self.assertEqual(len(re.findall(r'<span class="stepn">', d)), 3)
        self.assertIn("这条支线的各次运行 · 准确率（数字来自运行记录）", d)
        self.assertIn('title="r002">Focal loss</a>', d)

    def test_flow_attempt_reads_as_a_story(self):
        d = self.m("p_road_a")
        self.assertIn('版本 v1</a> <span class="small">›</span> <a href="#/p/demo/flow/t%3At-lr">学习率调度</a>', d)
        heads = re.findall(r'<div class="sec[^"]*"><h3>(.*?)</h3>', d)
        self.assertEqual([re.sub(r"<.*", "", h).strip() for h in heads[:4]], ["发生了什么", "一个例子", "数据", "结论和原因"])
        self.assertIn('<div class="onepager"><div class="oprow op-想回答"><span class="opk">想回答</span>', d)   # the 一页纸 first
        self.assertIn('<div class="oprow op-所以"><span class="opk">所以</span><div class="opv">预热有用，并入主线。</div>', d)
        self.assertIn('<abbr class="term" title="训练刚开始的几百步把学习率从很小慢慢调到目标值。">预热</abbr>', d)   # terms explained once
        self.assertIn('<div class="vsh">预期</div>', d)                                 # expected next to observed
        self.assertRegex(d, r'<th>测试集</th><th class="num" title="[^"]*">准确率 ↑</th><th class="num" title="[^"]*">F1 ↑</th>')
        self.assertIn('<td class="num">0.8100 <span class="small">(488/602)</span></td>', d)
        self.assertIn(">线性预热 500 步</a>", d)
        self.assertIn("下一步：", d)                                                   # steps follow the flow
        failed = self.m("p_failed")
        self.assertIn('<a class="frow k-attempt s-bad on"', failed)                    # failures are marked on the graph
        self.assertIn('<div class="oneline s-bad">', failed)
        self.assertRegex(self.m("legacy_exp"), r'<a class="frow k-attempt s-todo on" href="#/p/demo/flow/a%3A2026-11-15-focal"')
        self.assertRegex(self.m("legacy_track"), r'<a class="frow k-track on" href="#/p/demo/flow/t%3At-lr"')
        self.assertIn('data-key="demo|a:2026-11-15-focal"', self.m("legacy_exp"))     # confirm right on the step

    def test_runs(self):
        d = self.m("p_runs")
        self.assertNotIn('class="md2 wide"', d)                       # full-width table until a run is picked
        self.assertNotIn('class="bars"', d)                              # the table comes first, no bar chart
        self.assertIn('<th class="num sorted" title="判定与标准答案一致的比例；越高越好；acc"><a href="#/p/demo/runs?sort=m%3Aacc&amp;desc=0">准确率 ▼</a></th>', d)
        self.assertEqual(re.findall(r'<tr class="run[^"]*" data-href="#/p/demo/runs/(\w+)"', d), ["r001", "r002"])   # best first
        self.assertIn('<td class="n mcell best"><i style="width:100.0%"></i><span>0.8100 <span class="small">(488/602)</span></span></td>', d)
        self.assertIn("同分布测试集</b>（602 条）：和训练集同一个生成器、不同随机种子。", d)
        self.assertIn(">数据 1</a>", d)
        run = self.m("p_run")
        self.assertIn('class="md2 wide"', run)
        self.assertIn('学习率调度</a> <span class="small">›</span> <a href="#/p/demo/flow/a%3A2026-11-10-lr-sweep">lr-sweep</a>', run)
        self.assertRegex(run, r'<table class="kv">.*<th>warmup</th><td class="num">500</td>')
        self.assertNotIn('{"lr"', run)
        self.assertIn("<b>r005</b>", self.m("p_runs_data"))

    def test_method_levels_and_links(self):
        top = self.m("p_method")
        line = re.findall(r'<a class="mstep[^"]*" href="#/p/demo/method/([\w-]+)"', top)
        self.assertEqual([x for x in line if x != "broken"], ["data", "train", "eval"])   # the top level follows its arrows
        self.assertIn("broken", line)                                                     # a step with a wrong parent still shows
        self.assertIn('<span class="tag2">2 种做法</span>', top)                          # train has a variant
        w = self.m("p_method_warmup")
        self.assertIn('<a class="mstep on" href="#/p/demo/method/train"', w)              # the variant's place is highlighted
        self.assertRegex(w, r'<a class="chip on" href="#/p/demo/method/warmup">线性预热</a>')
        self.assertIn("「线性预热」的子流程", w)
        self.assertEqual(re.findall(r'<a class="mstep[^"]*" href="#/p/demo/method/(warmup-[\w]+)"', w), ["warmup-ramp", "warmup-hold"])
        self.assertIn("「训练」的一种做法", w)
        self.assertIn("↺ 第 1–2 步（「升温」到「恒定」）循环，每个 epoch", w)            # a loop back on the same level
        self.assertIn('href="#/p/demo/flow/a%3A2026-11-10-lr-sweep">lr-sweep</a>', w)    # experiments on this step
        self.assertIn('href="#/p/demo/setup/method%3Awarmup"', w)                         # its setup.json method
        self.assertIn('<a class="ref" href="#/p/demo/runs/r001"', w)                       # numbers come from the run
        ramp = self.m("p_method_ramp")
        self.assertIn('<a href="#/p/demo/method/warmup">线性预热</a>', ramp)               # breadcrumbs up the levels
        self.assertIn('<a class="mstep on inloop" href="#/p/demo/method/warmup-ramp"', ramp)   # selected, and inside the loop
        self.assertIn('改的是方法里的：</span><a class="chip" href="#/p/demo/method/warmup">线性预热</a>', self.m("p_road_a"))
        self.assertIn('href="#/p/demo/method/warmup">怎么做 →</a>', self.m("p_setup"))

    def test_examples_and_docs(self):
        ex = self.m("p_examples")
        self.assertIn("展开完整原文", ex)
        self.assertNotIn("FULL-TEXT-MARKER", ex)
        self.assertIn('&lt;script&gt;alert("xss in walkthrough")&lt;/script&gt;', ex)
        self.assertIn("观察样例", ex)
        docs = self.m("p_docs")
        groups = re.findall(r'<div class="tgroup">(.*?)</div>', docs)               # grouped by where they belong in the flow
        self.assertEqual([re.sub(r"<[^>]+>", "", g) for g in groups], ["整个项目", "支线 · 学习率调度", "学习率调度 › 尝试 · lr-sweep",
                                                                       "版本 v2 · 加入学习率预热", "损失函数 › 尝试 · focal", "找不到的位置：a:ghost"])
        self.assertIn('<a class="li old" href="#/p/demo/docs/lr-plan-v0.md', docs)      # replaced documents are greyed
        self.assertIn("已被「学习率方案」取代", docs)
        self.assertIn('<span class="pill t-other">未看</span>', docs)
        self.assertIn('data-mark="确认"', docs)
        self.assertIn('>未看 10</a>', docs)                                                # filter chips (8 documents, 2 figures)
        review = self.m("p_doc_review")
        self.assertRegex(review, r'属于：<a href="#/p/demo/flow/a%3A2026-11-15-focal">[^<]*尝试 · focal</a>')
        self.assertIn('<a class="ref" href="#/p/demo/runs/r002"', review)                 # {{run:...}} shows the run's number
        self.assertIn('📄1', self.m("p_road_v"))                                          # the flow step shows its document
        self.assertIn('&lt;script&gt;alert("xss in plan")&lt;/script&gt;', docs)
        self.assertNotIn('href="javascript:', docs.lower())
        self.assertIn("2026-11-20：加了 focal loss。", self.m("p_doc_notes"))

    def test_figures(self):
        fig = self.m("p_doc_fig")
        self.assertIn('<h2>预热 &amp; 不预热：开头 1000 步的训练损失 <span class="tag2">图解</span>', fig)   # <title>, entities decoded once
        self.assertRegex(fig, r'属于：<a href="#/p/demo/flow/a%3A2026-11-10-lr-sweep">[^<]*尝试 · lr-sweep</a>')   # <meta name="about">
        self.assertIn('<p class="figq">回答：加 500 步预热，训练开头的损失尖峰还在不在？</p>', fig)              # <meta name="question">
        frames = re.findall(r"<iframe\b[^>]*>", fig)
        self.assertEqual(len(frames), 1)
        self.assertIn('src="/files/demo/figures/lr-warmup.html" sandbox="allow-scripts"', frames[0])     # isolated: scripts, no origin
        self.assertNotIn("same-origin", frames[0])
        self.assertIn('<a href="/files/demo/figures/lr-warmup.html" target="_blank" rel="noopener noreferrer">在新窗口打开', fig)
        self.assertIn('data-mark="确认" data-file="figures/lr-warmup.html"', fig)                       # marked like a document
        self.assertIn('<a class="chip" href="#/p/demo/docs/lr-warmup.html?k=%E5%9B%BE%E8%A7%A3">图解</a>', fig)   # a kind of its own
        self.assertNotIn("postMessage", fig)                                                           # the page is not inlined
        self.assertIn('<span class="tag2">图解</span><a href="#/p/demo/docs/lr-warmup.html">预热 &amp; 不预热', self.m("p_road_a"))  # on its step
        self.assertIn("<h2>untitled <span", self.m("p_doc_untitled"))                                   # no <title>: the file name
        self.assertIn("找不到 a:ghost", self.m("p_doc_untitled"))

    def test_papers(self):
        m = self.m("p_papers")
        groups = re.findall(r'<div class="tgroup">(.*?)</div>', m)
        self.assertEqual(groups[0], "整个项目")                                         # then the flow's order, task cards last
        self.assertEqual([re.sub(r"<[^>]+>", "", g) for g in groups[1:]],
                         ["支线 · 学习率调度", "学习率调度 › 尝试 · lr-sweep", "数据增强 C# 实验 › 尝试 · aug-mixup", "支线 · 损失函数",
                          "任务 · 加 F1 指标并重算旧结果"])
        self.assertIn('<a href="#/p/demo/tasks/f1-metric">任务 · 加 F1 指标并重算旧结果</a>', groups[-1])
        self.assertEqual(re.findall(r'<li class="paper[^"]*" data-anchor="([^"]+)"', m),
                         ["sgdr", "goyal-large-batch", "radam", "mixup", "focal-loss", "f-score"])   # old-survey was dropped
        self.assertIn('<a href="https://arxiv.org/abs/1706.02677" target="_blank" rel="noopener noreferrer">Accurate, Large Minibatch SGD', m)
        self.assertEqual(m.count('<span class="pill t-todo">必读</span>'), 2)
        self.assertEqual(m.count(">未核实</span>"), 1)                                   # radam has no `checked`
        self.assertIn('<li class="paper read" data-anchor="focal-loss">', m)
        self.assertIn('<input type="checkbox" data-paper="focal-loss" checked="">', m)
        self.assertIn("读后要点：用 (1-p)^γ 压低易分样本的损失", m)
        self.assertIn('<input type="checkbox" data-paper="radam">', m)
        unread = self.m("p_papers_unread")
        self.assertIn("On the Variance of the Adaptive Learning Rate", unread)
        self.assertNotIn("Focal Loss", unread)                                           # the filter hides read ones
        self.assertIn('<li class="paper flash" data-anchor="radam">', self.m("p_paper"))   # papers/<id> points at the row
        head = self.pages["p_papers"][0]
        self.assertIn('论文<span class="cnt">5 未读 / 6</span>', head)
        self.assertIn('<a class="small" href="#/p/demo/papers">论文 6 篇 · 读过 1 · <b>必读未读 2</b></a>', head)
        self.assertRegex(self.m("p_overview"), r'<div class="k">论文</div><div class="v">6</div>\s*<div class="s">读过 <b>1</b> · 必读未读 <b>2</b>')
        att = self.m("p_road_a")                                                          # a step lists its papers
        self.assertIn("这一步的论文", att)
        self.assertIn('data-paper="radam"', att)
        self.assertNotIn('data-paper="goyal-large-batch"', att)                          # that one sits on the track
        task = self.m("p_task")
        self.assertIn("这一步的论文", task)
        self.assertIn("A Probabilistic Interpretation of Precision, Recall and F-Score", task)

    def test_guide_and_entry_status(self):
        g = self.m("guide")
        for s in ["新建项目", "一个项目怎么推进", "暂停、恢复、结束", "结束或放弃", "已归档", "待办怎么回复", "直接处理", "常用命令", "board task-start", "密钥和 token", "BOARD_FORMAT.md"]:
            self.assertIn(s, g)
        self.assertIn("! ~/bin/rc-start &lt;名字&gt;", g)
        self.assertIn("! tmux send-keys -t '=rc-&lt;名字&gt;:' y Enter", g)
        self.assertIn("! ~/bin/rc-start --all", g)
        self.assertEqual(g.count("常驻入口未运行"), 3)                    # no rc-demo etc. in the test tmux
        self.assertIn("<code>! ~/bin/rc-start demo</code>", g)
        self.assertNotIn("<h3>使用说明</h3>", g)                          # the file's own title is dropped
        self.assertIn('<span class="lbl">使用说明</span>', self.pages["guide"][0])
        self.assertIn('href="#/guide" title="在 a100 会话里输入：! ~/bin/rc-start demo">常驻入口未运行</a>', self.m("p_overview"))
        self.assertEqual(title(self.pages["guide"][0]), "使用说明 · CD的研究控制台")

    def test_global_pages(self):
        ov = self.m("overview")
        self.assertEqual(len(re.findall(r'<a class="pcard"', ov)), 3)
        self.assertIn("等你决定 <span class=\"meta\">6 条还没回复</span>", ov)
        self.assertIn('<a class="ig" href="#/p/demo/decisions/scale-later">采纳或不采纳 →</a>', ov)   # one line each, linking to where you reply
        self.assertIn("没有心跳，h200 上看不到进程或连不上", ov)                          # r002: 状态未知 is stuck, not "interrupted"
        live, _, arch = ov.partition('<details class="box ovarch" id="ov-arch">')       # archived projects are folded last
        self.assertIn("演示项目", live)
        self.assertIn("只有标题的项目", arch)
        self.assertNotIn("只有标题的项目", live)
        side = self.pages["overview"][0]
        self.assertRegex(side, r'<details class="sarch" id="side-arch"><summary class="shead">已归档 1</summary><a class="sitem" href="#/p/minimal/home"')
        self.assertIn('<details class="sarch" id="side-arch"><summary', self.pages["p_overview"][0])  # folded unless you are in one
        todo = self.m("todo")
        demo, _, shared = todo.partition("跨项目")                                    # grouped by project, shared last
        self.assertLess(demo.index("补 dev 集评测"), demo.index("决定要不要补跑"))       # blocking before whenever
        self.assertIn('<button class="opt" data-opt="现在补">现在补<span class="rec">建议</span></button>', demo)   # choices, suggested one marked
        self.assertIn("dev 集还没评测", demo)                                           # background shown with the question
        self.assertIn('data-key="demo|a:2026-11-15-focal"', demo)                       # a 待确认 attempt can be confirmed here
        self.assertIn('<button class="opt" data-opt="失败">失败<span class="rec">建议</span></button>', demo)
        self.assertIn("1 条尝试缺「结论和原因」", demo)
        self.assertNotIn("确认 focal 的结论</span>", todo)                            # done: only in the done list, struck out
        self.assertIn("给所有项目加远程仓库", shared)
        self.assertIn("现在能跑的卡", self.m("gpu"))
        self.assertIn("显卡一览", self.m("gpu"))
        self.assertNotIn("挤一挤", self.m("gpu"))
        self.assertIn("没有这个项目", self.m("missing"))


CLICKS = """<script>
const wait = ms => new Promise(r => setTimeout(r, ms));
const ths = () => [...document.querySelector('#main table.runs').querySelectorAll('th')].length;
(async () => {
  const out = {};
  await wait(800);
  location.hash = '#/p/demo/runs'; await wait(600);
  out.cols_before = ths();
  document.querySelector('#main a.allcols').click(); await wait(300);
  out.cols_after = ths();
  document.querySelector('#main a.chip[data-g=""]').click(); await wait(300);
  out.other_group = document.querySelector('#main a.chip.on[data-g]').textContent.trim().startsWith('其他指标');
  document.querySelector('#main table.runs th a').click(); await wait(300);
  out.sorted_hash = location.hash;
  [...document.querySelectorAll('tr[data-href]')].find(tr => tr.textContent.includes('r002')).click(); await wait(300);
  out.run_hash = location.hash;
  out.run_detail = !!document.querySelector('#main .detail h2');
  location.hash = '#/p/demo/examples'; await wait(300);
  document.querySelector('[data-example]').click(); await wait(50);
  out.example_open = document.getElementById('main').innerHTML.includes('FULL-TEXT-MARKER');
  document.body.style.paddingBottom = '3000px'; window.scrollTo(0, 400); await wait(50);
  const items = [...document.querySelectorAll('#main a.li')];
  items[items.length - 1].click(); await wait(300);
  out.examples_scroll = window.scrollY;                       // picking another probe must not jump to the page top
  document.body.style.paddingBottom = '';
  location.hash = '#/p/demo/runs'; await wait(300);
  const sel = document.querySelector('select[data-rq="track"]'); sel.value = 't-loss'; sel.dispatchEvent(new Event('change', {bubbles: true})); await wait(300);
  out.track_hash = location.hash;
  location.hash = '#/p/demo/flow'; await wait(300);
  [...document.querySelectorAll('#main a.frow')].find(a => a.textContent.includes('focal-gamma1')).click(); await wait(300);
  out.selected = document.querySelector('#main .detail h2').textContent.trim().slice(0, 12);
  location.hash = '#/todo'; await wait(300);
  const box = document.querySelector('.reply[data-id="t3"]');
  box.querySelector('[data-opt="先不补"]').click();
  const ta = box.querySelector('textarea'); ta.value = '下周再说'; ta.dispatchEvent(new Event('input', {bubbles: true}));
  box.querySelector('[data-send]').click(); await wait(800);
  out.answered = (document.querySelector('#main .ans') || {}).textContent || '';
  location.hash = '#/p/demo/overview'; await wait(500);
  document.getElementById('rename-open').click(); await wait(50);
  document.getElementById('rename-input').value = '演示'; document.getElementById('rename-save').click(); await wait(800);
  out.renamed = document.querySelector('.phead h1').textContent + '|' + document.querySelector('#side a.sitem.on .lbl').textContent;
  location.hash = '#/p/demo/docs/lr-plan-v1.md'; await wait(500);
  document.querySelector('[data-mark="确认"]').click(); await wait(900);
  out.marked = (document.querySelector('#main .li.on .pill') || {}).textContent || '';
  const probes = [];   // the figure reports what it could reach, once per load
  window.addEventListener('message', e => { if (e.data && e.data.figureProbe) probes.push(e.data.figureProbe); });
  location.hash = '#/p/demo/docs/lr-warmup.html'; await wait(1500);
  const frame = document.querySelector('#main iframe.figframe');
  document.querySelector('[data-mark="看过"]').click(); await wait(1200);
  out.fig_kept = !!frame && frame.isConnected && document.querySelector('#main iframe.figframe') === frame;
  out.fig_marked = (document.querySelector('#main .li.on .pill') || {}).textContent || '';
  out.fig_probes = probes;
  location.hash = '#/gpu'; await wait(300);
  out.sidebar_on = document.querySelector('#side a.sitem.on .lbl').textContent;
  location.hash = '#/p/demo/papers'; await wait(600);
  const pbox = () => document.querySelector('#main input[data-paper="radam"]');
  pbox().click(); await wait(1200);
  const after = await (await fetch('/api/project/demo')).json();
  out.ticked = [pbox().checked, pbox().closest('li').className, (after.paper_marks.radam || {}).mark || null];
  pbox().click(); await wait(1200);
  out.unticked = [pbox().checked, 'radam' in (await (await fetch('/api/project/demo')).json()).paper_marks];
  const fake = {name: 'demo', now: 0, papers: [
      {id: 'bad', title: '<b>x</b>', url: 'javascript:alert(1)', why: 'w'},
      {id: 'quote', title: 'q', url: 'https://x.org/a" onclick="alert(1)', why: 'w'},
      {id: 'away', title: 'Away', url: 'https://x.org/away', why: 'w'}],
    paper_marks: {}, papers_elsewhere: {away: ['jev-fraud']}};
  const pdiv = document.createElement('div');
  pdiv.innerHTML = '<ul>' + paperItems(fake).map(p => paperRow(fake, p)).join('') + '</ul>';
  out.paper_links = [...pdiv.querySelectorAll('a')].map(a => [a.getAttribute('href'), a.getAttribute('target'), a.getAttribute('rel')]);
  out.paper_bold = pdiv.querySelectorAll('b').length;
  out.paper_away = pdiv.querySelector('[data-anchor="away"]').textContent.replace(/\\s+/g, ' ');
  out.doc_about = ['k:f1-metric', 'd:scale-later', 'x:nope'].map(a => docItems({docs: [{file: 'docs/z.md', meta: {about: a}, mtime: 1}], marks: {}})[0].about);
  const sess = document.createElement('div');   // a big context matters only while the session is open
  sess.innerHTML = sessionRows([{id: 'aaaaaaaa-1', name: 'done', live: false, context: 200000, last_active: 100},
                                {id: 'bbbbbbbb-2', name: 'open', live: true, status: 'idle', context: 200000, last_active: 100}], 200, false);
  out.sessions = [...sess.querySelectorAll('tr')].slice(1).map(tr => [tr.className, tr.textContent.includes('建议换新')]);
  const pre =document.createElement('pre'); pre.id = 'clicks'; pre.textContent = JSON.stringify(out); document.body.appendChild(pre);
})();
<\\/script>
</body>"""


@unittest.skipUnless(CHROME, "Chromium not installed")
class Interactions(unittest.TestCase):
    """Clicks on a copy of the repo whose index.html has a test script appended."""

    def test_clicks(self):
        tmp = tempfile.mkdtemp()
        try:
            repo = os.path.join(tmp, "repo")
            os.mkdir(repo)
            for f in ["server.py", "projects.py", "sessions.py", "index.html", "project.html", "common.js", "work.js", "app.js"]:
                shutil.copy(os.path.join(REPO, f), repo)
            page = os.path.join(repo, "index.html")
            with open(page, encoding="utf-8") as f:
                text = f.read()
            with open(page, "w", encoding="utf-8") as f:
                f.write(text.replace("</body>", CLICKS.replace("<\\/script>", "</" + "script>"), 1))
            proc, base, log = start_server(repo, tmp)
            try:
                dom, errors = dump(base + "#/overview", 22000)
            finally:
                stop_server(proc, log)
        finally:
            shutil.rmtree(tmp)
        self.assertEqual(errors, [])
        m = re.search(r'<pre id="clicks">(.*?)</pre>', dom, re.S)
        self.assertTrue(m, "click script did not finish")
        out = json.loads(html.unescape(m.group(1)))
        self.assertGreater(out["cols_after"], out["cols_before"])
        self.assertTrue(out["other_group"])
        self.assertTrue(out["run_hash"].startswith("#/p/demo/runs/r002"), out["run_hash"])   # keeps the table's sort and filters
        self.assertIn("sort=name", out["sorted_hash"])                          # a header sorts
        self.assertTrue(out["run_detail"])
        self.assertTrue(out["example_open"])
        self.assertEqual(out["track_hash"], "#/p/demo/runs?track=t-loss")
        self.assertTrue(out["selected"].startswith("focal-gamma1"), out["selected"])
        self.assertEqual(out["sidebar_on"], "GPU")
        self.assertEqual(out["sessions"], [["ended", False], ["ctxbig", True]])
        self.assertEqual(out["ticked"], [True, "paper read", "读过"])          # a tick is saved and shown at once
        self.assertEqual(out["unticked"], [False, False])                        # and can be taken back
        self.assertEqual(out["paper_links"], [["https://x.org/away", "_blank", "noopener noreferrer"],   # no javascript: or broken-out url
                                              ["#/p/jev-fraud/papers", None, None]])                # the other project's list
        self.assertEqual(out["paper_bold"], 0)                                     # titles are text
        self.assertIn("在 jev-fraud 勾过", out["paper_away"])
        self.assertEqual(out["doc_about"], ["k:f1-metric", "d:scale-later", None])  # documents can sit on task cards and decisions   # ended: greyed, no 建议换新
        self.assertGreater(out["examples_scroll"], 0)
        self.assertIn("先不补", out["answered"])                       # the reply is shown as sent, waiting for a session
        self.assertIn("下周再说", out["answered"])
        self.assertIn("等你说「处理回复」", out["answered"])            # answered items are grouped by where they stand
        self.assertEqual(out["renamed"], "演示|演示")                   # header and sidebar use the new name
        self.assertEqual(out["marked"], "已确认")                       # marking a document shows at once
        self.assertEqual(out["fig_marked"], "看过")                     # a figure is marked the same way
        self.assertTrue(out["fig_kept"])                                # re-rendering the page keeps the open figure's frame
        self.assertEqual(out["fig_probes"], [{"ran": True, "parent": "blocked", "storage": "blocked", "fetch": "blocked"}])  # loaded once, isolated


if __name__ == "__main__":
    unittest.main()
