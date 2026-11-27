"""Run states and the reconcile pass (boardtool.py). Run: python3 -m unittest -v"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "skill", "research-board", "scripts"))
import boardtool as T  # noqa: E402
import projects as P  # noqa: E402

NOW = 1_800_000_000.0


class RunState(unittest.TestCase):
    """Agreed case 3: a fresh heartbeat wins; a dead run needs a probe saying the process is gone."""

    def test_states(self):
        running = {"status": "运行中"}
        self.assertEqual(P.run_state({"status": "完成"}, None, None, NOW), "已结束")
        self.assertEqual(P.run_state(running, NOW - 30, "absent", NOW), "运行中")       # fresh heartbeat, GPU poll blind
        self.assertEqual(P.run_state(running, NOW - 900, "absent", NOW), "已中断")      # stale, server says gone
        self.assertEqual(P.run_state(running, NOW - 900, "unreachable", NOW), "状态未知")
        self.assertEqual(P.run_state(running, NOW - 900, None, NOW), "状态未知")         # stale alone is not enough
        self.assertEqual(P.run_state(running, NOW - 900, "running", NOW), "运行中")
        self.assertEqual(P.run_state(running, None, "running", NOW), "运行中")           # old runs: as before
        self.assertEqual(P.run_state(running, None, "absent", NOW), "状态未知")          # may have finished without writing it
        self.assertEqual(P.run_state(running, None, None, NOW), "状态未知")


class FailedRuns(unittest.TestCase):
    """Gate 4 (2026-11-27): a failed or interrupted run whose attempt was not updated since gets a to-do."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "root")
        self.board = os.path.join(self.root, "proj", "board")
        os.makedirs(os.path.join(self.board, "attempts"))
        with open(os.path.join(self.board, "project.json"), "w") as f:
            json.dump({"title": "p"}, f)
        self.attempt("a1", "给监控哪些信息", ["f1"], NOW - 4 * 3600)
        self.attempt("a2", "两阶段能否省钱", ["g1"], NOW - 100)               # updated after its run failed
        self.attempt("a3", "旧实验", ["h1"], NOW - 9 * 3600)
        self.mkrun("f1", "失败", NOW - 3600, error="CUDA out of memory\n  at step 120")
        self.mkrun("f2", "被中断", NOW - 2400, attempt="a1")                   # names the attempt itself
        self.mkrun("f3", "失败", NOW - 600, attempt="a1")                     # ended 10 minutes ago: still in grace
        self.mkrun("ok", "完成", NOW - 300, attempt="a1")
        self.mkrun("g1", "失败", NOW - 7200)
        self.mkrun("h1", "失败", NOW - 6 * 3600)                              # before the rule was switched on
        self.state = os.path.join(self.tmp, "state", "reconcile.json")
        os.makedirs(os.path.dirname(self.state))
        with open(self.state, "w") as f:
            json.dump({"dead": {}, "failed_since": NOW - 5 * 3600}, f)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def attempt(self, aid, title, runs, mtime):
        path = os.path.join(self.board, "attempts", aid + ".md")
        with open(path, "w") as f:
            f.write("---\nid: %s\ntitle: %s\ntrack: null\nstatus: 进行中\nruns: [%s]\n---\n## 一页纸\n- 想回答：q\n" % (aid, title, ", ".join(runs)))
        os.utime(path, (mtime, mtime))

    def mkrun(self, rid, status, ended, **kw):
        d = os.path.join(self.board, "runs", rid)
        os.makedirs(d)
        with open(os.path.join(d, "run.json"), "w") as f:
            json.dump(dict(kw, id=rid, status=status, started=T.utc(ended - 600), ended=T.utc(ended)), f, ensure_ascii=False)

    def run_(self, now=NOW, dry_run=False):
        return T.reconcile(self.root, now, lambda recs: [None] * len(recs), self.state, dry_run=dry_run)

    def rows(self):
        path = os.path.join(self.board, "todos.jsonl")
        if not os.path.exists(path):
            return []
        with open(path) as f:
            return [json.loads(line) for line in f]

    def test_one_to_do_per_attempt_for_its_latest_failure(self):
        changes = self.run_()
        rows = self.rows()
        self.assertEqual([r["id"] for r in rows], ["run-failed-f2"])
        r = rows[0]
        self.assertEqual(r["text"], "实验「给监控哪些信息」的运行 f2 被中断了，实验记录还没记下这次结果")
        self.assertEqual((r["kind"], r["by"], r["link"], r["done"], r["options"], r["recommend"]),
                         ("whenever", "board reconcile", "roadmap/a:a1", None, ["记进尝试", "不用记"], "记进尝试"))
        self.assertIn("同一个实验还有 1 次失败或中断的运行也没记：f1。", r["context"])
        self.assertEqual(changes, [{"project": "proj", "file": "todos.jsonl", "what": "待办 run-failed-f2：" + r["text"]}])
        self.assertEqual(P.read_todos(os.path.join(self.board, "todos.jsonl"))["bad"], 0)    # the console reads it
        self.assertEqual(self.run_(), [])                                                    # idempotent
        self.assertEqual(len(self.rows()), 1)

    def test_the_error_is_quoted_short(self):
        os.remove(os.path.join(self.board, "runs", "f2", "run.json"))
        self.run_()
        self.assertIn("状态「失败」：CUDA out of memory at step 120。", self.rows()[0]["context"])

    def test_updating_the_attempt_closes_it(self):
        self.run_()
        path = os.path.join(self.board, "attempts", "a1.md")
        os.utime(path, (NOW + 60, NOW + 60))
        changes = self.run_(now=NOW + 120)
        last = self.rows()[-1]
        self.assertEqual((last["id"], last["done"], last["result"]), ("run-failed-f2", T.utc(NOW + 120), "实验记录在运行结束后更新了"))
        self.assertEqual(last["text"], self.rows()[0]["text"])                              # a closing row keeps its text
        self.assertEqual([c["what"] for c in changes], ["待办 run-failed-f2 标为完成：实验记录在运行结束后更新了"])
        self.assertEqual(self.run_(now=NOW + 180), [])

    def test_a_no_holds_until_the_next_failure(self):
        self.run_()
        with open(os.path.join(self.board, "todos.jsonl"), "a") as f:      # the session answered "不用记"
            f.write(json.dumps(dict(self.rows()[0], done=T.utc(NOW + 60), result="按回复不用记", by="board reconcile")) + "\n")
        self.assertEqual(self.run_(now=NOW + 3 * 3600), [])                # f1, f2 and f3 all ended before it was asked
        self.mkrun("f4", "失败", NOW + 3600, attempt="a1")
        self.assertEqual([c["what"].split("：")[0] for c in self.run_(now=NOW + 3 * 3600)], ["待办 run-failed-f4"])

    def test_the_first_pass_only_sets_the_start(self):
        os.remove(self.state)
        self.assertEqual(self.run_(), [])
        with open(self.state) as f:
            self.assertEqual(json.load(f)["failed_since"], NOW)
        self.assertEqual(self.run_(now=NOW + 60), [])                      # older failures stay untouched

    def test_dry_run_writes_nothing(self):
        self.assertEqual(len(self.run_(dry_run=True)), 1)
        self.assertEqual(self.rows(), [])


