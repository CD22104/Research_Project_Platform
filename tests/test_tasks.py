"""Task sessions and the reply dispatcher (boardtool.py task-start / dispatch). tmux is faked; git is real.
Run: python3 -m unittest -v"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import boardtool as T  # noqa: E402


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True, capture_output=True)


class FakeTmux:
    """Stands in for tmux; every other command runs for real."""

    def __init__(self, sessions=()):
        self.sessions, self.started = list(sessions), []

    def __call__(self, cmd, **kw):
        if cmd[0] != "tmux":
            return subprocess.run(cmd, **kw)
        if cmd[1] == "list-sessions":
            return subprocess.CompletedProcess(cmd, 0, "\n".join(self.sessions) + "\n", "")
        if cmd[1] == "new-session":
            name = cmd[cmd.index("-s") + 1]
            self.sessions.append(name)
            self.started.append({"name": name, "dir": cmd[cmd.index("-c") + 1], "shell": cmd[-1]})
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[1] == "kill-session":
            name = cmd[-1].lstrip("=")
            if name in self.sessions:
                self.sessions.remove(name)
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return subprocess.CompletedProcess(cmd, 1, "", "no session")
        raise AssertionError(cmd)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "root")
        self.proj = os.path.join(self.root, "proj")
        self.board = os.path.join(self.proj, "board")
        os.makedirs(os.path.join(self.board, "tasks"))
        with open(os.path.join(self.board, "project.json"), "w") as f:
            json.dump({"title": "p"}, f)
        self.card("code-a", "执行", "待开始", scope="[src/]")
        self.card("code-b", "调试", "待开始", scope="[src/]")
        self.card("write", "写作", "待开始")
        self.card("done", "写作", "完成")
        git(self.proj, "init", "-q")
        git(self.proj, "add", ".")
        git(self.proj, "commit", "-q", "-m", "x")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def card(self, tid, kind, status, scope=None):
        with open(os.path.join(self.board, "tasks", tid + ".md"), "w") as f:
            f.write("---\nid: %s\ntitle: %s\nkind: %s\nstatus: %s\n%s---\n## 要做什么\nx\n" % (tid, tid, kind, status, "scope: %s\n" % scope if scope else ""))


class TaskStart(Base):
    def test_code_task_gets_a_worktree_and_a_named_session(self):
        tm = FakeTmux()
        x = T.task_start(self.root, "proj", "code-a", run=tm)
        wt = os.path.join(self.proj, ".worktrees", "code-a")
        self.assertEqual((x["session"], x["worktree"], x["name"]), ("task-proj-code-a", wt, "proj·code-a"))
        self.assertTrue(os.path.isfile(os.path.join(wt, ".git")))                     # a real git worktree
        branch = subprocess.run(["git", "branch", "--show-current"], cwd=wt, capture_output=True, text=True).stdout.strip()
        self.assertEqual(branch, "task/code-a")
        st = subprocess.run(["git", "status", "--porcelain"], cwd=self.proj, capture_output=True, text=True).stdout
        self.assertEqual(st, "")                                                       # the main checkout stays clean
        s = tm.started[0]
        self.assertEqual(s["dir"], wt)
        self.assertIn("'--remote-control' 'proj·code-a'", s["shell"])
        self.assertIn("unset ANTHROPIC_API_KEY", s["shell"])
        self.assertIn("记录一律写到项目主目录的 %s" % self.board, s["shell"])

    def test_writing_task_runs_in_the_project(self):
        tm = FakeTmux()
        x = T.task_start(self.root, "proj", "write", run=tm)
        self.assertEqual((x["dir"], x["worktree"]), (self.proj, None))
        self.assertNotIn("worktree", tm.started[0]["shell"])

    def test_limits(self):
        tm = FakeTmux()
        T.task_start(self.root, "proj", "code-a", run=tm)
        with self.assertRaisesRegex(T.TaskError, "已经在运行"):
            T.task_start(self.root, "proj", "code-a", run=tm)
        with self.assertRaisesRegex(T.TaskError, "同一个项目同时只做一张改代码的卡"):
            T.task_start(self.root, "proj", "code-b", run=tm)
        T.task_start(self.root, "proj", "write", run=tm)                                # writing can run beside it
        with self.assertRaisesRegex(T.TaskError, "只有 待开始 / 可以继续 / 进行中 的卡能开工"):
            T.task_start(self.root, "proj", "done", run=tm)
        with self.assertRaisesRegex(T.TaskError, "没有任务卡"):
            T.task_start(self.root, "proj", "ghost", run=tm)
        full = FakeTmux(["task-x-1", "task-y-2", "reply-z", "task-w-3", "gpuboard"])
        with self.assertRaisesRegex(T.TaskError, "已经有 4 个任务会话在运行"):
            T.task_start(self.root, "proj", "write", run=full)
        self.assertEqual(T.task_stop("proj", "write", run=tm), "task-proj-write")
        with self.assertRaises(T.TaskError):
            T.task_stop("proj", "write", run=tm)


class Dispatch(Base):
    def answer(self, rid, mode=None, at="2026-11-24T01:00:00Z"):
        with open(os.path.join(self.board, "answers.jsonl"), "a") as f:
            f.write(json.dumps({"id": rid, "answer": "好", "text": "", "at": at, **({"mode": mode} if mode else {})}) + "\n")

    def test_only_direct_replies_once(self):
        with open(os.path.join(self.board, "todos.jsonl"), "w") as f:
            for i in ("t1", "t2", "t3"):
                f.write(json.dumps({"id": i, "text": "问题 " + i, "done": None}) + "\n")
        self.answer("t1", "直接处理")
        self.answer("t2")                                                              # waits for 处理回复
        state = os.path.join(self.tmp, "state", "dispatch.json")
        tm = FakeTmux()
        got = T.dispatch(self.root, state, 0, run=tm)
        self.assertEqual(got, [{"project": "proj", "replies": ["t1"]}])
        self.assertEqual(tm.started[0]["name"], "reply-proj")
        self.assertIn("'-p' '--permission-mode' 'auto'", tm.started[0]["shell"])
        self.assertIn("这几条：t1", tm.started[0]["shell"])
        tm.sessions.clear()                                                            # that session finished
        self.assertEqual(T.dispatch(self.root, state, 300, run=tm), [])               # taken once
        self.answer("t1", "直接处理", at="2026-11-24T02:00:00Z")                         # the user changed the reply
        self.answer("t3", "直接处理")
        self.assertEqual(T.dispatch(self.root, state, 600, run=tm)[0]["replies"], ["t1", "t3"])
        self.answer("t2", "直接处理", at="2026-11-24T03:00:00Z")
        self.assertEqual(T.dispatch(self.root, state, 900, run=tm), [])               # reply-proj still running

    def test_dry_run_and_limit(self):
        with open(os.path.join(self.board, "todos.jsonl"), "w") as f:
            f.write(json.dumps({"id": "t1", "text": "q", "done": None}) + "\n")
        self.answer("t1", "直接处理")
        state = os.path.join(self.tmp, "state", "dispatch.json")
        full = FakeTmux(["task-a-1", "task-b-2", "task-c-3", "task-d-4"])
        self.assertEqual(T.dispatch(self.root, state, 0, run=full), [])                # at the limit
        tm = FakeTmux()
        self.assertEqual(len(T.dispatch(self.root, state, 0, run=tm, dry_run=True)), 1)
        self.assertEqual(tm.started, [])


if __name__ == "__main__":
    unittest.main()
