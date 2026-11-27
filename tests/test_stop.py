"""The end-of-turn check (board stop-check, the Stop hook). Run: python3 -m unittest -v"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "skill", "research-board", "scripts")
sys.path[:0] = [SCRIPTS, HERE]
import boardtool as T  # noqa: E402
from test_tasks import FakeTmux, git  # noqa: E402

SID = "11111111-0000-0000-0000-00000000000a"
OTHER = "22222222-0000-0000-0000-00000000000b"
EMPTY = "- 做了什么：\n- 证据在哪：\n- 没做完的：\n- 下一步：\n- 要你决定的：\n"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        for name, value in (("STATE_HOME", os.path.join(self.home, ".local/state/research-board")),):
            p = mock.patch.object(T, name, value)
            p.start()
            self.addCleanup(p.stop)
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for k in (T.TASK_ENV, T.SESSION_ENV, T.REPLY_ENV):
            os.environ.pop(k, None)
        self.root = os.path.join(self.tmp, "root")
        self.proj = os.path.join(self.root, "proj")
        self.board = os.path.join(self.proj, "board")
        os.makedirs(os.path.join(self.board, "tasks"))
        with open(os.path.join(self.board, "project.json"), "w") as f:
            json.dump({"title": "p"}, f)
        self.card("write", "进行中", sessions=SID)
        self.notes(age=3 * 3600)
        git(self.proj, "init", "-q")
        git(self.proj, "add", ".")
        old = dict(os.environ, GIT_COMMITTER_DATE="2026-01-01T00:00:00Z", GIT_AUTHOR_DATE="2026-01-01T00:00:00Z")
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "x"], cwd=self.proj, env=old, check=True)
        self.transcript = os.path.join(self.tmp, "t.jsonl")
        open(self.transcript, "w").close()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def card(self, tid, status, sessions=None, handoff=EMPTY, kind="写作"):
        with open(os.path.join(self.board, "tasks", tid + ".md"), "w") as f:
            f.write("---\nid: %s\ntitle: 写报告 %s\nkind: %s\nstatus: %s\n%s---\n## 要做什么\nx\n## 交接\n%s" % (
                tid, tid, kind, status, "sessions: [%s]\n" % sessions if sessions else "", handoff))

    def notes(self, age=None):
        path = os.path.join(self.proj, "NOTES.md")
        if age is None:
            if os.path.exists(path):
                os.remove(path)
            return
        with open(path, "w") as f:
            f.write("# 进度笔记\n")
        os.utime(path, (time.time() - age, time.time() - age))

    def take(self, tid="write", ago=60):
        """What task-begin does when the session takes the card, some time ago."""
        _, sections = T.read_task(self.board, tid)
        T.snapshot_handoff("proj", tid, sections, time.time() - ago)

    def edit(self, path, ago=60, tool="Edit"):
        with open(self.transcript, "a") as f:
            stamp = time.strftime("%Y-%m-%dT%H:%M:%S.123Z", time.gmtime(time.time() - ago))
            f.write(json.dumps({"type": "assistant", "timestamp": stamp, "message": {"content": [
                {"type": "tool_use", "name": tool, "input": {"file_path": path, "command": "x"}}]}}) + "\n")

    def check(self, env=None, **data):
        d = {"session_id": SID, "cwd": self.proj, "transcript_path": self.transcript, "stop_hook_active": False}
        d.update(data)
        return T.stop_check(self.root, d, time.time(), env={} if env is None else env)


class TaskSessions(Base):
    def test_card_moved_on_with_an_old_handoff(self):
        """S1: 完成 with the handoff as it was when the session took the card: kept going once, told what to write."""
        self.card("write", "可以继续", sessions=SID, handoff="- 做了什么：上一个会话写的\n")
        self.take()
        self.card("write", "完成", sessions=SID, handoff="- 做了什么：上一个会话写的\n")
        path = os.path.join(self.board, "tasks", "write.md")
        self.assertEqual(self.check(), "收尾检查：任务卡 write（写报告 write）已经是「完成」，但交接还是接卡时的样子，这次没有更新。"
                         "结束前请在 %s 的「## 交接」写上这次的五项：做了什么、证据在哪（文件、运行编号、提交号）、没做完的、下一步、"
                         "要你决定的。写好后简短告诉用户就可以停下。" % path)
        self.card("write", "完成", sessions=SID, handoff="- 做了什么：这次写了第二节\n")
        self.assertIsNone(self.check())

    def test_never_while_already_kept_going(self):
        """S2"""
        self.take()
        self.card("write", "等你", sessions=SID)
        self.assertIsNone(self.check(stop_hook_active=True))
        self.assertIsNotNone(self.check())

    def test_work_done_with_an_untouched_card(self):
        """S3: 进行中, a commit or a run since the card was taken, handoff untouched; a plain chat turn passes."""
        self.take(ago=120)
        self.assertIsNone(self.check())                                         # nothing done yet: just talking
        with open(os.path.join(self.proj, "report.md"), "w") as f:
            f.write("x\n")
        git(self.proj, "add", ".")
        git(self.proj, "commit", "-q", "-m", "report")
        d = os.path.join(self.board, "runs", "r1")
        os.makedirs(d)
        with open(os.path.join(d, "run.json"), "w") as f:
            json.dump({"id": "r1", "task": "write", "status": "运行中", "started": T.utc(time.time() - 30)}, f)
        reason = self.check()
        self.assertTrue(reason.startswith("收尾检查：本会话接手任务卡 write（写报告 write）以后提交了 1 次代码、启动了 1 次运行，但任务卡的交接还没动过。"), reason)
        self.assertIn("回复一句「还在做」即可。", reason)

    def test_cards_taken_before_the_check_existed(self):
        """S4: no snapshot, no check."""
        self.card("write", "完成", sessions=SID)
        self.assertIsNone(self.check())

    def test_same_situation_once(self):
        """S5"""
        self.take()
        self.card("write", "完成", sessions=SID)
        self.assertIsNotNone(self.check())
        self.assertIsNone(self.check())                                         # it said why not: not again
        self.card("write", "等你", sessions=SID)
        self.assertIsNotNone(self.check())                                      # a new situation

    def test_a_started_session_is_known_by_its_environment(self):
        """S6: task-start's session names itself on the card by name, not id; the environment says which card."""
        self.card("bg", "进行中", sessions="proj·bg")
        self.take("bg")
        self.card("bg", "等运行", sessions="proj·bg")
        self.assertIn("任务卡 bg", self.check(env={T.TASK_ENV: "proj/bg"}, session_id=OTHER))
        self.assertIsNone(self.check(session_id=OTHER))                         # without it: not this card's session


