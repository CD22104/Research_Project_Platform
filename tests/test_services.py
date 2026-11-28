"""Services started and always stopped by a script (services.py, board serve). ssh, tmux and the GPUs are faked.
Run: python3 -m unittest -v"""
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "skill", "research-board", "scripts"))
import projects as P  # noqa: E402
import services as S  # noqa: E402

NOW = 1_800_000_000.0
FREE = 100


class FakeServers:
    """Stands in for `bash -c` here and `ssh host` elsewhere: answers the probe from a made-up state and records
    every tmux start and stop."""

    def __init__(self, cfg, here):
        self.hosts = {s["host"]: s["name"] for s in cfg["servers"]}
        self.here = here
        self.state = {s["name"]: {"gpus": {i: FREE for i in range(8)}, "tmux": set(), "ports": set(), "waiting": [],
                                  "healthy": set(), "up": True} for s in cfg["servers"]}
        self.started, self.killed, self.calls = [], [], []

    def __call__(self, cmd, **kw):
        srv, script = (self.here, cmd[2]) if cmd[:2] == ["bash", "-c"] else (self.hosts[cmd[-2]], cmd[-1])
        self.calls.append(srv)
        st = self.state[srv]
        if not st["up"]:
            return subprocess.CompletedProcess(cmd, 255, "", "ssh: connect to host: timed out")
        if "@@@GPU" in script:
            keys = re.findall(r"^echo (\S+) \$\(curl", script, re.M)
            out = ["@@@GPU"] + ["%d, %d" % (i, m) for i, m in sorted(st["gpus"].items())]
            out += ["@@@TMUX"] + sorted(st["tmux"]) + ["@@@PORTS"] + ["127.0.0.1:%d" % p for p in sorted(st["ports"])]
            out += ["@@@WAIT"] + [json.dumps(w) for w in st["waiting"]]
            out += ["@@@HEALTH"] + ["%s %s" % (k, "200" if k in st["healthy"] else "000") for k in keys] + ["@@@END"]
            return subprocess.CompletedProcess(cmd, 0, "\n".join(out) + "\n", "")
        m = re.search(r"tmux new-session -d -s (\S+) (.*)$", script)
        if m:
            name = m.group(1).strip("'")
            self.started.append({"server": srv, "tmux": name, "script": script})
            st["tmux"].add(name)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        m = re.search(r"tmux kill-session -t '?=(\S+?)'? ", script)
        if m:
            self.killed.append((srv, m.group(1)))
            st["tmux"].discard(m.group(1))
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(script)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "projects")
        self.board = os.path.join(self.root, "proj", "board")
        for d in ("tasks", "decisions", "services", "runs"):
            os.makedirs(os.path.join(self.board, d))
        with open(os.path.join(self.board, "project.json"), "w") as f:
            json.dump({"title": "p"}, f)
        self.model = os.path.join(self.tmp, "shared", "models", "Qwen--Qwen3.5-9B")
        os.makedirs(self.model)
        self.cfg = {"projects_root": self.root, "shared_dir": os.path.join(self.tmp, "shared"),
                    "state_dir": os.path.join(self.tmp, "state"), "home_server": "gpu-a100",
                    "servers": [{"name": "gpu-a100", "host": "a.example"}, {"name": "gpu-h100", "host": "h.example"}],
                    "services": {"vllm": {"command": "CUDA_VISIBLE_DEVICES={gpus} ~/.venvs/vllm-serve/bin/vllm serve {model} "
                                                     "--served-model-name {served_name} --host 127.0.0.1 --port {port} --max-model-len {max_model_len}",
                                          "health": "http://127.0.0.1:{port}/v1/models", "ports": [8771, 8779]}}}
        self.card("l1", "进行中")
        self.decision("limits", "采纳", "[gpu-a100 4, gpu-h100 all]")
        self.fake = FakeServers(self.cfg, "gpu-a100")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def card(self, tid, status):
        with open(os.path.join(self.board, "tasks", tid + ".md"), "w") as f:
            f.write("---\nid: %s\ntitle: %s\nkind: 执行\nstatus: %s\n---\n## 要做什么\nx\n" % (tid, tid, status))

    def decision(self, did, status, limit, date="2026-11-24"):
        with open(os.path.join(self.board, "decisions", did + ".md"), "w") as f:
            f.write("---\nid: %s\ntitle: 夜里用卡\nstatus: %s\ndate: %s\n%s---\n## 决定\nx\n## 理由\nx\n## 影响\nx\n"
                    % (did, status, date, "gpu_limit: %s\n" % limit if limit else ""))

    def request(self, name="qwen9b", **kw):
        req = {"template": "vllm", "model": self.model, "served_name": "small-9b", "max_model_len": 16384, "task": "l1"}
        req.update(kw)
        with open(os.path.join(self.board, "services", name + ".json"), "w") as f:
            json.dump(req, f)

    def run_(self, n=1, now=NOW):
        for _ in range(n):
            out = S.serve(self.root, self.cfg, now, run=self.fake, here="gpu-a100")
        return out

    def status(self, name="qwen9b", closed=False):
        return S._load(os.path.join(self.board, "services", "closed" if closed else "", name + ".status.json"))

    def todos(self):
        path = os.path.join(self.board, "todos.jsonl")
        return P.read_todos(path)["items"] if os.path.exists(path) else []


