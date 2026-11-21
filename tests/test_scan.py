"""Scanner, cache and file-lookup tests for projects.py. Run: python3 -m unittest -v"""
import errno
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
        self.assertEqual(set(d["updated"]), {"project", "plan", "versions", "tracks", "attempts", "runs", "examples", "setup", "todos", "docs"})

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
        self.assertEqual([x["file"] for x in d["docs"]], ["docs/hparams.md", "NOTES.md", "REPORT.md"])
        self.assertIn("进度笔记", d["docs"][1]["body"])
        self.assertIn("setup", d["updated"])

    def test_root_doc_symlink_out_is_ignored(self):
        os.remove(os.path.join(self.root, "demo", "NOTES.md"))
        os.symlink(os.path.join(self.tmp, "secret.md"), os.path.join(self.root, "demo", "NOTES.md"))
        d = self.demo()
        self.assertEqual([x["file"] for x in d["docs"]], ["docs/hparams.md", "REPORT.md"])
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


class SafeFile(FixtureCase):
    def test_allowed(self):
        p = P.safe_board_file(self.root, "demo", "runs/r001/curve.svg")
        self.assertEqual(p, os.path.realpath(os.path.join(self.root, "demo/board/runs/r001/curve.svg")))
        self.assertTrue(P.safe_board_file(self.root, "demo", "attempts/2026-11-15-focal.md"))

    def test_rejected(self):
        bad = [("../x", "plan.md"), (".hidden", "project.json"), ("_shared", "project.json"),
               ("a/b", "plan.md"), ("", "plan.md"),
               ("demo", "../project.json"), ("demo", "runs/../../../minimal/board/project.json"),
               ("demo", "/etc/passwd"), ("demo", ""), ("demo", "runs//r001/curve.svg"),
               ("demo", "runs/./r001/curve.svg"), ("demo", "legacy-site/index.html"),
               ("demo", "runs/r001/leak.png"), ("demo", "attempts/notes.txt"),
               ("demo", "a\\b.md"), ("demo", "x\0.md"), ("demo", ".secret.md")]
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