class Reconcile(unittest.TestCase):
    """Agreed case 4: two passes before writing, idempotent, waiting tasks move on."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "root")
        self.board = os.path.join(self.root, "proj", "board")
        os.makedirs(os.path.join(self.board, "tasks"))
        with open(os.path.join(self.board, "project.json"), "w") as f:
            json.dump({"title": "p"}, f)
        self.mkrun("dead", {"status": "运行中", "server": "gpu-h100", "pid": 11, "task": "train-9b"}, hb_age=900)
        self.mkrun("alive", {"status": "运行中", "server": "gpu-h100", "pid": 12, "task": "eval-9b"}, hb_age=20)
        self.mkrun("old", {"status": "运行中", "server": "gpu-h200", "tmux": "proj-old"})          # hand-written: no heartbeat
        self.mkrun("done", {"status": "完成", "server": "gpu-h100", "task": "train-9b"})
        self.task("train-9b", "等运行")
        self.task("eval-9b", "等运行")
        self.task("other", "进行中")
        self.state = os.path.join(self.tmp, "state", "reconcile.json")
        self.asked = []

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def mkrun(self, rid, rec, hb_age=None):
        d = os.path.join(self.board, "runs", rid)
        os.makedirs(d)
        with open(os.path.join(d, "run.json"), "w") as f:
            json.dump(dict(rec, id=rid), f, ensure_ascii=False)
        if hb_age is not None:
            hb = os.path.join(d, "heartbeat")
            open(hb, "w").close()
            os.utime(hb, (NOW - hb_age, NOW - hb_age))

    def task(self, tid, status):
        with open(os.path.join(self.board, "tasks", tid + ".md"), "w") as f:
            f.write("---\nid: %s\ntitle: 任务 %s\nkind: 执行\nstatus: %s  # 注释保留\n---\n## 要做什么\nx\n" % (tid, tid, status))

    def probe(self, recs):
        self.asked.extend(r["id"] for r in recs)
        return [{"dead": "absent", "old": "absent"}.get(r["id"], "running") for r in recs]

    def rec(self, rid):
        with open(os.path.join(self.board, "runs", rid, "run.json")) as f:
            return json.load(f)

    def meta(self, tid):
        with open(os.path.join(self.board, "tasks", tid + ".md")) as f:
            return P.parse_front_matter(f.read())[0]

    def test_two_passes_then_idempotent(self):
        first = T.reconcile(self.root, NOW, self.probe, self.state)
        self.assertEqual(first, [])                                          # one look is not enough
        self.assertEqual(self.rec("dead")["status"], "运行中")
        self.assertNotIn("alive", self.asked)                                # a fresh heartbeat needs no ssh
        second = T.reconcile(self.root, NOW + 300, self.probe, self.state)
        self.assertEqual({(c["file"], c["what"].split("：")[0]) for c in second},
                         {("runs/dead/run.json", "标为被中断"), ("tasks/train-9b.md", "等运行 → 可以继续（2 次运行都结束了）")})
        self.assertEqual(self.rec("old")["status"], "运行中")               # a hand-written record is never closed by the program
        dead = self.rec("dead")
        self.assertEqual((dead["status"], dead["closed_by"], dead["task"]), ("被中断", "board reconcile", "train-9b"))
        self.assertIn("心跳停在", dead["error"])
        self.assertEqual(self.meta("train-9b")["status"], "可以继续")
        self.assertEqual(self.meta("eval-9b")["status"], "等运行")          # its run is alive
        self.assertEqual(self.meta("other")["status"], "进行中")            # other states untouched
        with open(os.path.join(self.board, "tasks", "train-9b.md")) as f:
            self.assertIn("title: 任务 train-9b\n", f.read())                   # other lines kept as they were
        self.assertEqual(T.reconcile(self.root, NOW + 600, self.probe, self.state), [])   # nothing left to do

    def test_a_run_that_comes_back_resets_the_count(self):
        T.reconcile(self.root, NOW, self.probe, self.state)
        T.reconcile(self.root, NOW + 300, lambda recs: ["unreachable"] * len(recs), self.state)   # server down: not dead
        self.assertEqual(T.reconcile(self.root, NOW + 600, self.probe, self.state), [])
        self.assertEqual(self.rec("dead")["status"], "运行中")

    def test_dry_run_changes_nothing(self):
        T.reconcile(self.root, NOW, self.probe, self.state)
        changes = T.reconcile(self.root, NOW + 300, self.probe, self.state, dry_run=True)
        self.assertEqual(len(changes), 2)
        self.assertEqual(self.rec("dead")["status"], "运行中")
        self.assertEqual(self.meta("train-9b")["status"], "等运行")


class Helpers(unittest.TestCase):
    def test_set_front_matter(self):
        text = "---\nid: a\nstatus: 等运行\n---\n## 要做什么\nstatus: 不是字段\n"
        out = T.set_front_matter(text, {"status": "可以继续", "updated": "2026-11-24T00:00:00Z"})
        self.assertEqual(out, "---\nid: a\nstatus: 可以继续\nupdated: 2026-11-24T00:00:00Z\n---\n## 要做什么\nstatus: 不是字段\n")
        with self.assertRaises(ValueError):
            T.set_front_matter("no front matter", {"a": "b"})

    def test_probe_one_connection_per_server(self):
        calls = []

        def fake(cmd):
            calls.append(cmd)
            if "down.example" in cmd:
                return None
            # pretend r1 (pid 6) is gone and every other run is alive
            return "\n".join("%s %s" % (k, "gone" if k == "r1" else "alive")
                             for k in __import__("re").findall(r"echo '(r\d+) alive'", cmd[-1]))
        probe = T.make_probe({"gpu-h100": "h100.example", "gpu-h200": "down.example"}, run_cmd=fake, here="gpu-a100")
        got = probe([{"server": "gpu-a100", "pid": 5}, {"server": "gpu-h100", "pid": 6}, {"server": "gpu-h100", "tmux": "jev-x"},
                     {"server": "gpu-h200", "pid": 7}, {"server": "gpu-h100"}, {"server": "gpu-h100", "tmux": "bad name; rm -rf /"},
                     {"server": "elsewhere", "pid": 3}])
        self.assertEqual(got, ["running", "absent", "running", "unreachable", None, None, None])
        self.assertEqual(sorted(c[0] if c[0] == "bash" else c[-2] for c in calls), ["bash", "down.example", "h100.example"])
        h100 = next(c for c in calls if "h100.example" in c)[-1]
        self.assertIn("tmux has-session -t '=jev-x'", h100)                      # both h100 runs in one connection
        self.assertIn("-p 6 ", h100)
        self.assertNotIn("rm -rf", "".join(c[-1] for c in calls))                # odd names never reach a shell


if __name__ == "__main__":
    unittest.main()