class Requests(Base):
    def test_refusals(self):
        """T5.1"""
        ok = {"template": "vllm", "model": self.model, "served_name": "q", "max_model_len": 16384, "task": "l1"}
        self.assertIsNone(S.refusal("qwen9b", ok, self.cfg, self.board))
        os.makedirs(os.path.join(self.tmp, "elsewhere"))
        cases = [("qwen9b", dict(ok, model=os.path.join(self.tmp, "elsewhere")), "model 要写共享目录 models/ 下的完整路径"),
                 ("qwen9b", dict(ok, model=self.model + "/../../../elsewhere"), "model 要写共享目录 models/ 下的完整路径"),
                 ("qwen9b", dict(ok, model=self.model + "-missing"), "不存在"),
                 ("qwen9b", dict(ok, template="sglang"), "模板「sglang」不在个人配置的 services 里（有：vllm）"),
                 ("qwen9b", dict(ok, served_name="q; rm -rf ~"), "served_name 只能用字母、数字和 . _ -"),
                 ("qwen9b", dict(ok, task="ghost"), "task 要写申请它的任务卡 id"),
                 ("qwen9b", dict(ok, command="echo hi"), "申请里有不认识的字段：command"),
                 ("qwen9b", dict(ok, gpus=0), "gpus 是卡数"),
                 ("qwen9b", dict(ok, hours=48), "hours 是最长占用的小时数"),
                 ("qwen9b", dict(ok, server="gpu-h200"), "server「gpu-h200」不在配置的服务器里"),
                 ("q;x", ok, "名字「q;x」只能用字母、数字和 . _ -")]
        for name, req, want in cases:
            self.assertIn(want, S.refusal(name, req, self.cfg, self.board) or "", req)
        self.assertIsNone(S.refusal("qwen9b", dict(ok, server="a100"), self.cfg, self.board))   # short names work

    def test_values_are_quoted_into_the_template(self):
        spaced = os.path.join(self.tmp, "shared", "models", "Qwen 9B; echo pwned")
        cmd = S.command(self.cfg["services"]["vllm"], {"model": spaced, "served_name": "q", "max_model_len": 4096}, [3, 4], 8772)
        self.assertIn("CUDA_VISIBLE_DEVICES=3,4 ~/.venvs/vllm-serve/bin/vllm serve '%s'" % spaced, cmd)
        self.assertIn("--port 8772 --max-model-len 4096", cmd)

    def test_gpu_limit(self):
        """T5.2"""
        row = lambda did, status, limit, date="2026-11-24": {"meta": {"id": did, "status": status, "gpu_limit": limit, "date": date}}
        self.assertEqual(S.gpu_limit([row("a", "采纳", ["gpu-a100 4", "h100 all"])], {}, self.cfg), ({"gpu-a100": 4, "gpu-h100": None}, []))
        self.assertEqual(S.gpu_limit([row("a", "候选", ["gpu-a100 4"])], {}, self.cfg), (None, []))
        self.assertEqual(S.gpu_limit([row("a", "候选", ["gpu-a100 4"])], {"d:a": {"answer": "采纳"}}, self.cfg)[0], {"gpu-a100": 4})
        two = [row("old", "采纳", ["gpu-a100 4"], "2026-11-20"), row("new", "采纳", ["gpu-a100 2"], "2026-11-26")]
        self.assertEqual(S.gpu_limit(two, {}, self.cfg)[0], {"gpu-a100": 2})
        self.assertEqual(S.gpu_limit([row("a", "采纳", ["gpu-h200 4", "gpu-a100 four"])], {}, self.cfg),
                         ({}, ["决定 a 的 gpu_limit 里「gpu-h200 4」看不懂（写「服务器 卡数」或「服务器 all」）",
                               "决定 a 的 gpu_limit 里「gpu-a100 four」看不懂（写「服务器 卡数」或「服务器 all」）"]))
        self.assertEqual(S.gpu_limit([], {}, self.cfg), (None, []))

    def test_record_check(self):
        """The record check reports a gpu_limit it cannot read and a start value other than auto."""
        self.decision("limits", "采纳", "[gpu-a100 4, gpu-h100, h200 four]")
        with open(os.path.join(self.board, "tasks", "l2.md"), "w") as f:
            f.write("---\nid: l2\ntitle: l2\nkind: 执行\nstatus: 待开始\nstart: yes\n---\n## 要做什么\nx\n")
        got = [c["what"].split("：")[0] for c in P.check_project(P.scan_project(self.board))
               if "gpu_limit" in c["what"] or c["what"].startswith("start ")]
        self.assertEqual(got, ["start 只能写 auto（after 里的卡都完成后，派活程序自动开这张卡；要先问过用户）", "gpu_limit 里「gpu-h100」看不懂",
                               "gpu_limit 里「h200 four」看不懂"])

    def test_no_limit_refuses_and_asks_once(self):
        self.decision("limits", "候选", "[gpu-a100 4]")
        self.request()
        self.run_(n=2)
        self.assertEqual((self.status()["state"], self.fake.started), ("拒绝", []))
        self.assertIn("没有已采纳的决定写着本项目最多占几张卡", self.status()["reason"])
        self.assertEqual([t["id"] for t in self.todos()], ["svc-no-gpu-limit"])


