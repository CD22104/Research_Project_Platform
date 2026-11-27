"""Sessions waiting for a permission answer (board waiting, the Notification hook). Run: python3 -m unittest -v"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SCRIPTS = os.path.join(REPO, "skill", "research-board", "scripts")
sys.path.insert(0, SCRIPTS)
import boardtool as T  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures", "projects")
SID = "11111111-2222-3333-4444-555555555555"
SID2 = "66666666-7777-8888-9999-000000000000"


def dead_pid():
    p = subprocess.Popen(["true"])
    p.wait()
    return p.pid


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.wdir = os.path.join(self.home, ".local", "state", "research-board", "waiting")
        self.root = os.path.join(self.tmp, "projects")
        shutil.copytree(FIXTURES, self.root, symlinks=True)
        self.proj = os.path.join(self.root, "demo")
        self.cfg = os.path.join(self.tmp, "config.json")
        with open(self.cfg, "w") as f:
            json.dump({"projects_root": self.root, "state_dir": os.path.join(self.tmp, "state"), "servers": []}, f)
        self.transcript = os.path.join(self.tmp, "t.jsonl")
        self.write_transcript([("Read", {"file_path": "/x/notes.md"}),
                               ("Bash", {"command": "pip install secretpkg --token abc", "description": "安装依赖"})])
        patcher = mock.patch.object(T, "WAITING_DIR", self.wdir)  # in-process calls use the same directory
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def write_transcript(self, calls):
        with open(self.transcript, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user", "message": {"role": "user", "content": "做 f1-metric"}}) + "\n")
            for name, inp in calls:
                f.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
                    {"type": "thinking", "thinking": "the word tool_use in a thought"}]}}) + "\n")
                f.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "x", "name": name, "input": inp}]}}, ensure_ascii=False) + "\n")

    def data(self, **kw):
        d = {"session_id": SID, "cwd": self.proj, "transcript_path": self.transcript, "hook_event_name": "Notification",
             "notification_type": "permission_prompt", "message": "Claude needs your permission to use Bash"}
        d.update(kw)
        return d

    def run_board(self, args, stdin, path=None, wrapper=True):
        env = dict(os.environ, HOME=self.home, RESEARCH_BOARD_CONFIG=self.cfg, CLAUDE_PID=str(os.getpid()))
        env.pop("TMUX", None)
        if path:
            env["PATH"] = path
        cmd = [os.path.join(SCRIPTS, "board")] if wrapper else [sys.executable, os.path.join(SCRIPTS, "boardtool.py")]
        p = subprocess.run(cmd + args, input=stdin, capture_output=True, text=True, env=env, timeout=20)
        return p.returncode, p.stdout, p.stderr

    def files(self):
        return sorted(os.listdir(self.wdir)) if os.path.isdir(self.wdir) else []


class Record(Base):
    def test_permission_prompt_is_recorded_without_the_command(self):
        """T3.1: what = Bash's own description; the command itself is nowhere in the file."""
        code, out, err = self.run_board(["waiting", "--hook"], json.dumps(self.data()))
        self.assertEqual((code, out, err), (0, "", ""))
        self.assertEqual(self.files(), [SID + ".json"])
        with open(os.path.join(self.wdir, SID + ".json"), encoding="utf-8") as f:
            text = f.read()
        rec = json.loads(text)
        self.assertEqual(rec["what"], "Bash：安装依赖")
        self.assertNotIn("pip install", text)
        self.assertNotIn("secretpkg", text)
        self.assertEqual((rec["session"], rec["project"], rec["pid"]), (SID, "demo", os.getpid()))
        self.assertEqual(rec["message"], "Claude needs your permission to use Bash")
        self.assertRegex(rec["since"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(os.stat(self.wdir).st_mode & 0o777, 0o700)
        code, out, _ = self.run_board(["waiting", "--json"], "")
        self.assertEqual([r["session"] for r in json.loads(out)], [SID])

    def test_project_and_task_card(self):
        """T3.2: the card that names this session, the card of a task-<project>-<task> tmux session, no project outside."""
        card = os.path.join(self.proj, "board", "tasks", "report-r1.md")
        with open(card, encoding="utf-8") as f:
            text = f.read()
        with open(card, "w", encoding="utf-8") as f:
            f.write(text.replace("status: 待开始", "status: 进行中\nsessions: [%s]" % SID, 1))
        with mock.patch.object(T, "_tmux_name", return_value=None):
            rec = T.waiting_record(self.data(), self.root)
        self.assertEqual((rec["project"], rec["task"]), ("demo", "report-r1"))
        with mock.patch.object(T, "_tmux_name", return_value="task-demo-f1-metric"):
            rec = T.waiting_record(self.data(session_id=SID2), self.root)
        self.assertEqual((rec["task"], rec["tmux"]), ("f1-metric", "task-demo-f1-metric"))
        with mock.patch.object(T, "_tmux_name", return_value=None):
            rec = T.waiting_record(self.data(cwd=self.tmp), self.root)
        self.assertEqual((rec["project"], rec["task"], rec["cwd"]), (None, None, self.tmp))

    def test_other_notifications_are_ignored(self):
        """T3.3"""
        for kind in ("idle_prompt", "auth_success", "agent_completed"):
            self.assertEqual(self.run_board(["waiting", "--hook"], json.dumps(self.data(notification_type=kind)))[0], 0)
        self.assertEqual(self.files(), [])

    def test_clear_removes_only_this_session(self):
        """T3.4"""
        self.run_board(["waiting", "--hook"], json.dumps(self.data()))
        self.run_board(["waiting", "--hook"], json.dumps(self.data(session_id=SID2)))
        self.assertEqual(self.files(), sorted([SID + ".json", SID2 + ".json"]))
        for event in ("PostToolUse", "Stop"):
            code, out, err = self.run_board(["waiting", "--clear", "--hook"], json.dumps({"session_id": SID, "hook_event_name": event}))
            self.assertEqual((code, out, err), (0, "", ""))
        self.assertEqual(self.files(), [SID2 + ".json"])

    def test_a_hook_never_gets_in_the_way(self):
        """T3.5: bad input, a session id that is a path, an unwritable directory, a missing transcript."""
        for stdin in ("not json", "[]", json.dumps(self.data(session_id="../../etc/passwd")), json.dumps(self.data(session_id=7))):
            for args in (["waiting", "--hook"], ["waiting", "--clear", "--hook"]):
                self.assertEqual(self.run_board(args, stdin, wrapper=False), (0, "", ""))
        self.assertEqual(self.files(), [])
        os.makedirs(os.path.dirname(self.wdir))
        with open(self.wdir, "w") as f:  # the directory cannot be made
            f.write("x")
        self.assertEqual(self.run_board(["waiting", "--hook"], json.dumps(self.data())), (0, "", ""))
        os.remove(self.wdir)
        rec = T.waiting_record(self.data(transcript_path=os.path.join(self.tmp, "gone.jsonl")), self.root)
        self.assertEqual((rec["what"], rec["message"]), (None, "Claude needs your permission to use Bash"))

    def test_clear_with_nobody_waiting_does_not_start_python(self):
        """T3.6: the wrapper stops before python when the directory is empty or missing."""
        fake = os.path.join(self.tmp, "fakebin")
        os.makedirs(fake)
        marker = os.path.join(self.tmp, "python-ran")
        with open(os.path.join(fake, "python3"), "w") as f:
            f.write("#!/bin/sh\ntouch %s\nexit 1\n" % marker)
        os.chmod(os.path.join(fake, "python3"), 0o755)
        path = fake + ":/usr/bin:/bin"
        stdin = json.dumps({"session_id": SID, "hook_event_name": "PostToolUse"})
        self.assertEqual(self.run_board(["waiting", "--clear", "--hook"], stdin, path=path), (0, "", ""))
        os.makedirs(self.wdir)
        self.assertEqual(self.run_board(["waiting", "--clear", "--hook"], stdin, path=path)[0], 0)
        self.assertFalse(os.path.exists(marker))
        with open(os.path.join(self.wdir, SID2 + ".json"), "w") as f:
            f.write("{}")
        self.run_board(["waiting", "--clear", "--hook"], stdin, path=path)
        self.assertTrue(os.path.exists(marker))  # somebody waits: python does the work

    def test_listing_drops_sessions_that_are_gone(self):
        os.makedirs(self.wdir)
        for sid, pid in ((SID, os.getpid()), (SID2, dead_pid())):
            with open(os.path.join(self.wdir, sid + ".json"), "w") as f:
                json.dump({"session": sid, "pid": pid, "since": "2026-11-25T03:28:06Z"}, f)
        with open(os.path.join(self.wdir, "broken.json"), "w") as f:
            f.write("{")
        self.assertEqual([r["session"] for r in T.list_waiting()], [SID])
        self.assertEqual(self.files(), [SID + ".json"])

    def test_a_second_prompt_keeps_when_the_waiting_began(self):
        with mock.patch.object(T, "_hook_input", return_value=self.data()), mock.patch.dict(os.environ, {"CLAUDE_PID": str(os.getpid())}):
            T.hook_waiting(self.root, now=1795600000)
            T.hook_waiting(self.root, now=1795600600)
        with open(os.path.join(self.wdir, SID + ".json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["since"], T.utc(1795600000))


class What(unittest.TestCase):
    def test_short_and_safe(self):
        self.assertEqual(T.waiting_what("Read", {"file_path": "/shared/x/MODELS.md"}), "Read /shared/x/MODELS.md")
        self.assertEqual(T.waiting_what("Grep", {"pattern": "HF_TOKEN=.*", "path": "/home/u/.config"}), "Grep /home/u/.config")
        self.assertEqual(T.waiting_what("WebFetch", {"url": "https://example.org/a?key=1", "prompt": "p"}), "WebFetch example.org")
        self.assertEqual(T.waiting_what("Bash", {"command": "rm -rf build"}), "Bash")
        self.assertEqual(T.waiting_what("mcp__x__send", {"text": "hello"}), "mcp__x__send")
        long = T.waiting_what("Bash", {"description": "很" * 300})
        self.assertEqual(len(long), T.WHAT_MAX)
        self.assertTrue(long.endswith("…"))
        self.assertIsNone(T.waiting_what(None, None))

    def test_last_tool_call(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.jsonl")
            with open(path, "w") as f:
                f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "/a"}}]}}) + "\n")
                f.write("{broken line with \"tool_use\"\n")
                f.write(json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}}) + "\n")
            self.assertEqual(T.last_tool_call(path), ("Read", {"file_path": "/a"}))
            self.assertEqual(T.last_tool_call(os.path.join(d, "none")), (None, None))
            self.assertEqual(T.last_tool_call(None), (None, None))

    def test_one_directory_everywhere(self):
        """scripts/board's shortcut looks in the directory the hooks write to."""
        with open(os.path.join(SCRIPTS, "board"), encoding="utf-8") as f:
            self.assertIn('"$HOME/%s"' % T.WAITING_DIR[2:], f.read())


if __name__ == "__main__":
    unittest.main()
