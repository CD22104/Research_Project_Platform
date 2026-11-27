"""The run-record module (boardrun/): the seven key cases agreed with the user. Run: python3 -m unittest -v"""
import contextlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import warnings
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SCRIPTS = os.path.join(REPO, "skill", "research-board", "scripts")
sys.path.insert(0, os.path.join(SCRIPTS, "boardrun"))
sys.path.insert(0, SCRIPTS)
import boardrun   # noqa: E402
import projects   # noqa: E402


def make_project(root):
    """A minimal project whose setup.json defines the group and metrics the tests write."""
    b = os.path.join(root, "board")
    os.makedirs(os.path.join(b, "attempts"))
    with open(os.path.join(b, "project.json"), "w") as f:
        json.dump({"title": "测试项目", "status": "进行中"}, f)
    with open(os.path.join(b, "setup.json"), "w") as f:
        json.dump({"goal": "g", "datasets": [{"id": "test_iid", "name": "同分布测试集", "role": "test"}],
                   "metrics": [{"id": "accuracy", "name": "准确率", "better": "higher"},
                               {"id": "n", "name": "条数", "better": None}]}, f)
    with open(os.path.join(b, "attempts", "2026-11-22-a1.md"), "w") as f:
        f.write("---\nid: 2026-11-22-a1\ntrack: null\ndate: 2026-11-22\nstatus: 进行中\nruns: []\n---\n\n## 假设\nh\n\n## 预期\ne\n")
    os.makedirs(os.path.join(root, "src"))
    for name, text in [("src/train.py", "print('train')\n"), ("NOTES.md", "# 进度笔记\n"), ("CLAUDE.md", "# 规则\n")]:
        with open(os.path.join(root, name), "w") as f:
            f.write(text)
    git(root, "init", "-q")
    with open(os.path.join(root, ".gitignore"), "w") as f:
        f.write("board/runs/\nresults/\n")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "x")
    return root


def make_project_nogit(root):
    b = os.path.join(root, "board")
    os.makedirs(b)
    with open(os.path.join(b, "project.json"), "w") as f:
        json.dump({"title": "没有提交的项目"}, f)
    return root


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True)


def record(run_dir):
    with open(os.path.join(run_dir, "run.json"), encoding="utf-8") as f:
        return json.load(f)


