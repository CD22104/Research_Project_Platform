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


def start_server(repo, tmp):
    """server.py from `repo` on a free 127.0.0.1 port, serving a copy of the fixtures; waits for the first scan."""
    root = os.path.join(tmp, "projects")
    shutil.copytree(FIXTURES, root, symlinks=True)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    cfg = os.path.join(tmp, "servers.json")
    with open(cfg, "w") as f:  # no "socket": plain 127.0.0.1 port, fixtures only
        json.dump({"port": port, "servers": [], "projects_root": root, "projects_interval_seconds": 3600,
                   "state_dir": os.path.join(tmp, "state")}, f)
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
    "p_doc_review": "#/p/demo/docs/focal-review.md", "p_method": "#/p/demo/method", "p_method_warmup": "#/p/demo/method/warmup",
    "p_method_ramp": "#/p/demo/method/warmup-ramp",
    "legacy_project": "project.html?p=demo", "legacy_projects": "#projects", "root": "", "guide": "#/guide",
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

    def m(self, key):
        return main_html(self.pages[key][0])

    def test_no_js_errors_and_no_injected_scripts(self):
        for k, (dom, errors) in self.pages.items():
            with self.subTest(route=k):
                self.assertEqual(errors, [])
                self.assertEqual(len(re.findall(r"<script\b", dom)), 2)  # common.js + app.js only

    def test_sidebar_and_titles(self):
        d = self.pages["p_overview"][0]
        self.assertIn('<a class="brand" href="#/overview">CD的研究控制台</a>', d)
        self.assertIn('<span class="lbl">待办</span><span class="cnt">5</span>', d)   # 2 pending attempts + 2 open project to-dos + 1 global
        self.assertRegex(d, r'<a class="sitem on" href="#/p/demo/flow"[^>]*><span class="dot d-run"></span><span class="lbl">演示项目</span>')
        self.assertEqual(title(self.pages["gpu"][0]), "(0 空) CD的研究控制台")
        for k in ["p_overview", "legacy_project"]:
            self.assertEqual(title(self.pages[k][0]), "演示项目 · CD的研究控制台", k)
        for k in ["legacy_projects", "root"]:
            self.assertIn('<h1 class="pt">总览</h1>', self.m(k), k)

    def test_tabs_follow_the_hierarchy(self):
        tabs = re.findall(r'<a class="ptab[^"]*" href="#/p/demo/(\w+)">([^<]+)', self.m("p_overview"))
        self.assertEqual(tabs, [("method", "方法"), ("flow", "实验"), ("runs", "运行"), ("examples", "样例"), ("setup", "实验设置"), ("docs", "文档")])
        for k in ["p_old_overview", "p_road"]:                                       # old addresses land on the flow
            self.assertIn('<a class="ptab on" href="#/p/demo/flow">', self.m(k), k)

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
        self.assertIn("记录检查", d)
        self.assertIn("没有「例子」小节", d)
        self.assertNotIn("Tiny-6L</b>", d)                                                    # full tables live on the setup tab

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
        self.assertIn('<div class="oneline s-ok">', d)                                 # one line first
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
        self.assertIn("准确率 · 同分布测试集", d)                         # bars
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
        self.assertIn('href="#/p/demo/flow/a%3A2026-11-10-lr-sweep">lr-sweep</a>', w)    # experiments on this step
        self.assertIn('href="#/p/demo/setup/method%3Awarmup"', w)                         # its setup.json method
        self.assertIn('<a class="ref" href="#/p/demo/runs/r001"', w)                       # numbers come from the run
        ramp = self.m("p_method_ramp")
        self.assertIn('<a href="#/p/demo/method/warmup">线性预热</a>', ramp)               # breadcrumbs up the levels
        self.assertIn('<a class="mstep on" href="#/p/demo/method/warmup-ramp"', ramp)
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
        self.assertEqual([re.sub(r"<[^>]+>", "", g) for g in groups], ["整个项目", "支线 · 学习率调度", "损失函数 › 尝试 · focal"])
        self.assertIn('<a class="li old" href="#/p/demo/docs/lr-plan-v0.md', docs)      # replaced documents are greyed
        self.assertIn("已被「学习率方案」取代", docs)
        self.assertIn('<span class="pill t-other">未看</span>', docs)
        self.assertIn('data-mark="确认"', docs)
        self.assertIn('>未看 7</a>', docs)                                                 # filter chips
        review = self.m("p_doc_review")
        self.assertRegex(review, r'属于：<a href="#/p/demo/flow/a%3A2026-11-15-focal">[^<]*尝试 · focal</a>')
        self.assertIn('<a class="ref" href="#/p/demo/runs/r002"', review)                 # {{run:...}} shows the run's number
        self.assertIn('📄1', self.m("p_road_v"))                                          # the flow step shows its document
        self.assertIn('&lt;script&gt;alert("xss in plan")&lt;/script&gt;', docs)
        self.assertNotIn('href="javascript:', docs.lower())
        self.assertIn("2026-11-20：加了 focal loss。", self.m("p_doc_notes"))

    def test_guide_and_entry_status(self):
        g = self.m("guide")
        for s in ["新建项目", "暂停和恢复", "结束或放弃", "已归档", "待办怎么回复", "常用命令", "密钥和 token", "BOARD_FORMAT.md"]:
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
        self.assertIn("4 条等你处理", ov)
        live, _, arch = ov.partition('<details class="box ovarch" id="ov-arch">')       # archived projects are folded last
        self.assertIn("演示项目", live)
        self.assertIn("只有标题的项目", arch)
        self.assertNotIn("只有标题的项目", live)
        side = self.pages["overview"][0]
        self.assertRegex(side, r'<details class="sarch" id="side-arch"><summary class="shead">已归档 1</summary><a class="sitem" href="#/p/minimal/flow"')
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
  document.querySelector('[data-allcols="main"]').click(); await wait(50);
  out.cols_after = ths();
  document.querySelector('button[data-view="main"][data-group=""]').click(); await wait(50);
  out.other_group = document.querySelector('button.tab.on').textContent.trim().startsWith('其他指标');
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
  const sel = document.querySelector('select[data-rtrack]'); sel.value = 't-loss'; sel.dispatchEvent(new Event('change', {bubbles: true})); await wait(300);
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
  location.hash = '#/gpu'; await wait(300);
  out.sidebar_on = document.querySelector('#side a.sitem.on .lbl').textContent;
  const pre = document.createElement('pre'); pre.id = 'clicks'; pre.textContent = JSON.stringify(out); document.body.appendChild(pre);
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
            for f in ["server.py", "projects.py", "index.html", "project.html", "common.js", "app.js"]:
                shutil.copy(os.path.join(REPO, f), repo)
            page = os.path.join(repo, "index.html")
            with open(page, encoding="utf-8") as f:
                text = f.read()
            with open(page, "w", encoding="utf-8") as f:
                f.write(text.replace("</body>", CLICKS.replace("<\\/script>", "</" + "script>"), 1))
            proc, base, log = start_server(repo, tmp)
            try:
                dom, errors = dump(base + "#/overview", 17000)
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
        self.assertEqual(out["run_hash"], "#/p/demo/runs/r002")
        self.assertTrue(out["run_detail"])
        self.assertTrue(out["example_open"])
        self.assertEqual(out["track_hash"], "#/p/demo/runs?track=t-loss")
        self.assertTrue(out["selected"].startswith("focal-gamma1"), out["selected"])
        self.assertEqual(out["sidebar_on"], "GPU")
        self.assertGreater(out["examples_scroll"], 0)
        self.assertIn("先不补", out["answered"])                       # the reply is shown as sent, waiting for a session
        self.assertIn("下周再说", out["answered"])
        self.assertEqual(out["renamed"], "演示|演示")                   # header and sidebar use the new name
        self.assertEqual(out["marked"], "已确认")                       # marking a document shows at once


if __name__ == "__main__":
    unittest.main()
