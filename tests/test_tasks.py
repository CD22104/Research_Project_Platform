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
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "skill", "research-board", "scripts"))
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

    def card(self, tid, kind, status, scope=None, sessions=None, board=None):
        with open(os.path.join(board or self.board, "tasks", tid + ".md"), "w") as f:
            f.write("---\nid: %s\ntitle: %s\nkind: %s\nstatus: %s\n%s%s---\n## 要做什么\nx\n" % (
                tid, tid, kind, status, "scope: %s\n" % scope if scope else "", "sessions: [%s]\n" % sessions if sessions else ""))


class TaskStart(Base):
    def test_code_task_gets_a_worktree_and_a_named_session(self):
        tm = FakeTmux()
        x = T.task_start(self.root, "proj", "code-a", run=tm, live=[])
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
        x = T.task_start(self.root, "proj", "write", run=tm, live=[])
        self.assertEqual((x["dir"], x["worktree"]), (self.proj, None))
        self.assertNotIn("worktree", tm.started[0]["shell"])

    def test_limits(self):
        tm = FakeTmux()
        T.task_start(self.root, "proj", "code-a", run=tm, live=[])
        with self.assertRaisesRegex(T.TaskError, "已经在运行"):
            T.task_start(self.root, "proj", "code-a", run=tm, live=[])
        with self.assertRaisesRegex(T.TaskError, "同一个项目同时只做一张改代码的卡"):
            T.task_start(self.root, "proj", "code-b", run=tm, live=[])
        T.task_start(self.root, "proj", "write", run=tm, live=[])                                # writing can run beside it
        with self.assertRaisesRegex(T.TaskError, "只有 待开始 / 可以继续 / 进行中 的卡能开工"):
            T.task_start(self.root, "proj", "done", run=tm, live=[])
        with self.assertRaisesRegex(T.TaskError, "没有任务卡"):
            T.task_start(self.root, "proj", "ghost", run=tm, live=[])
        full = FakeTmux(["task-x-1", "task-y-2", "reply-z", "task-w-3", "gpuboard"])
        with self.assertRaisesRegex(T.TaskError, "已经有 4 个任务会话在运行"):
            T.task_start(self.root, "proj", "write", run=full, live=[])
        self.assertEqual(T.task_stop("proj", "write", run=tm), "task-proj-write")
        with self.assertRaises(T.TaskError):
            T.task_stop("proj", "write", run=tm)