class Boardrun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.proj = make_project(os.path.join(self.tmp, "proj"))
        self.env = mock.patch.dict(os.environ, {"CUDA_VISIBLE_DEVICES": "2,3"})
        self.env.start()
        os.environ.pop("TMUX", None)
        os.environ.pop("BOARD_PROJECT_DIR", None)

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp)

    def run_(self, **kw):
        return boardrun.Run(kw.pop("attempt", "2026-11-22-a1"), project_dir=self.proj, **kw)

    # 1 -----------------------------------------------------------------
    def test_start_writes_a_complete_record_atomically(self):
        r = self.run_(kind="train", config={"lr": 1e-4}, heartbeat_s=0)
        rec = record(r.dir)
        self.assertEqual(set(rec), {"id", "kind", "attempt", "status", "server", "gpus", "pid", "tmux", "started",
                                    "ended", "commit", "task", "resumed_from", "config", "metrics", "artifacts"})
        self.assertEqual((rec["status"], rec["kind"], rec["gpus"], rec["pid"]), ("运行中", "train", [2, 3], os.getpid()))
        self.assertEqual(rec["config"], {"lr": 1e-4})
        self.assertRegex(rec["commit"], r"^[0-9a-f]{12}$")
        self.assertEqual(os.listdir(r.dir), ["run.json"])                     # no temp file left behind
        with open(os.path.join(self.proj, "tracked.txt"), "w") as f:
            f.write("x")
        subprocess.run(["git", "add", "tracked.txt"], cwd=self.proj, check=True)
        self.assertTrue(record(self.run_(allow_dirty=True).dir)["commit"].endswith("-dirty"))  # uncommitted changes are visible
        with mock.patch.dict(os.environ, {"BOARD_PROJECT_DIR": self.proj}):  # found without project_dir
            self.assertEqual(str(boardrun.Run("2026-11-22-a1", allow_dirty=True).project), os.path.realpath(self.proj))

    # 2 -----------------------------------------------------------------
    def test_status_on_normal_exit_error_and_interrupt(self):
        with self.run_() as r:
            pass
        self.assertEqual(record(r.dir)["status"], "完成")
        self.assertIsNotNone(record(r.dir)["ended"])
        with self.assertRaises(ValueError):                                   # the error still reaches the caller
            with self.run_() as r:
                raise ValueError("bad batch")
        self.assertEqual(record(r.dir)["status"], "失败")
        self.assertIn("ValueError: bad batch", record(r.dir)["error"])
        with self.assertRaises(KeyboardInterrupt):
            with self.run_() as r:
                raise KeyboardInterrupt
        self.assertEqual(record(r.dir)["status"], "被中断")
        # SIGTERM (e.g. tmux kill-session, kill) in a real process
        code = ("import sys, time; sys.path.insert(0, %r); import boardrun\n"
                "with boardrun.Run('2026-11-22-a1', run_id='term', project_dir=%r):\n"
                "    print('ready', flush=True); time.sleep(30)\n") % (os.path.join(SCRIPTS, "boardrun"), self.proj)
        p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
        self.assertEqual(p.stdout.readline().strip(), "ready")
        p.send_signal(signal.SIGTERM)
        self.assertEqual(p.wait(10), -signal.SIGTERM)                       # still dies the way SIGTERM does
        p.stdout.close()
        rec = record(os.path.join(self.proj, "board", "runs", "term"))
        self.assertEqual((rec["status"], rec["error"]), ("被中断", "SIGTERM"))

    # 3 -----------------------------------------------------------------
    def test_results_merge_per_group_and_must_be_numbers(self):
        r = self.run_()
        r.result("test_iid", accuracy=0.5)
        r.result("test_iid", accuracy=0.6, n=10)
        self.assertEqual(record(r.dir)["metrics"], {"test_iid": {"accuracy": 0.6, "n": 10}})
        for bad in ["0.7", True, None]:
            with self.subTest(bad=bad), self.assertRaises(TypeError):
                r.result("test_iid", accuracy=bad)
        with self.assertRaises(TypeError):
            r.log(1, loss="nan?")
        self.assertEqual(record(r.dir)["metrics"], {"test_iid": {"accuracy": 0.6, "n": 10}})  # unchanged

    # 4 -----------------------------------------------------------------
    def test_resume_gets_a_new_id_and_leaves_the_old_record(self):
        with self.run_(run_id="r1") as r1:
            r1.result("test_iid", accuracy=0.4)
        before = record(r1.dir)
        with self.run_(resumed_from="r1") as r2:
            pass
        self.assertNotEqual(r2.id, "r1")
        self.assertEqual(record(r2.dir)["resumed_from"], "r1")
        self.assertEqual(record(r1.dir), before)
        with self.assertRaises(FileExistsError):                              # never overwrite a record
            self.run_(run_id="r1")
        with self.assertRaises(ValueError):
            self.run_(run_id="../x")

    # 5 -----------------------------------------------------------------
    def test_disk_errors_do_not_stop_the_run(self):
        r = self.run_()
        real_open = open

        def flaky(path, *a, **k):
            if str(path).startswith(str(r.dir)):
                raise OSError(5, "Input/output error")
            return real_open(path, *a, **k)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            with mock.patch("builtins.open", flaky):
                r.log(1, loss=0.9)
                r.log(2, loss=0.8)
                r.result("test_iid", accuracy=0.7)                            # no exception reaches the training loop
            self.assertTrue(any("will retry" in str(x.message) for x in w))
        r.log(3, loss=0.7)                                                    # disk back: everything kept is written
        with open(os.path.join(r.dir, "metrics.jsonl")) as f:
            self.assertEqual([json.loads(l)["step"] for l in f], [1, 2, 3])
        self.assertEqual(record(r.dir)["metrics"], {"test_iid": {"accuracy": 0.7}})
        # still failing at the very end: the record is kept locally and the path is reported
        fb = os.path.join(self.tmp, "fallback")
        with mock.patch.object(boardrun, "FALLBACK_DIR", boardrun.pathlib.Path(fb)), \
                mock.patch.object(boardrun.time, "sleep"), mock.patch("builtins.open", flaky), \
                warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            r.finish()
        with open(os.path.join(fb, r.id + ".json")) as f:
            self.assertEqual(json.load(f)["record"]["status"], "完成")
        self.assertTrue(any(fb in str(x.message) for x in w))

    # 6 -----------------------------------------------------------------
    def test_the_console_accepts_what_it_writes(self):
        with self.run_(kind="eval") as r:
            for step in range(3):
                r.log(step, loss=1.0 / (step + 1))
            r.result("test_iid", accuracy=0.95, n=602)
            for i in range(250):
                r.sample(id=i, output="allow")
            png = os.path.join(self.tmp, "curve.png")
            with open(png, "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\n")
            r.artifact(png)
            big = os.path.join(r.output_dir(), "preds.parquet")
            open(big, "w").close()
            r.artifact(big)
        self.assertEqual(r.n_samples, boardrun.MAX_SAMPLES)
        self.assertEqual(record(r.dir)["artifacts"], {"curve.png": "curve.png", "preds.parquet": big})
        self.assertTrue(os.path.isdir(os.path.join(self.proj, "results", r.id)))
        d = projects.scan_project(os.path.join(self.proj, "board"))
        self.assertEqual(d["errors"], [])
        self.assertEqual([c for c in d["checks"] if r.id in json.dumps(c, ensure_ascii=False)], [])
        run = next(x for x in d["runs"] if x["run"]["id"] == r.id)
        self.assertEqual(run["run"]["status"], "完成")
        self.assertTrue(run.get("series"))                                      # the curve is read too

    # 7 -----------------------------------------------------------------
    def test_command_line_wrapper_uses_the_exit_code(self):
        def cli(*cmd, rid):
            p = subprocess.run([sys.executable, "-m", "boardrun", "run", "--attempt", "2026-11-22-a1", "--kind", "data",
                                "--id", rid, "--project", self.proj, "--", *cmd],
                               cwd=os.path.join(SCRIPTS, "boardrun"), capture_output=True, text=True)
            return p.returncode, record(os.path.join(self.proj, "board", "runs", rid))
        code, rec = cli(sys.executable, "-c", "import os; print(os.environ['BOARDRUN_ID'])", rid="ok")
        self.assertEqual((code, rec["status"], rec["kind"]), (0, "完成", "data"))
        code, rec = cli(sys.executable, "-c", "import sys; sys.exit(3)", rid="bad")
        self.assertEqual((code, rec["status"], rec["error"]), (3, "失败", "exit code 3"))
        code, rec = cli(sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGTERM)", rid="killed")
        self.assertEqual(rec["status"], "被中断")

    # 8 ----------------------------------------------------------------- heartbeat and exit receipt
    def test_heartbeat_advances_and_cli_records_exit_code(self):
        r = self.run_(heartbeat_s=0.2)
        hb = os.path.join(r.dir, "heartbeat")
        t0 = os.stat(hb).st_mtime_ns
        time.sleep(0.6)
        self.assertGreater(os.stat(hb).st_mtime_ns, t0)                    # touched while running
        r.finish()
        t1 = os.stat(hb).st_mtime_ns
        time.sleep(0.5)
        self.assertEqual(os.stat(hb).st_mtime_ns, t1)                     # and no longer after the end
        from boardrun.__main__ import main
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["run", "--attempt", "2026-11-22-a1", "--project", self.proj, "--id", "ok",
                                   "--task", "f1-metric", "--", sys.executable, "-c", "pass"]), 0)
            self.assertEqual(main(["run", "--attempt", "2026-11-22-a1", "--project", self.proj, "--id", "bad",
                                   "--", sys.executable, "-c", "raise SystemExit(4)"]), 4)
        ok, bad = record(os.path.join(self.proj, "board", "runs", "ok")), record(os.path.join(self.proj, "board", "runs", "bad"))
        self.assertEqual((ok["status"], ok["exit_code"], ok["task"]), ("完成", 0, "f1-metric"))
        self.assertEqual((bad["status"], bad["exit_code"], bad["error"]), ("失败", 4, "exit code 4"))

    # 9 ----------------------------------------------------------------- tied to a commit
    def test_records_changing_is_not_a_code_change(self):
        for name in ("board/project.json", "board/attempts/2026-11-22-a1.md", "NOTES.md", "CLAUDE.md"):
            with open(os.path.join(self.proj, name), "a") as f:     # sessions update these while runs go on
                f.write("\n")
        rec = record(self.run_(heartbeat_s=0).dir)
        self.assertFalse(rec["commit"].endswith("-dirty"), rec["commit"])

    def test_dirty_or_uncommitted_code_refuses_to_start(self):
        with open(os.path.join(self.proj, "src", "train.py"), "a") as f:
            f.write("\n")                                                  # a tracked code file changed
        before = sorted(os.listdir(os.path.join(self.proj, "board", "runs"))) if os.path.isdir(os.path.join(self.proj, "board", "runs")) else []
        with self.assertRaises(boardrun.DirtyCode):
            self.run_(run_id="refused")
        self.assertFalse(os.path.exists(os.path.join(self.proj, "board", "runs", "refused")))   # no run dir left
        from boardrun.__main__ import main
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main(["run", "--attempt", "2026-11-22-a1", "--project", self.proj, "--", "true"]), 3)
        self.assertIn("uncommitted changes", err.getvalue())
        after = sorted(os.listdir(os.path.join(self.proj, "board", "runs"))) if os.path.isdir(os.path.join(self.proj, "board", "runs")) else []
        self.assertEqual(before, after)
        rec = record(self.run_(allow_dirty=True, heartbeat_s=0).dir)        # debugging can opt out, and it shows
        self.assertTrue(rec["commit"].endswith("-dirty"))
        fresh = make_project_nogit(os.path.join(self.tmp, "nogit"))
        with self.assertRaises(boardrun.DirtyCode):
            boardrun.Run("2026-11-22-a1", project_dir=fresh)
        # a repository cloned inside the project is the code when the run starts from inside it
        inner = os.path.join(self.proj, "team")
        os.makedirs(inner)
        git(inner, "init", "-q")
        git(inner, "commit", "-q", "--allow-empty", "-m", "team")
        git(self.proj, "checkout", "-q", "--", "src/train.py")
        old = os.getcwd()
        os.chdir(inner)
        try:
            rec = record(self.run_(heartbeat_s=0).dir)
        finally:
            os.chdir(old)
        self.assertEqual(rec["code_dir"], os.path.realpath(inner))