class ProjectSessions(Base):
    def setUp(self):
        super().setUp()
        self.card("write", "进行中", sessions=OTHER)                              # this session holds no card

    def test_board_edited_and_notes_hours_old(self):
        """S7"""
        self.edit(os.path.join(self.board, "attempts", "a.md"))
        self.edit(os.path.join(self.board, "todos.jsonl"), tool="Write")
        self.edit(os.path.join(self.board, "attempts", "b.md"), tool="Read")     # reading is not editing
        self.edit(os.path.join(self.proj, "src", "x.py"))                        # not board/
        self.assertEqual(self.check(), "收尾检查：本会话改过 board/（2 个文件：board/attempts/a.md、board/todos.jsonl），"
                         "而 NOTES.md 已经 3 小时没更新。换会话时靠 NOTES.md 接上：请把这次定下的事、做到哪、下一步补进 %s 的交接。"
                         "如果这次的改动不值得记，回复一句说明即可。" % os.path.join(self.proj, "NOTES.md"))
        self.assertIsNone(self.check())                                         # at most once per 2 hours

    def test_notes_recent_or_edits_older(self):
        self.edit(os.path.join(self.board, "plan.md"), ago=4 * 3600)             # before NOTES.md was last changed
        self.assertIsNone(self.check())
        self.notes(age=3600)
        self.edit(os.path.join(self.board, "plan.md"))
        self.assertIsNone(self.check())                                         # NOTES.md is an hour old: fine
        self.notes(age=None)
        self.assertIn("而 NOTES.md 还不存在。", self.check())

    def test_who_is_not_asked(self):
        """S8: reply sessions, sessions outside projects; a task worktree belongs to its project."""
        self.edit(os.path.join(self.board, "plan.md"))
        self.assertIsNone(self.check(env={T.REPLY_ENV: "proj"}))
        self.assertIsNone(self.check(cwd=self.tmp))
        wt = os.path.join(self.proj, ".worktrees", "code-a")
        os.makedirs(os.path.join(wt, "board"))
        with open(os.path.join(wt, "board", "project.json"), "w") as f:
            f.write("{}")
        self.assertEqual(T.project_dir_of(wt, self.root), os.path.realpath(self.proj))
        self.assertIsNotNone(self.check(cwd=wt))