class TaskBegin(Base):
    """board task-begin: a conversation the user opened in the project's folder in the App takes a card
    (agreed 2026-11-24, cases 1-8)."""
    ME, OTHER = "11111111-0000-0000-0000-00000000000a", "22222222-0000-0000-0000-00000000000b"

    def live(self, *sids, name=None):
        return [{"sessionId": s, "name": name or s[:8], "cwd": self.proj} for s in sids]

    def begin(self, task, live=(), session_id=ME, tmux=()):
        return T.task_begin(self.root, "proj", task, session_id=session_id, live=list(live), run=FakeTmux(tmux))

    def test_1_code_card_gets_a_worktree(self):
        x = self.begin("code-a")
        wt = os.path.join(self.proj, ".worktrees", "code-a")
        self.assertEqual((x["workdir"], x["worktree"]), (wt, wt))
        branch = subprocess.run(["git", "branch", "--show-current"], cwd=wt, capture_output=True, text=True).stdout.strip()
        self.assertEqual(branch, "task/code-a")
        self.assertIn("把本会话 id「%s」加进任务卡的 sessions" % self.ME, x["steps"])
        self.assertIn("worktree（%s，分支 task/code-a）" % wt, x["steps"])
        self.assertIn("这个对话开在项目主目录", x["steps"])                     # it must work in the worktree path
        self.assertIn("记录一律写到项目主目录的 %s" % self.board, x["steps"])

    def test_2_writing_card_works_in_the_project(self):
        x = self.begin("write")
        self.assertEqual((x["workdir"], x["worktree"]), (self.proj, None))
        self.assertNotIn("worktree", x["steps"])
        self.assertFalse(os.path.exists(os.path.join(self.proj, ".worktrees", "write")))

    def test_3_finished_card_is_refused(self):
        with self.assertRaisesRegex(T.TaskError, "只有 待开始 / 可以继续 / 进行中 的卡能开工"):
            self.begin("done")
        with self.assertRaisesRegex(T.TaskError, "没有任务卡"):
            self.begin("ghost")

    def test_4_one_code_card_per_project(self):
        self.card("code-b", "调试", "进行中", scope="[src/]", sessions=self.OTHER)
        with self.assertRaisesRegex(T.TaskError, "同一个项目同时只做一张改代码的卡"):
            self.begin("code-a", live=self.live(self.OTHER))
        self.begin("write", live=self.live(self.OTHER))                           # writing can run beside it
        self.begin("code-a", live=[])                                             # that conversation is gone

    def test_5_limit_counts_every_kind_of_session_once(self):
        q = os.path.join(self.root, "q", "board")
        os.makedirs(os.path.join(q, "tasks"))
        with open(os.path.join(q, "project.json"), "w") as f:
            json.dump({"title": "q"}, f)
        s1, s2 = "33333333-0000-0000-0000-000000000001", "33333333-0000-0000-0000-000000000002"
        self.card("q1", "分析", "进行中", sessions=s1, board=q)
        self.card("q2", "分析", "进行中", sessions=s2, board=q)
        self.card("q3", "分析", "完成", sessions=self.OTHER, board=q)                  # finished: its idle conversation does not count
        self.card("write", "写作", "进行中")
        tmux = ["task-proj-write", "reply-q"]
        live = self.live(s1, s2, self.OTHER) + self.live("44444444-0000-0000-0000-000000000004", name="proj·write")  # task-start's session, seen twice
        self.assertEqual(sorted(T.busy_tasks(self.root, tmux, live)), ["reply-q", "task-proj-write", "task-q-q1", "task-q-q2"])
        with self.assertRaisesRegex(T.TaskError, "已经有 4 个任务会话在运行（上限 4）"):
            self.begin("code-a", live=live, tmux=tmux)
        with self.assertRaisesRegex(T.TaskError, "已经有 4 个任务会话在运行"):           # task-start counts the same way
            T.task_start(self.root, "proj", "code-a", run=FakeTmux(tmux), live=live)
        self.begin("code-a", live=[x for x in live if x["sessionId"] != s2], tmux=tmux)

    def test_6_a_card_has_one_conversation(self):
        self.card("write", "写作", "进行中", sessions=self.OTHER)
        with self.assertRaisesRegex(T.TaskError, "这张卡已经有对话在做"):
            self.begin("write", live=self.live(self.OTHER, self.ME))
        self.card("write", "写作", "进行中", sessions=self.ME)
        self.begin("write", live=self.live(self.ME))                              # the same conversation again is fine
        self.card("write", "写作", "可以继续", sessions=self.OTHER)
        self.begin("write", live=self.live(self.OTHER))                           # nobody works on it now: a new conversation may

    def test_7_facts_pack_knows_the_conversation(self):
        text = T.brief(self.proj, session_id=self.ME)
        self.assertIn("本会话 id：%s" % self.ME, text)
        self.assertIn("用户说「做 <任务 id>」：这个对话就是那张卡的任务会话。先运行 /home/dchen/bin/board task-begin <任务 id> --session %s" % self.ME, text)
        text = T.brief(self.proj)
        self.assertNotIn("本会话 id", text)
        self.assertIn("先运行 /home/dchen/bin/board task-begin <任务 id>，", text)
        cfg = os.path.join(self.tmp, "servers.json")
        with open(cfg, "w") as f:
            json.dump({"projects_root": self.root, "servers": []}, f)
        p = subprocess.run([sys.executable, os.path.join(os.path.join(os.path.dirname(HERE), "skill", "research-board", "scripts"), "boardtool.py"), "brief", "--hook"],
                           input=json.dumps({"cwd": self.proj, "session_id": self.ME}), capture_output=True, text=True,
                           env=dict(os.environ, GPUBOARD_CONFIG=cfg), timeout=20)
        self.assertIn("本会话 id：%s" % self.ME, p.stdout)                          # the SessionStart hook passes it on

    def test_8_command_line(self):
        cfg = os.path.join(self.tmp, "servers.json")
        with open(cfg, "w") as f:
            json.dump({"projects_root": self.root, "servers": []}, f)
        def cli(*args, cwd=self.proj):
            return subprocess.run([sys.executable, os.path.join(os.path.join(os.path.dirname(HERE), "skill", "research-board", "scripts"), "boardtool.py"), "task-begin", *args],
                                  cwd=cwd, capture_output=True, text=True, env=dict(os.environ, GPUBOARD_CONFIG=cfg), timeout=60)
        p = cli("write", "--session", self.ME, cwd=os.path.join(self.board, "tasks"))     # anywhere inside the project
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("可以开工：write（写作）", p.stdout)
        self.assertIn("把本会话 id「%s」加进任务卡的 sessions" % self.ME, p.stdout)
        p = cli("done")
        self.assertEqual(p.returncode, 1)
        self.assertIn("board: 任务卡的状态是「完成」", p.stderr)
        p = cli("write", cwd=self.tmp)
        self.assertEqual(p.returncode, 1)
        self.assertIn("不在任何项目里", p.stderr)


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
        got = T.dispatch(self.root, state, 0, run=tm, live=[])
        self.assertEqual(got, [{"project": "proj", "replies": ["t1"]}])
        self.assertEqual(tm.started[0]["name"], "reply-proj")
        self.assertIn("'-p' '--permission-mode' 'auto'", tm.started[0]["shell"])
        self.assertIn("这几条：t1", tm.started[0]["shell"])
        tm.sessions.clear()                                                            # that session finished
        self.assertEqual(T.dispatch(self.root, state, 300, run=tm, live=[]), [])               # taken once
        self.answer("t1", "直接处理", at="2026-11-24T02:00:00Z")                         # the user changed the reply
        self.answer("t3", "直接处理")
        self.assertEqual(T.dispatch(self.root, state, 600, run=tm, live=[])[0]["replies"], ["t1", "t3"])
        self.answer("t2", "直接处理", at="2026-11-24T03:00:00Z")
        self.assertEqual(T.dispatch(self.root, state, 900, run=tm, live=[]), [])               # reply-proj still running

    def test_dry_run_and_limit(self):
        with open(os.path.join(self.board, "todos.jsonl"), "w") as f:
            f.write(json.dumps({"id": "t1", "text": "q", "done": None}) + "\n")
        self.answer("t1", "直接处理")
        state = os.path.join(self.tmp, "state", "dispatch.json")
        full = FakeTmux(["task-a-1", "task-b-2", "task-c-3", "task-d-4"])
        self.assertEqual(T.dispatch(self.root, state, 0, run=full, live=[]), [])                # at the limit
        tm = FakeTmux()
        self.assertEqual(len(T.dispatch(self.root, state, 0, run=tm, dry_run=True, live=[])), 1)
        self.assertEqual(tm.started, [])


if __name__ == "__main__":
    unittest.main()