CORE = "2026-11-27-core"
VARIANTS = [   # ways the 一页纸 and the front matter get written; boardrun must read them as projects.py does
    "---\nid: x\ncore: true\n---\n## 一页纸\n- 想回答：q\n- 预期：准确率高 1–3 个点（用户 11-27 说的）\n",
    "---\nid: x\ncore: true   # 用户 11-27 定的\nexpect_skip:  \n---\n## 一页纸\n- **预期**：用户没写（2026-11-25 凌晨明确说「都做，直接开始」）\n",
    "---\nid: x\ncore: 是\nexpect_skip: 用户 11-25 说直接开始\n---\n## 一页纸\n* 预期: **κ ≥ 0.8**\n",
    "---\nid: x\ncore: [true]\n---\n## 一页纸\n```\n## 不是标题\n- 预期：代码块里的不算\n```\n- 预期：\n  写在下一行的不算\n",
    "---\nid: x\n---\n## 假设\nh\n## 一页纸\n- 预期：第一处\n## 一页纸\n- 预期：第二处\n- 预期：第三行\n",
    "## 一页纸\n- 预期：没有字段区\n",
    "---\ncore: true\n## 一页纸\n- 预期：字段区没收尾\n",
]


class CoreExpectation(unittest.TestCase):
    """Gate 1 (agreed 2026-11-27): a core attempt's run starts only with the user's expectation or a skip."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.proj = make_project(os.path.join(self.tmp, "proj"))
        self.runs = os.path.join(self.proj, "board", "runs")
        os.environ.pop("TMUX", None)
        os.environ.pop("BOARD_PROJECT_DIR", None)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def attempt(self, front, expect):
        with open(os.path.join(self.proj, "board", "attempts", CORE + ".md"), "w", encoding="utf-8") as f:
            f.write("---\nid: %s\ntitle: 核心实验\ntrack: null\ndate: 2026-11-27\nstatus: 计划中\n%s---\n## 一页纸\n"
                    "- 想回答：预热能不能消掉尖峰？\n- 怎么做：加 500 步预热，和基线比。\n%s- 结果：还没出\n\n## 例子\nx\n"
                    % (CORE, "".join(x + "\n" for x in front), "- 预期：%s\n" % expect if expect is not None else ""))

    def start(self, kind="train", rid="r"):
        r = boardrun.Run(CORE, kind=kind, run_id=rid, project_dir=self.proj, heartbeat_s=0)
        r.finish()
        return r

    def refused(self, rid="refused", kind="train"):
        with self.assertRaises(boardrun.NoExpectation) as cm:
            boardrun.Run(CORE, kind=kind, run_id=rid, project_dir=self.proj, heartbeat_s=0)
        self.assertFalse(os.path.exists(os.path.join(self.runs, rid)))      # nothing written
        return str(cm.exception)

    def test_no_expectation_is_refused_with_the_steps(self):
        """T1.1"""
        for expect in (None, ""):
            self.attempt(["core: true"], expect)
            msg = self.refused()
            self.assertTrue(msg.startswith("boardrun 没有启动：尝试 %s 标了核心实验（core: true），但一页纸的「预期」还没写。" % CORE), msg)
            for step in ("1. 问用户：「你预计这次结果会怎样、为什么？」", "board/attempts/%s.md" % CORE,
                         "expect_skip: <用户的原话或理由，日期>", "不要替用户写预期，也不要去掉 core 绕过。"):
                self.assertIn(step, msg)

    def test_not_written_is_refused(self):
        """T1.2"""
        self.attempt(["core: true"], "用户没写（2026-11-25 凌晨明确说「都做，直接开始」）")
        self.assertIn("一页纸的「预期」写的是「用户没写」。", self.refused())

    def test_an_expectation_lets_it_start(self):
        """T1.3"""
        self.attempt(["core: true"], "尖峰消失，准确率高 1–3 个点（用户 11-27 说的）")
        self.assertEqual(record(self.start().dir)["status"], "完成")

    def test_a_skip_needs_a_reason(self):
        """T1.4"""
        self.attempt(["core: true", "expect_skip: "], "用户没写")
        self.assertIn("「预期」写的是「用户没写」，expect_skip 也是空的。", self.refused())
        self.attempt(["core: true", "expect_skip: 用户 2026-11-27 说这次是复现，不写预期"], "用户没写（复现）")
        self.start()

    def test_only_core_attempts_are_checked(self):
        """T1.5"""
        for i, front in enumerate(([], ["core: false"], ["core: null"], ["core:"])):
            self.attempt(front, None)
            self.start(rid="r%d" % i)

    def test_smoke_runs_and_runs_without_an_attempt(self):
        """T1.6"""
        self.attempt(["core: true"], None)
        self.start(kind="smoke")
        r = boardrun.Run(None, run_id="none", project_dir=self.proj, heartbeat_s=0)
        r.finish()
        r = boardrun.Run("2026-11-27-not-written-yet", run_id="missing", project_dir=self.proj, heartbeat_s=0)
        r.finish()
        self.assertIsNone(boardrun.expectation_problem(self.proj, "../../etc/passwd", "train"))

    def test_an_unknown_core_value_counts_as_core(self):
        """T1.7 (boardrun's half; the record check reports the value)"""
        self.attempt(["core: 是"], None)
        self.assertIn("（core: 是）", self.refused())

    def test_command_line(self):
        """T1.8: exit 4, the message on stderr, the command never runs."""
        self.attempt(["core: true"], None)
        marker = os.path.join(self.tmp, "ran")
        p = subprocess.run([sys.executable, "-m", "boardrun", "run", "--attempt", CORE, "--id", "cli", "--project", self.proj,
                            "--", sys.executable, "-c", "open(%r, 'w').close()" % marker],
                           cwd=os.path.join(SCRIPTS, "boardrun"), capture_output=True, text=True)
        self.assertEqual(p.returncode, 4)
        self.assertTrue(p.stderr.startswith("boardrun 没有启动：尝试 %s" % CORE), p.stderr)
        self.assertFalse(os.path.exists(marker))
        self.assertFalse(os.path.exists(os.path.join(self.runs, "cli")))

    def test_same_reading_as_the_console(self):
        """T1.9: boardrun's own small parser and projects.py agree on 预期, core and expect_skip."""
        texts = list(VARIANTS)
        fixtures = os.path.join(HERE, "fixtures", "projects")
        for dirpath, _, files in os.walk(fixtures):
            for f in files if "attempts" in dirpath else []:
                if f.endswith(".md"):
                    with open(os.path.join(dirpath, f), encoding="utf-8") as fh:
                        texts.append(fh.read())
        self.assertGreater(len(texts), 12)
        for text in texts:
            meta, body = projects.parse_front_matter(text)
            want = (projects.parse_onepager(projects.split_sections(body).get("一页纸")).get("预期") or {}).get("text", "")
            got_meta, got = boardrun.attempt_expectation(text)
            self.assertEqual(got, want, text)
            self.assertEqual((got_meta.get("core"), got_meta.get("expect_skip"), "expect_skip" in got_meta),
                             (meta.get("core"), meta.get("expect_skip"), "expect_skip" in meta), text)


if __name__ == "__main__":
    unittest.main()
