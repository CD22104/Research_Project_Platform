"""HTTP tests: runs server.py on a throwaway unix socket. Run: python3 -m unittest -v"""
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, "fixtures", "projects")


class UnixHTTP(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=10)
        self.sock_path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.settimeout(10)
        self.sock.connect(self.sock_path)


def write_config(tmp, root, servers=()):
    cfg = os.path.join(tmp, "servers.json")
    with open(cfg, "w") as f:
        json.dump({"socket": os.path.join(tmp, "run", "t.sock"), "servers": list(servers),
                   "projects_root": root, "projects_interval_seconds": 3600, "state_dir": os.path.join(tmp, "state")}, f)
    return cfg


class Liveness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        os.environ["GPUBOARD_CONFIG"] = write_config(cls.tmp, cls.tmp)
        sys.path.insert(0, REPO)
        import server
        cls.S = server

    @classmethod
    def tearDownClass(cls):
        del os.environ["GPUBOARD_CONFIG"]
        shutil.rmtree(cls.tmp)

    def srv(self, procs_by_gpu, status="ok"):
        return {"status": status, "gpus": [{"index": i, "procs": procs} for i, procs in procs_by_gpu.items()]}

    def test_cases(self):
        L, me = self.S.liveness, "dchen"
        run = {"server": "xtrah200", "gpus": [2, 3], "pid": 999999}
        other = [{"pid": 11, "user": "someone"}]
        self.assertEqual(L(run, self.srv({2: other, 3: []}), me), "maybe_interrupted")
        self.assertEqual(L(run, self.srv({0: [{"pid": 999999, "user": "dchen"}]}), me), "running")  # pid anywhere
        self.assertEqual(L(run, self.srv({3: [{"pid": 5, "user": "dchen"}]}), me), "running")  # child on its GPU
        self.assertEqual(L(run, self.srv({0: [{"pid": 5, "user": "dchen"}]}), me), "maybe_interrupted")  # other GPU
        self.assertEqual(L(run, self.srv({2: []}, status="error"), me), "unknown")  # server not reachable
        self.assertEqual(L(run, None, me), "unknown")                               # server not polled
        self.assertEqual(L(dict(run, gpus=[]), self.srv({}), me), "unknown")        # no GPUs: can't tell
        self.assertEqual(L(dict(run, gpus=None, pid=None), self.srv({}), me), "unknown")
        self.assertEqual(L({"server": "x", "gpus": ["2"], "pid": "999999"},
                           self.srv({2: [{"pid": 999999, "user": "x"}]}), me), "running")  # strings tolerated