class Starting(Base):
    def test_start_on_the_first_free_card_within_the_limit(self):
        """T5.3: cards 0, 1 busy with someone else, card 2 with this project's run; port 8771 taken."""
        a = self.fake.state["gpu-a100"]
        a["gpus"].update({0: 30000, 1: 20000, 2: 40000})
        a["ports"].add(8771)
        os.makedirs(os.path.join(self.board, "runs", "r1"))
        with open(os.path.join(self.board, "runs", "r1", "run.json"), "w") as f:
            json.dump({"id": "r1", "status": "运行中", "server": "gpu-a100", "gpus": [2]}, f)
        self.request()
        changes = self.run_()
        st = self.status()
        self.assertEqual((st["state"], st["server"], st["gpus"], st["port"], st["tmux"], st["url"]),
                         ("启动中", "gpu-a100", [3], 8772, "svc-proj-qwen9b", "http://127.0.0.1:8772/v1"))
        [s] = self.fake.started
        self.assertEqual((s["server"], s["tmux"]), ("gpu-a100", "svc-proj-qwen9b"))
        self.assertIn("CUDA_VISIBLE_DEVICES=3 ~/.venvs/vllm-serve/bin/vllm serve %s" % self.model, s["script"])
        self.assertIn("; } >> $HOME/.local/state/research-board/services/proj-qwen9b.log 2>&1", s["script"])   # all of it logged
        self.assertEqual([c["what"] for c in changes], ["起服务 qwen9b：gpu-a100 3 号卡，端口 8772"])
        self.run_()
        self.assertEqual(len(self.fake.started), 1)                                # not started twice

    def test_queue_when_at_the_limit_or_no_free_card(self):
        """T5.4"""
        self.decision("limits", "采纳", "[gpu-a100 1, gpu-h100 all]")
        os.makedirs(os.path.join(self.board, "runs", "r1"))
        with open(os.path.join(self.board, "runs", "r1", "run.json"), "w") as f:
            json.dump({"id": "r1", "status": "运行中", "server": "gpu-a100", "gpus": [5]}, f)
        self.fake.state["gpu-h100"]["gpus"] = {0: 70000, 1: 70000}
        self.request()
        self.run_()
        self.assertEqual((self.status()["state"], self.status()["reason"]),
                         ("排队", "gpu-a100 上本项目已占 1 张，上限 1；gpu-h100 空卡 0 张，要 1 张"))
        self.assertEqual(self.fake.started, [])

    def test_hold_while_the_requester_waits_for_approval(self):
        """T5.5"""
        self.fake.state["gpu-a100"]["waiting"] = [{"session": "abc", "project": "proj", "task": "l1", "pid": 1}]
        self.request()
        self.run_()
        self.assertEqual(self.status()["state"], "暂缓")
        self.assertEqual(self.fake.started, [])
        self.fake.state["gpu-a100"]["waiting"] = []
        self.run_()
        self.assertEqual(self.status()["state"], "启动中")

    def test_ready_or_failed(self):
        """T5.6"""
        self.request()
        self.run_()
        self.run_(now=NOW + 120)
        self.assertEqual(self.status()["state"], "启动中")
        self.fake.state["gpu-a100"]["healthy"].add("qwen9b")
        self.run_(now=NOW + 300)
        self.assertEqual((self.status()["state"], self.status()["ready_at"]), ("就绪", S.utc(NOW + 300)))
        self.request("slow")
        self.run_(now=NOW + 600)
        self.run_(now=NOW + 600 + 16 * 60)
        st = self.status("slow", closed=True)
        self.assertEqual((st["state"], st["reason"]), ("失败", "15 分钟还没通过健康检查"))
        self.assertIn(("gpu-a100", "svc-proj-slow"), self.fake.killed)
        self.assertEqual([t["id"].split("-2")[0] for t in self.todos()], ["svc-failed-slow"])