class Wiring(Base):
    def test_the_hook(self):
        """S9: JSON with decision block on stdout; anything odd: exit 0, silent."""
        self.take()
        self.card("write", "完成", sessions=SID)
        cfg = os.path.join(self.tmp, "config.json")
        with open(cfg, "w") as f:
            json.dump({"projects_root": self.root, "servers": []}, f)
        env = dict(os.environ, HOME=self.home, RESEARCH_BOARD_CONFIG=cfg)

        def hook(stdin):
            p = subprocess.run([sys.executable, os.path.join(SCRIPTS, "boardtool.py"), "stop-check", "--hook"], input=stdin,
                               capture_output=True, text=True, env=env, timeout=30)
            return p.returncode, p.stdout, p.stderr
        code, out, err = hook(json.dumps({"session_id": SID, "cwd": self.proj, "stop_hook_active": False}))
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)["decision"], "block")
        self.assertIn("任务卡 write", json.loads(out)["reason"])
        for stdin in ("not json", "[]", json.dumps({"session_id": "../x", "cwd": self.proj})):
            self.assertEqual(hook(stdin), (0, "", ""))

    def test_taking_a_card_remembers_its_handoff(self):
        """S10: task-begin and task-start snapshot the handoff; reply sessions are marked."""
        self.card("write", "进行中", sessions=SID, handoff="- 做了什么：旧的\n")
        T.task_begin(self.root, "proj", "write", session_id=SID, live=[], run=FakeTmux(), env={})
        snap = T._load_json(T._snapshot_path("proj", "write"))
        self.assertEqual(snap["sha"], T.handoff_sha(T.read_task(self.board, "write")[1]))
        self.card("other", "待开始")
        T.task_start(self.root, "proj", "other", run=FakeTmux(), live=[], env={})
        self.assertTrue(os.path.isfile(T._snapshot_path("proj", "other")))
        with open(os.path.join(self.board, "answers.jsonl"), "w") as f:
            f.write(json.dumps({"id": "t1", "answer": "好", "text": "", "at": "2026-11-27T01:00:00Z", "mode": "直接处理"}) + "\n")
        with open(os.path.join(self.board, "todos.jsonl"), "w") as f:
            f.write(json.dumps({"id": "t1", "text": "要不要补跑", "kind": "decision", "created": "2026-11-27T00:00:00Z", "done": None}) + "\n")
        tm = FakeTmux()
        T.dispatch(self.root, os.path.join(self.tmp, "state", "dispatch.json"), time.time(), run=tm, live=[])
        self.assertIn("export RESEARCH_BOARD_REPLY=proj;", tm.started[0]["shell"].replace("'", ""))


if __name__ == "__main__":
    unittest.main()