class Http(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.root = os.path.join(cls.tmp, "projects")
        shutil.copytree(FIXTURES, cls.root, symlinks=True)
        secret = os.path.join(cls.tmp, "secret.png")
        with open(secret, "w") as f:
            f.write("outside")
        r001 = os.path.join(cls.root, "demo", "board", "runs", "r001")
        os.symlink(secret, os.path.join(r001, "leak.png"))
        with open(os.path.join(r001, "big.png"), "wb") as f:
            f.truncate(11 * 1024 * 1024)  # sparse, over the 10 MB limit
        cfg = write_config(cls.tmp, cls.root)
        cls.sock = os.path.join(cls.tmp, "run", "t.sock")
        cls.log = open(os.path.join(cls.tmp, "server.log"), "w")
        cls.proc = subprocess.Popen([sys.executable, os.path.join(REPO, "server.py")],
                                    env=dict(os.environ, GPUBOARD_CONFIG=cfg), stdout=cls.log, stderr=cls.log)
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                if cls.get_json("/api/projects")["scan"]["finished"]:
                    break
            except OSError:
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError("server did not finish its first scan")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(5)
        cls.log.close()
        shutil.rmtree(cls.tmp)

    @classmethod
    def get(cls, path):
        c = UnixHTTP(cls.sock)
        try:
            c.request("GET", path)
            r = c.getresponse()
            return r.status, dict(r.getheaders()), r.read()
        finally:
            c.close()

    @classmethod
    def post(cls, path, body, headers=None):
        h = {"Host": "localhost:8765", "Content-Type": "application/json", "X-Board-Write": "1",
             "Origin": "http://localhost:8765"}
        h.update(headers or {})
        h = {k: v for k, v in h.items() if v is not None}
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        c = UnixHTTP(cls.sock)
        try:
            c.putrequest("POST", path, skip_host=True)
            for k, v in h.items():
                c.putheader(k, v)
            c.putheader("Content-Length", str(len(data)))
            c.endheaders(data)
            r = c.getresponse()
            return r.status, r.read()
        finally:
            c.close()

    @classmethod
    def get_json(cls, path):
        status, _, body = cls.get(path)
        assert status == 200, (path, status)
        return json.loads(body)

    def test_projects_list(self):
        d = self.get_json("/api/projects")
        self.assertEqual([p["name"] for p in d["projects"]], ["broken", "demo", "minimal"])
        demo = d["projects"][1]
        self.assertEqual(demo["active_runs"][0]["id"], "r002")
        self.assertEqual(demo["active_runs"][0]["liveness"], "unknown")  # xtrah200 is not polled here
        self.assertIsNone(d["scan"]["error"])

    def test_project_detail(self):
        d = self.get_json("/api/project/demo")
        runs = {r["id"]: r for r in d["runs"]}
        self.assertEqual(runs["r002"]["liveness"], "unknown")
        self.assertIsNone(runs["r001"]["liveness"])  # finished runs are not checked
        self.assertEqual(len(d["attempts"]), 7)
        self.assertIn("scan", d)

    def test_unknown_or_invalid_project(self):
        for p in ["/api/project/nope", "/api/project/_shared", "/api/project/.hidden",
                  "/api/project/..%2Fdemo", "/api/project/", "/api/project/noboard"]:
            with self.subTest(p=p):
                self.assertEqual(self.get(p)[0], 404)

    def test_files_ok_and_headers(self):
        status, h, body = self.get("/files/demo/runs/r001/curve.svg")
        self.assertEqual((status, h["Content-Type"]), (200, "image/svg+xml"))
        self.assertIn("sandbox", h["Content-Security-Policy"])
        self.assertIn("default-src 'none'", h["Content-Security-Policy"])
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        self.assertEqual(self.get("/files/demo/runs/r001/tiny.png")[1]["Content-Type"], "image/png")
        status, h, body = self.get("/files/demo/plan.md")
        self.assertEqual((status, h["Content-Type"]), (200, "text/plain; charset=utf-8"))
        self.assertIn(b"<script>", body)  # served as inert text
        quoted = "/files/demo/attempts/2026-11-15-focal.md".replace("-", "%2D")
        self.assertEqual(self.get(quoted)[0], 200)  # percent-encoded paths are decoded first

    def test_files_rejected(self):
        for p in ["/files/demo/../minimal/board/project.json",
                  "/files/demo/%2e%2e/%2e%2e/minimal/board/project.json",
                  "/files/demo/runs/r001/..%2F..%2F..%2Fminimal%2Fboard%2Fproject.json",
                  "/files/demo/runs/r001/leak.png", "/files/demo/legacy-site/index.html",
                  "/files/demo/attempts/notes.txt", "/files/_shared/project.json",
                  "/files/demo//etc/passwd", "/files/demo/runs/r001/missing.png", "/files/demo"]:
            with self.subTest(p=p):
                self.assertEqual(self.get(p)[0], 404)
        self.assertEqual(self.get("/files/demo/runs/r001/big.png")[0], 413)

    def test_guide_and_entries(self):
        status, h, body = self.get("/guide.md")
        self.assertEqual((status, h["Content-Type"]), (200, "text/plain; charset=utf-8"))
        self.assertIn("rc-start", body.decode())
        self.assertIsInstance(self.get_json("/api/projects")["entries"], list)

    def demo_summary(self):
        return next(p for p in self.get_json("/api/projects")["projects"] if p["name"] == "demo")

    def test_reply_to_todo(self):
        status, body = self.post("/api/answer", {"project": "demo", "id": "t3", "answer": "现在补", "text": "用 h200\x07"})
        self.assertEqual(status, 200, body)
        t3 = next(t for t in self.demo_summary()["todos"] if t["id"] == "t3")
        self.assertEqual((t3["answer"]["answer"], t3["answer"]["text"]), ("现在补", "用 h200"))   # visible at once, control chars dropped
        self.assertEqual(t3["options"], ["现在补", "先不补"])
        with open(os.path.join(self.root, "demo", "board", "answers.jsonl"), encoding="utf-8") as f:
            rows = [json.loads(l) for l in f]
        self.assertEqual(rows[-1]["id"], "t3")
        self.assertEqual(rows[-1]["by"], "console")
        self.post("/api/answer", {"project": "demo", "id": "t3", "answer": "先不补"})          # changing a reply: last row wins
        t3 = next(t for t in self.demo_summary()["todos"] if t["id"] == "t3")
        self.assertEqual(t3["answer"]["answer"], "先不补")

    def test_confirm_attempt(self):
        status, body = self.post("/api/answer", {"project": "demo", "id": "a:2026-11-15-focal", "answer": "失败", "text": ""})
        self.assertEqual(status, 200, body)
        focal = next(a for a in self.demo_summary()["pending"] if a["id"] == "2026-11-15-focal")
        self.assertEqual((focal["answer"]["answer"], focal["proposed"]), ("失败", "失败"))
        self.assertEqual(self.post("/api/answer", {"project": "demo", "id": "a:2026-11-15-focal", "answer": "大成功"})[0], 400)
        self.assertEqual(self.post("/api/answer", {"project": "demo", "id": "a:2026-11-10-lr-sweep", "answer": "成功"})[0], 404)

    def test_reply_rejected(self):
        ok = {"project": "demo", "id": "t2", "answer": None, "text": "好"}
        cases = [
            ({}, {"X-Board-Write": None}, 403),                       # plain cross-site form posts lack the header
            ({}, {"Host": "evil.example:8765"}, 403),                 # DNS rebinding
            ({}, {"Origin": "http://evil.example"}, 403),             # another site's page
            ({}, {"Sec-Fetch-Site": "cross-site"}, 403),
            ({}, {"Content-Type": "text/plain"}, 403),
            ({"id": "t1"}, {}, 404),                                  # already done
            ({"project": "nope"}, {}, 404),
            ({"project": "../demo"}, {}, 404),
            ({"text": "", "answer": None}, {}, 400),                  # empty
            ({"answer": "随便"}, {}, 400),                            # t2 has no options
            ({"text": "x" * 2001}, {}, 400),
        ]
        for change, headers, code in cases:
            with self.subTest(change=change, headers=headers):
                self.assertEqual(self.post("/api/answer", dict(ok, **change), headers)[0], code)
        self.assertEqual(self.post("/api/answer", b"x" * 20000)[0], 413)
        self.assertEqual(self.post("/api/answer", b"[1]")[0], 400)
        self.assertEqual(self.post("/api/other", ok)[0], 404)
        self.assertEqual(self.post("/api/answer", ok, {"Origin": None})[0], 200)  # no Origin (same-origin GET-like) is fine

    def test_rename(self):
        self.assertEqual(self.post("/api/name", {"project": "demo", "name": "演示"})[0], 200)
        row = self.demo_summary()
        self.assertEqual((row["title"], row["full_title"]), ("演示", "演示项目"))
        p = self.get_json("/api/project/demo")["project"]
        self.assertEqual((p["title"], p["full_title"]), ("演示", "演示项目"))
        self.assertEqual(self.post("/api/name", {"project": "demo", "name": "x" * 25})[0], 400)
        self.assertEqual(self.post("/api/name", {"project": "nope", "name": "x"})[0], 404)
        self.assertEqual(self.post("/api/name", {"project": "demo", "name": ""})[0], 200)  # back to the original
        self.assertEqual(self.demo_summary()["title"], "演示项目")
        self.assertNotIn("full_title", self.demo_summary())

    def test_gpu_api_unchanged(self):
        d = self.get_json("/api/status")
        self.assertEqual(d["servers"], [])
        self.assertTrue(d["me"])                                     # the GPU page marks this user's cards
        self.assertEqual(self.get("/")[0], 200)


if __name__ == "__main__":
    unittest.main()