class AlwaysStopped(Base):
    """T5.7: whatever happens, the service is stopped, the card freed, the reason written down."""

    def started(self, **kw):
        self.request(**kw)
        self.run_()
        self.fake.state["gpu-a100"]["healthy"].add("qwen9b")
        self.run_(now=NOW + 300)
        self.assertEqual(self.status()["state"], "就绪")

    def stopped(self, reason, state="已停", now=NOW + 600):
        self.run_(now=now)
        st = self.status(closed=True)
        self.assertEqual((st["state"], st["reason"]), (state, reason))
        self.assertIsNone(self.status())
        self.assertFalse(os.path.exists(os.path.join(self.board, "services", "qwen9b.json")))
        return st

    def test_done(self):
        self.started()
        open(os.path.join(self.board, "services", "qwen9b.done"), "w").close()
        self.stopped("申请方写了 .done")
        self.assertEqual(self.fake.killed, [("gpu-a100", "svc-proj-qwen9b")])
        self.assertTrue(os.path.exists(os.path.join(self.board, "services", "closed", "qwen9b.done")))

    def test_request_deleted(self):
        self.started()
        os.remove(os.path.join(self.board, "services", "qwen9b.json"))
        self.stopped("申请文件删掉了")

    def test_card_finished(self):
        for status in ("完成", "放弃", "暂停"):
            with self.subTest(status=status):
                self.setUp()
                self.started()
                self.card("l1", status)
                self.stopped("任务卡已经是「%s」" % status)
                self.tearDown()
        self.setUp()

    def test_hours_up(self):
        self.started(hours=1)
        self.stopped("超过了申请的 1 小时", now=NOW + 3700)

    def test_process_gone(self):
        self.started()
        self.fake.state["gpu-a100"]["tmux"].clear()
        self.stopped("服务进程没了（tmux svc-proj-qwen9b 不在）", state="失败")
        self.assertEqual(len(self.todos()), 1)

    def test_a_name_used_again_keeps_the_old_record(self):
        self.started()
        open(os.path.join(self.board, "services", "qwen9b.done"), "w").close()
        self.run_(now=NOW + 600)
        self.request()
        self.run_(now=NOW + 900)
        self.fake.state["gpu-a100"]["healthy"].add("qwen9b")
        open(os.path.join(self.board, "services", "qwen9b.done"), "w").close()
        self.run_(now=NOW + 1200)
        self.run_(now=NOW + 1500)
        closed = sorted(os.listdir(os.path.join(self.board, "services", "closed")))
        self.assertEqual(len([f for f in closed if f.endswith(".status.json")]), 2)

    def test_only_its_own_sessions(self):
        """T5.8"""
        self.fake.state["gpu-a100"]["tmux"].update({"svc-proj-qwen9b-old", "monitor-bench-vllm-qwen9b", "rc-proj"})
        self.started()
        open(os.path.join(self.board, "services", "qwen9b.done"), "w").close()
        self.run_(now=NOW + 600)
        self.assertEqual(self.fake.killed, [("gpu-a100", "svc-proj-qwen9b")])
        self.assertEqual(self.fake.state["gpu-a100"]["tmux"], {"svc-proj-qwen9b-old", "monitor-bench-vllm-qwen9b", "rc-proj"})


