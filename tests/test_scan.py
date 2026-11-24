"""Scanner, cache and file-lookup tests for projects.py. Run: python3 -m unittest -v"""
import errno
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import projects as P  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures", "projects")


class FixtureCase(unittest.TestCase):
    """Each test gets a private copy of the fixtures plus symlinks that escape board/."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "projects")
        shutil.copytree(FIXTURES, self.root, symlinks=True)
        secret = os.path.join(self.tmp, "secret.md")
        with open(secret, "w") as f:
            f.write("---\nid: secret\n---\nmust never be read\n")
        board = os.path.join(self.root, "demo", "board")
        os.symlink(secret, os.path.join(board, "attempts", "leak.md"))
        os.symlink(secret, os.path.join(board, "runs", "r001", "leak.png"))
        os.symlink("../plan.md", os.path.join(board, "attempts", "inside-link.md"))  # stays in board/

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def demo(self):
        return P.scan_root(self.root)["demo"]


class Scan(FixtureCase):
    def test_which_dirs_are_projects(self):
        # _shared, .hidden, noboard are skipped; broken project.json still lists the project
        self.assertEqual(sorted(P.scan_root(self.root)), ["broken", "demo", "minimal"])

    def test_broken_and_minimal(self):
        out = P.scan_root(self.root)
        self.assertEqual(out["broken"]["project"], {})
        self.assertEqual([e["file"] for e in out["broken"]["errors"]], ["project.json"])
        m = out["minimal"]
        self.assertEqual((m["errors"], m["versions"], m["runs"], m["plan"]), ([], [], [], None))
        self.assertEqual(P.summary(m)["title"], "只有标题的项目")

    def test_records(self):
        d = self.demo()
        self.assertEqual([v["meta"]["id"] for v in d["versions"]], ["v1", "v2"])
        self.assertEqual(d["versions"][1]["meta"]["merged_tracks"], ["t-lr"])
        self.assertEqual(d["versions"][1]["meta"]["parent"], "v1")
        self.assertEqual({t["meta"]["id"]: t["meta"]["status"] for t in d["tracks"]},
                         {"t-aug": "放弃", "t-loss": "进行中", "t-lr": "并入主线", "t-old": "并入主线"})
        self.assertNotIn("ended", d["tracks"][0]["meta"])  # t-aug: missing field stays missing
        files = [a["file"] for a in d["attempts"]]
        self.assertNotIn("attempts/notes.txt", files)      # only .md
        self.assertNotIn("attempts/leak.md", files)        # symlink out of board/
        self.assertIn("attempts/inside-link.md", files)    # symlink within board/ is fine
        a = {x["file"].split("/")[1]: x for x in d["attempts"]}
        self.assertEqual(a["2026-11-15-focal.md"]["needs_input"], ["预期", "结论和原因", "结论待确认"])
        self.assertEqual(a["2026-11-10-lr-sweep.md"]["needs_input"], [])
        self.assertEqual(a["2026-11-12-aug-mixup.md"]["meta"]["status"], "失败")
        self.assertEqual(a["2026-11-16-focal-gamma1.md"]["needs_input"], ["结论和原因"])  # 部分成功 carries a conclusion
        self.assertEqual(a["2026-11-17-odd-status.md"]["meta"]["status"], "搁置中")       # unknown status kept as is
        self.assertEqual(a["2026-11-17-odd-status.md"]["needs_input"], [])
        self.assertEqual(a["2026-11-18-no-frontmatter.md"]["meta"], {})
        self.assertEqual(a["2026-11-19-unclosed.md"]["meta"], {})
        self.assertIn("字段区没有结束行", a["2026-11-19-unclosed.md"]["body"])
        self.assertIn("<script>", d["plan"]["body"])       # backend returns raw text; frontend escapes
        self.assertIn("<script>", d["example"]["walkthrough"]["body"])
        self.assertEqual(len(d["example"]["probe_head"]), 2)
        self.assertEqual(set(d["updated"]), {"project", "plan", "versions", "tracks", "attempts", "runs", "examples", "setup", "todos", "docs", "method", "tasks", "decisions", "figures", "papers"})

    def test_runs(self):
        d = self.demo()
        runs = {r["id"]: r for r in d["runs"]}
        self.assertEqual(sorted(runs), ["r001", "r002", "r003", "r004", "r005"])
        self.assertEqual(runs["r005"]["run"]["kind"], "data")
        r1 = runs["r001"]
        self.assertEqual(r1["run"]["metrics"]["测试集"]["acc"], 0.81)
        self.assertEqual(r1["series"]["total"], 50)
        self.assertEqual(r1["images"], ["runs/r001/curve.svg", "runs/r001/tiny.png"])  # not leak.png
        self.assertEqual(runs["r002"]["run"]["status"], "运行中")
        self.assertEqual(runs["r003"]["run"], {})
        self.assertEqual(runs["r004"]["run"], {})
        self.assertEqual(len(runs["r004"]["samples_head"]), 2)
        errs = {e["file"] for e in d["errors"]}
        self.assertEqual(errs, {"runs/r003/run.json", "attempts/leak.md", "runs/r001/leak.png"})

    def test_ignored_content_never_appears(self):
        blob = repr(self.demo())
        self.assertNotIn("看板应忽略这个文件", blob)   # FORMAT.md
        self.assertNotIn("legacy-site", blob)
        self.assertNotIn("must never be read", blob)  # symlink target outside board/

    def test_summary(self):
        s = P.summary(self.demo())
        self.assertEqual((s["title"], s["status"], s["current_version"]), ("演示项目", "进行中", "v2"))
        self.assertEqual([r["id"] for r in s["active_runs"]], ["r002"])
        self.assertEqual(s["active_runs"][0]["gpus"], [2, 3])
        self.assertEqual(s["n_errors"], 3)
        self.assertEqual((s["n_tracks"], s["n_attempts"], s["n_runs"]), (4, 8, 5))  # 8: setUp adds inside-link.md
        self.assertEqual(s["track_status"], {"并入主线": 2, "放弃": 1, "进行中": 1})
        self.assertEqual(s["attempt_status"], {"成功": 1, "失败": 1, "待确认": 1, "部分成功": 1, "搁置中": 1, "未写": 3})
        self.assertEqual([(x["id"], x["needs"]) for x in s["pending"]],
                         [("2026-11-15-focal", ["结论和原因", "结论待确认"]), ("2026-11-16-focal-gamma1", ["结论和原因"])])

    def test_summary_tolerates_odd_status_values(self):
        d = self.demo()
        d["attempts"][0]["meta"]["status"] = ["a", "b"]
        d["tracks"][0]["meta"]["status"] = 3
        s = P.summary(d)
        self.assertEqual(s["attempt_status"]["未写"], 4)
        self.assertEqual(s["track_status"]["未写"], 1)


class IOFailures(FixtureCase):
    def eio_under(self, prefix):
        real = P._read_bytes

        def fake(path, limit=None):
            if os.path.realpath(path).startswith(os.path.realpath(prefix)):
                raise OSError(errno.EIO, "Input/output error")
            return real(path, limit)
        return mock.patch.object(P, "_read_bytes", fake)

    def test_eio_keeps_previous_project_snapshot(self):
        first = P.scan_root(self.root)
        with self.eio_under(os.path.join(self.root, "demo")):
            second = P.scan_root(self.root, prev=first)  # no memo: forces re-reads
        self.assertEqual(second["demo"]["stale"]["error"], "OSError: [Errno 5] Input/output error")
        self.assertEqual(len(second["demo"]["attempts"]), len(first["demo"]["attempts"]))
        self.assertIsNone(second["minimal"]["stale"])

    def test_eio_on_unseen_project_gives_empty_stale_entry(self):
        with self.eio_under(os.path.join(self.root, "demo")):
            out = P.scan_root(self.root)
        self.assertEqual(out["demo"]["attempts"], [])
        self.assertTrue(out["demo"]["stale"])

    def test_missing_root(self):
        c = P.ProjectCache(os.path.join(self.tmp, "nope"), log=lambda m: None)
        self.assertFalse(c.scan_once())
        projects, status = c.snapshot()
        self.assertEqual(projects, {})
        self.assertTrue(status["error"].startswith("FileNotFoundError"))

    def test_root_failure_keeps_cache_and_logs(self):
        logs = []
        c = P.ProjectCache(self.root, log=logs.append)
        self.assertTrue(c.scan_once())
        with mock.patch.object(P, "scan_root", side_effect=OSError(errno.EIO, "Input/output error")):
            self.assertFalse(c.scan_once())
        projects, status = c.snapshot()
        self.assertEqual(sorted(projects), ["broken", "demo", "minimal"])
        self.assertIn("Input/output error", status["error"])
        self.assertIsNotNone(status["finished"])
        self.assertIn("Traceback", logs[-1])

    def test_hung_scan_does_not_block_readers(self):
        c = P.ProjectCache(self.root, log=lambda m: None)
        c.scan_once()
        release = threading.Event()
        real = P.scan_root

        def hang(*a, **k):
            release.wait(10)
            return real(*a, **k)
        with mock.patch.object(P, "scan_root", hang):
            t = threading.Thread(target=c.scan_once)
            t.start()
            time.sleep(0.2)
            t0 = time.time()
            projects, status = c.snapshot()
            self.assertLess(time.time() - t0, 0.05)
            self.assertIsNotNone(status["running_since"])
            self.assertIn("demo", projects)
            release.set()
            t.join(5)
        self.assertIsNone(c.snapshot()[1]["running_since"])

    def test_unchanged_files_are_not_reread(self):
        c = P.ProjectCache(self.root, log=lambda m: None)
        c.scan_once()
        with mock.patch.object(P, "_read_bytes", side_effect=AssertionError("re-read")) as rb, \
                mock.patch.object(P, "_complete_lines", side_effect=AssertionError("re-read")):
            self.assertTrue(c.scan_once())
            self.assertEqual(rb.call_count, 0)
        self.assertEqual(len(c.snapshot()[0]["demo"]["errors"]), 3)  # cached errors are kept


class Extras(FixtureCase):
    """setup.json, flow.json, to-dos, documents and record checks."""

    def test_setup_flow_docs(self):
        d = self.demo()
        self.assertEqual([x["id"] for x in d["setup"]["datasets"]], ["测试集"])
        self.assertEqual(len(d["flow"]["nodes"]), 3)
        self.assertEqual([x["file"] for x in d["docs"]], ["docs/2026-11-20-discussion.md", "docs/focal-review.md", "docs/hparams.md",
                                                          "docs/lr-plan-v0.md", "docs/lr-plan-v1.md", "NOTES.md", "REPORT.md"])
        self.assertIn("进度笔记", d["docs"][5]["body"])
        self.assertEqual((d["docs"][1]["meta"]["about"], d["docs"][1]["meta"]["kind"]), ("a:2026-11-15-focal", "复盘"))
        self.assertIn("setup", d["updated"])

    def test_root_doc_symlink_out_is_ignored(self):
        os.remove(os.path.join(self.root, "demo", "NOTES.md"))
        os.symlink(os.path.join(self.tmp, "secret.md"), os.path.join(self.root, "demo", "NOTES.md"))
        d = self.demo()
        self.assertEqual([x["file"] for x in d["docs"]], ["docs/2026-11-20-discussion.md", "docs/focal-review.md", "docs/hparams.md",
                                                          "docs/lr-plan-v0.md", "docs/lr-plan-v1.md", "REPORT.md"])
        self.assertNotIn("must never be read", repr(d))

    def test_todos_last_row_wins(self):
        d = self.demo()
        self.assertEqual([(t["id"], t["done"]) for t in d["todos"]], [("t2", None), ("t1", "2026-11-21T00:00:00Z"), ("t3", None)])
        self.assertEqual([t["id"] for t in P.summary(d)["todos"]], ["t2", "t3"])   # open ones only
        with open(os.path.join(self.root, "demo", "board", "todos.jsonl"), "a") as f:
            f.write("not json\n{\"id\": \"t9\"}\n")
        d = self.demo()
        self.assertIn({"file": "todos.jsonl", "error": "2 行无法解析或缺少 id / text"}, d["errors"])

    def test_global_todos(self):
        c = P.ProjectCache(self.root, log=lambda m: None)
        c.scan_once()
        self.assertEqual([(t["id"], t["done"]) for t in c.global_todos()], [("g1", None), ("g2", "2026-11-21T00:00:00Z")])

    def test_checks(self):
        board = os.path.join(self.root, "demo", "board")
        with open(os.path.join(board, "attempts", "2026-11-20-broken.md"), "w") as f:
            f.write("---\nid: 2026-11-20-broken\ntrack: nope\nruns: [r404]\n---\n## 例子\nx\n")
        os.makedirs(os.path.join(board, "runs", "r006"))
        with open(os.path.join(board, "runs", "r006", "run.json"), "w") as f:
            f.write('{"id": "r006", "attempt": "ghost", "metrics": {"新集": {"acc": 1}}}')
        checks = {(c["where"], c["what"]) for c in self.demo()["checks"]}
        for want in [("attempts/2026-11-20-broken.md", "所属支线「nope」不存在"),
                     ("attempts/2026-11-20-broken.md", "引用的运行「r404」不存在"),
                     ("runs/r006/run.json", "attempt「ghost」不存在"),
                     ("setup.json", "分组「新集」（1 次运行用到）没有在 datasets 里定义"),
                     ("setup.json", "方法「warmup」引用的运行「r999」不存在"),
                     ("attempts/2026-11-15-focal.md", "没有「例子」小节"),
                     ("attempts/2026-11-18-no-frontmatter.md", "没有字段区（开头的 --- 块），读不到 id、支线和状态")]:
            self.assertIn(want, checks)
        for want in [("method/broken.md", "parent「nope」不是方法里的步骤"),             # method steps link to real steps and tracks
                     ("method/broken.md", "tracks 里的支线「t-missing」不存在")]:
            self.assertIn(want, checks)
        self.assertFalse([c for c in checks if c[0].startswith("method/") and c[0] != "method/broken.md"])
        self.assertNotIn(("attempts/2026-11-10-lr-sweep.md", "没有「例子」小节"), checks)
        self.assertFalse([c for c in checks if c[0] == "tracks/t-old.md"])   # attached through v1.merged_tracks

    def test_checks_survive_odd_values(self):
        d = self.demo()
        d["versions"][0]["meta"]["merged_tracks"] = "t-old"
        d["attempts"][0]["meta"]["runs"] = "r001"
        d["setup"] = {"datasets": "x", "metrics": [1, None], "methods": [{"runs": 5}]}
        d["project"]["display"] = {"headline": {"group": "zz", "metric": "acc"}}
        checks = P.check_project(d)
        self.assertIn({"where": "project.json", "what": "display.headline 指向的「zz / acc」在运行记录里找不到"}, checks)


class FormatV2(FixtureCase):
    """Record format v2: 一页纸, tasks/, decisions/, minutes, terms, numbered metric ids."""

    def checks(self, d=None):
        return {(c["where"], c["what"]) for c in (d or self.demo())["checks"]}

    def test_onepager_parsed_and_checked(self):
        d = self.demo()
        a = {x["file"]: x for x in d["attempts"]}
        op = a["attempts/2026-11-10-lr-sweep.md"]["onepager"]
        self.assertEqual(list(op), ["想回答", "怎么做", "预期", "结果", "所以"])
        self.assertEqual(op["所以"]["text"], "预热有用，并入主线。")
        checks = self.checks(d)
        self.assertFalse([c for c in checks if c[0] == "attempts/2026-11-10-lr-sweep.md" and "一页纸" in c[1]])
        self.assertIn(("attempts/2026-11-15-focal.md", "没有「一页纸」一节（想回答 / 怎么做 / 预期 / 结果 / 所以，每项一两句白话）"), checks)

    def test_onepager_rules(self):
        P_ = P.parse_onepager
        op = P_("- **想回答**：问题？\n- 怎么做: 做法\n  续行说明\n| 名次 | A |\n|---|---|\n| 1 | x |\n- 预期：好\n")
        self.assertEqual((op["想回答"]["text"], op["怎么做"]["text"]), ("问题？", "做法"))
        self.assertIn("| 1 | x |", op["怎么做"]["extra"])
        done = {"status": "成功"}
        self.assertEqual(P.onepager_problems(done, op, True), ["「一页纸」缺「结果」", "「一页纸」缺「所以」"])
        self.assertEqual(P.onepager_problems({"status": "进行中"}, op, True), [])
        long = P_("- 想回答：" + "长" * 151 + "\n- 怎么做：x\n- 预期：y")
        self.assertTrue(any("太长（151 字" in m for m in P.onepager_problems({}, long, True)))
        table = P_("- 想回答：q\n- 怎么做：h\n- 预期：e\n" + "\n".join("| %d |" % i for i in range(13)))
        self.assertTrue(any("表格太长" in m for m in P.onepager_problems({}, table, True)))
        # a planned attempt needs no example yet, but still its first three lines
        self.assertEqual(P.onepager_problems({"status": "计划中"}, {}, True), ["「一页纸」缺「想回答」", "「一页纸」缺「怎么做」", "「一页纸」缺「预期」"])

    def test_tasks(self):
        d = self.demo()
        t = {x["meta"].get("id"): x for x in d["tasks"]}
        self.assertEqual(t["f1-metric"]["meta"]["scope"], ["src/eval/"])
        self.assertIn("重算 r002", t["f1-metric"]["sections"]["交接"])
        checks = self.checks(d)
        self.assertFalse([c for c in checks if c[0] in ("tasks/f1-metric.md", "tasks/report-r1.md")])
        for want in ["文件名要和 id 一致：tasks/other-name.md", "kind「跳舞」不认识（讨论 / 执行 / 写作 / 分析 / 审查 / 调试）",
                     "status「搞定」不认识（待开始 / 进行中 / 等运行 / 等你 / 可以继续 / 完成 / 暂停 / 放弃）",
                     "after 里的任务「ghost」不存在", "about 「a:nope」指向的记录不存在", "没有「要做什么」一节"]:
            self.assertIn(("tasks/broken-task.md", want), checks)
        board = os.path.join(self.root, "demo", "board")
        with open(os.path.join(board, "tasks", "f1-metric.md")) as f:
            text = f.read()
        with open(os.path.join(board, "tasks", "f1-metric.md"), "w") as f:
            f.write(text.replace("status: 进行中", "status: 完成").split("## 交接")[0])
        self.assertIn(("tasks/f1-metric.md", "状态是「完成」，但「交接」是空的（做了什么 / 证据在哪 / 没做完的 / 下一步 / 要你决定的）"), self.checks())

    def test_decisions_and_answers(self):
        d = self.demo()
        self.assertEqual(P.decision_states(d["decisions"], {}),
                         {"broken-dec": "候选", "old-plan": "已替代", "scale-later": "候选", "warmup-first": "采纳"})
        # the user's click in the console counts at once, before any session processes the reply
        answers = {"d:scale-later": {"id": "d:scale-later", "answer": "采纳"}}
        self.assertEqual(P.decision_states(d["decisions"], answers)["scale-later"], "采纳")
        self.assertEqual(P.decision_states(d["decisions"], {"d:scale-later": {"answer": "随便"}})["scale-later"], "候选")
        checks = self.checks(d)
        for want in [("decisions/broken-dec.md", "status「拍板」不认识（候选 / 采纳 / 不采纳 / 已替代）"),
                     ("decisions/broken-dec.md", "没有「决定」一节（一句话写定了什么）"),
                     ("decisions/broken-dec.md", "replaces 里的决定「ghost」不存在"),
                     ("docs/2026-11-20-discussion.md", "decisions 里的决定「ghost-dec」不存在")]:
            self.assertIn(want, checks)
        self.assertFalse([c for c in checks if c[0] in ("decisions/warmup-first.md", "decisions/old-plan.md", "decisions/scale-later.md")])

    def test_two_adopted_decisions_cannot_replace_the_same_one(self):
        board = os.path.join(self.root, "demo", "board")
        with open(os.path.join(board, "decisions", "again.md"), "w") as f:
            f.write("---\nid: again\nstatus: 采纳\nreplaces: [old-plan]\n---\n## 决定\nx\n")
        msgs = [c[1] for c in self.checks() if c[0] in ("decisions/again.md", "decisions/warmup-first.md")]
        self.assertEqual(len([m for m in msgs if "已经被" in m and "不能再被替代一次" in m]), 1)

    def test_terms_doc_kinds_and_numbered_metrics(self):
        board = os.path.join(self.root, "demo", "board")
        os.makedirs(os.path.join(board, "runs", "r006"))
        with open(os.path.join(board, "runs", "r006", "run.json"), "w") as f:
            f.write('{"id": "r006", "metrics": {"测试集": {"acc_step1": 0.5, "acc_step12": 0.6, "acc_stepx": 1}}}')
        with open(os.path.join(board, "docs", "odd.md"), "w") as f:
            f.write("---\nkind: 随笔\nabout: z:1\n---\n正文\n")
        checks = self.checks()
        self.assertIn(("setup.json", "指标「acc_stepx」（1 次运行用到）没有在 metrics 里定义"), checks)
        self.assertFalse([c for c in checks if "acc_step1" in c[1]])          # acc_step{n} covers numbered steps
        self.assertIn(("setup.json", "terms 里每项要有 term（术语）和 about（一句白话解释）"), checks)
        self.assertIn(("docs/odd.md", "kind「随笔」不认识（方案 / 观察 / 复盘 / 报告 / 说明 / 纪要）"), checks)
        self.assertIn(("docs/odd.md", "about 「z:1」不是 v: / t: / a: / m: / k: / d: 加 id 的写法"), checks)
        self.assertIn(("docs/hparams.md", "没有 kind（方案 / 观察 / 复盘 / 报告 / 说明 / 纪要）"), checks)
        self.assertFalse([c for c in checks if c[0] == "NOTES.md"])           # root notes need no front matter


class Figures(FixtureCase):
    """figures/*.html (图解): only the title and two meta tags at the top are read, and checked."""

    def test_parsed(self):
        d = self.demo()
        self.assertEqual([f["file"] for f in d["figures"]], ["figures/lr-warmup.html", "figures/untitled.html"])  # not hidden, nested or .txt
        self.assertEqual(d["figures"][0]["meta"], {"title": "预热 & 不预热：开头 1000 步的训练损失",        # entities decoded
                                                   "about": "a:2026-11-10-lr-sweep", "question": "加 500 步预热，训练开头的损失尖峰还在不在？"})
        self.assertIsInstance(d["figures"][0]["size"], int)
        self.assertEqual(d["figures"][1]["meta"], {"about": "a:ghost"})          # an <svg><title> in the body is not the page's title
        self.assertIn("figures", d["updated"])
        self.assertNotIn("postMessage", repr(d))                                  # the page itself is never read into the scan
        self.assertEqual(P.empty_project("x")["figures"], [])

    def test_checks(self):
        figs = os.path.join(self.root, "demo", "board", "figures")
        with open(os.path.join(figs, "big.html"), "wb") as f:
            f.write(b"<title>big</title>")
            f.truncate(11 * 1024 * 1024)  # sparse, over the 10 MB limit
        with open(os.path.join(figs, "late.html"), "w") as f:
            f.write("<style>" + "x" * 9000 + "</style><title>too late</title>")
        checks = {(c["where"], c["what"]) for c in self.demo()["checks"]}
        no_title = "开头 8 KB 里没有 <title>：写在文件最前面，控制台用它作标题"
        for want in [("figures/untitled.html", no_title), ("figures/untitled.html", "about 「a:ghost」指向的记录不存在"),
                     ("figures/late.html", no_title), ("figures/big.html", "文件有 11.0 MB，超过 10 MB，控制台不显示：数据抽样或少画几张再放进来")]:
            self.assertIn(want, checks)
        self.assertFalse([c for c in checks if c[0] == "figures/lr-warmup.html"])

    def test_links_out_of_figures_are_not_listed(self):
        figs = os.path.join(self.root, "demo", "board", "figures")
        os.symlink("../legacy-site/index.html", os.path.join(figs, "via-link.html"))   # inside board/, but not a figure
        d = self.demo()
        self.assertNotIn("figures/via-link.html", [f["file"] for f in d["figures"]])   # /files/ would refuse it too
        self.assertIn({"file": "figures/via-link.html", "error": "指向 figures/ 之外的链接，控制台不显示"}, d["errors"])


class Papers(FixtureCase):
    """board/papers.jsonl: the project's reading list, checked; 读过 comes from marks.jsonl."""

    def append(self, project, which, rows):
        with open(os.path.join(self.root, project, "board", which), "a", encoding="utf-8") as f:
            f.write("".join((r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)) + "\n" for r in rows))

    def test_scanned_and_counted(self):
        d = self.demo()
        self.assertEqual([p["id"] for p in d["papers"]], ["goyal-large-batch", "radam", "focal-loss", "mixup", "f-score", "sgdr"])
        self.assertEqual(d["papers"][1]["key"], "arxiv:1908.03265")
        self.assertEqual(d["papers"][4]["key"], "doi:10.1007/978-3-540-31865-1_25")
        self.assertEqual(set(d["paper_marks"]), {"focal-loss"})            # goyal's mark was cleared
        self.assertEqual(d["marks"], {})                                     # paper rows are not document marks
        self.assertEqual(P.paper_counts(d), {"n": 6, "read": 1, "must_unread": 2})
        self.assertEqual(P.summary(d)["papers"], {"n": 6, "read": 1, "must_unread": 2})
        self.assertIn("papers", d["updated"])
        self.assertFalse([c for c in d["checks"] if c["where"].startswith("papers.jsonl")])
        self.append("demo", "marks.jsonl", [{"paper": "radam", "mark": "读过"},
                                            {"paper": "old-survey", "mark": "读过"}])   # dropped from the list: not counted
        self.assertEqual(P.paper_counts(self.demo()), {"n": 6, "read": 2, "must_unread": 1})
        e = P.empty_project("x")
        self.assertEqual((e["papers"], e["paper_marks"], P.paper_counts(e)), ([], {}, {"n": 0, "read": 0, "must_unread": 0}))

    def test_checks(self):
        self.append("demo", "papers.jsonl", [
            {"id": "no-why", "title": "T", "url": "https://x.org/1"},
            {"id": "js", "title": "T", "url": "javascript:alert(1)", "why": "w"},
            {"id": "no-url", "title": "T", "why": "w"},
            {"id": "pri", "title": "T", "url": "https://x.org/2", "why": "w", "priority": "重要"},
            {"id": "ghost", "title": "T", "url": "https://x.org/3", "why": "w", "about": "a:nope"},
            {"id": "a b/c", "title": "T", "url": "https://x.org/4", "why": "w"},
            {"id": "no-title", "url": "https://x.org/5", "why": " "},
            "not json"])
        d = self.demo()
        got = {(c["where"], c["what"]) for c in d["checks"] if c["where"].startswith("papers.jsonl")}
        self.assertEqual(got, {
            ("papers.jsonl · no-why", "缺 why：一句话写为什么读、和本项目什么关系"),
            ("papers.jsonl · js", "url 要是 http(s) 链接：写 arXiv 摘要页、DOI 或论文主页"),
            ("papers.jsonl · no-url", "缺 url：写 arXiv 摘要页、DOI 或论文主页"),
            ("papers.jsonl · pri", "priority「重要」不认识（必读 / 选读）"),
            ("papers.jsonl · ghost", "about 「a:nope」指向的记录不存在"),
            ("papers.jsonl · a b/c", "id「a b/c」只能用英文字母、数字和 . _ -（会出现在控制台的链接里）"),
            ("papers.jsonl · no-title", "缺 title"),
            ("papers.jsonl · no-title", "缺 why：一句话写为什么读、和本项目什么关系")})   # no `checked` is not a problem
        self.assertIn({"file": "papers.jsonl", "error": "1 行无法解析或缺少 id"}, d["errors"])

    def test_read_elsewhere(self):
        self.append("minimal", "papers.jsonl", [
            {"id": "liu-radam", "title": "RAdam", "url": "http://arxiv.org/pdf/1908.03265.pdf", "why": "w"},   # demo's radam
            {"id": "focal", "title": "Focal", "url": "https://arxiv.org/abs/1708.02002v3", "why": "w"},          # demo's focal-loss
            {"id": "other", "title": "O", "url": "https://x.org/o", "why": "w"}])
        self.append("minimal", "marks.jsonl", [{"paper": "liu-radam", "mark": "读过"}, {"paper": "other", "mark": "读过"}])
        snap = P.scan_root(self.root)
        self.assertEqual(P.papers_read_elsewhere(snap, "demo"), {"radam": ["minimal"]})
        self.assertEqual(P.papers_read_elsewhere(snap, "minimal"), {"focal": ["demo"]})   # its own marks are not listed
        self.assertEqual(P.papers_read_elsewhere(snap, "broken"), {})
        self.assertEqual(P.papers_read_elsewhere(snap, "nope"), {})
        self.append("minimal", "marks.jsonl", [{"paper": "liu-radam", "mark": None}])
        self.assertEqual(P.papers_read_elsewhere(P.scan_root(self.root), "demo"), {})


class SafeFile(FixtureCase):
    def test_allowed(self):
        p = P.safe_board_file(self.root, "demo", "runs/r001/curve.svg")
        self.assertEqual(p, os.path.realpath(os.path.join(self.root, "demo/board/runs/r001/curve.svg")))
        self.assertTrue(P.safe_board_file(self.root, "demo", "attempts/2026-11-15-focal.md"))
        self.assertTrue(P.safe_board_file(self.root, "demo", "figures/lr-warmup.html"))

    def test_rejected(self):
        bad = [("../x", "plan.md"), (".hidden", "project.json"), ("_shared", "project.json"),
               ("a/b", "plan.md"), ("", "plan.md"),
               ("demo", "../project.json"), ("demo", "runs/../../../minimal/board/project.json"),
               ("demo", "/etc/passwd"), ("demo", ""), ("demo", "runs//r001/curve.svg"),
               ("demo", "runs/./r001/curve.svg"), ("demo", "legacy-site/index.html"),
               ("demo", "runs/r001/leak.png"), ("demo", "attempts/notes.txt"),
               ("demo", "a\\b.md"), ("demo", "x\0.md"), ("demo", ".secret.md"),
               ("demo", "figures/sub/nested.html"), ("demo", "figures/.draft.html"), ("demo", "figures/readme.txt"),
               ("demo", "examples/page.html")]
        for name, rel in bad:
            with self.subTest(name=name, rel=rel):
                self.assertIsNone(P.safe_board_file(self.root, name, rel))

    def test_valid_name(self):
        for ok in ["demo", "jev-authz", "a.b_c-1"]:
            self.assertTrue(P.valid_name(ok))
        for no in ["", "_x", ".x", "a/b", "..", "x" * 65, "中文", "a b"]:
            self.assertFalse(P.valid_name(no))


if __name__ == "__main__":
    unittest.main()