class Passes(Base):
    def test_unreachable_server(self):
        """T5.9: nothing is started or stopped there; the status says so."""
        self.request(server="gpu-h100")
        self.run_()
        self.assertEqual(self.status()["server"], "gpu-h100")
        self.fake.state["gpu-h100"]["up"] = False
        open(os.path.join(self.board, "services", "qwen9b.done"), "w").close()
        self.run_(now=NOW + 300)
        self.assertEqual((self.status()["state"], self.status()["note"]), ("启动中", "gpu-h100 这一轮连不上"))
        self.assertEqual(self.fake.killed, [])
        self.request("other", server="gpu-h100")
        self.run_(now=NOW + 600)
        self.assertEqual((self.status("other")["state"], self.status("other")["reason"]), ("排队", "gpu-h100 连不上"))
        self.fake.state["gpu-h100"]["up"] = True
        self.run_(now=NOW + 900)
        self.assertEqual(self.fake.killed, [("gpu-h100", "svc-proj-qwen9b")])      # back: stopped as asked

    def test_one_connection_per_server_and_none_without_services(self):
        self.run_()
        self.assertEqual(self.fake.calls, [])                                       # no requests anywhere: no ssh
        self.request()
        self.request("second")
        self.run_()
        probes = [c for c in self.fake.calls]
        self.assertEqual(sorted(probes[:2]), ["gpu-a100", "gpu-h100"])              # one probe each, then two starts
        self.assertEqual(len(self.fake.started), 2)
        self.assertEqual(sorted(s["script"].split("CUDA_VISIBLE_DEVICES=")[1][0] for s in self.fake.started), ["0", "1"])

    def test_a_pass_still_running_is_left_alone(self):
        """T5.11"""
        self.request()
        os.makedirs(self.cfg["state_dir"], exist_ok=True)
        with open(os.path.join(self.cfg["state_dir"], "serve.lock"), "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            self.assertEqual([c["what"] for c in self.run_()], ["上一轮还没跑完，这一轮跳过"])
        self.assertEqual(self.fake.calls, [])
        self.run_()
        self.assertEqual(len(self.fake.started), 1)


if __name__ == "__main__":
    unittest.main()
