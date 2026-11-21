#!/usr/bin/env python3
"""CD的研究控制台：各服务器的 GPU 占用（SSH 轮询 nvidia-smi）和共享盘上各项目的进度记录。

只依赖 Python 标准库和本机的 ssh（使用 ~/.ssh/config 与免密密钥）。
服务器列表、监听方式（unix socket 或本机端口）、项目根目录在 servers.json 里改。
平台名显示在页面上的那一处在 common.js 的 APP_NAME。
"""
import json
import os
import pwd
import socket
import socketserver
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

import projects

HERE = os.path.dirname(os.path.abspath(__file__))
# GPUBOARD_CONFIG lets tests run the server against a throwaway config
with open(os.environ.get("GPUBOARD_CONFIG") or os.path.join(HERE, "servers.json"), encoding="utf-8") as f:
    CFG = json.load(f)

PORT = int(CFG.get("port", 8765))
INTERVAL = int(CFG.get("interval_seconds", 30))
JUMP = CFG.get("jump_host") or None
SERVERS = CFG["servers"]
# If set, listen only on this unix socket (no TCP port), so only its owner can connect
SOCKET = CFG.get("socket") or None
PROJECTS_ROOT = os.path.expanduser(CFG.get("projects_root") or "/shared/ssd/dchen")
PROJECTS_INTERVAL = int(CFG.get("projects_interval_seconds", 60))
ME = pwd.getpwuid(os.getuid()).pw_name  # same login on every server
project_cache = projects.ProjectCache(PROJECTS_ROOT, PROJECTS_INTERVAL)

# 在远端执行的只读脚本：GPU 列表 / GPU 上的进程 / 这些进程的用户
REMOTE = r"""
echo @@@BEGIN
nvidia-smi --query-gpu=index,uuid,name,memory.used,memory.total,utilization.gpu,temperature.gpu --format=csv,noheader,nounits || exit 3
echo @@@
nvidia-smi --query-compute-apps=gpu_uuid,pid,used_memory --format=csv,noheader,nounits
echo @@@
P=$(nvidia-smi --query-compute-apps=pid --format=csv,noheader | sort -u | paste -sd, -)
[ -n "$P" ] && ps -o pid=,user:32=,etimes=,args= -p "$P"
exit 0
"""

SSH_OPTS = [
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=10",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ControlMaster=auto",
    "-o", "ControlPersist=180",
    "-o", "LogLevel=ERROR",
]

state = {s["name"]: {"name": s["name"], "host": s["host"], "status": "pending"} for s in SERVERS}
lock = threading.Lock()
wake = threading.Event()


def num(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return None


def ssh(host, via_jump):
    cmd = ["ssh"] + SSH_OPTS + ["-o", "ControlPath=/tmp/gpub-%s-%%C" % ("j" if via_jump else "d")]
    if via_jump:
        cmd += ["-J", JUMP]
    cmd += [host, "bash -s"]
    try:
        p = subprocess.run(cmd, input=REMOTE, capture_output=True, text=True, timeout=45)
    except subprocess.TimeoutExpired:
        return None, "连接超时"
    if p.returncode != 0:
        err = (p.stderr.strip().splitlines() or ["ssh 退出码 %d" % p.returncode])[-1]
        return None, err
    return p.stdout, None


def parse(out):
    out = out.split("@@@BEGIN", 1)[-1]  # 丢掉登录横幅等杂项输出
    parts = (out.split("@@@") + ["", ""])[:3]
    gpus, by_uuid = [], {}
    for line in parts[0].strip().splitlines():
        c = [x.strip() for x in line.split(",")]
        if len(c) < 7:
            continue
        g = {"index": num(c[0]), "name": c[2].replace("NVIDIA ", ""), "mem_used": num(c[3]),
             "mem_total": num(c[4]), "util": num(c[5]), "temp": num(c[6]), "procs": []}
        gpus.append(g)
        by_uuid[c[1]] = g
    procs = {}
    for line in parts[2].strip().splitlines():
        c = line.split(None, 3)
        if len(c) >= 3:
            procs[c[0]] = {"user": c[1], "elapsed": num(c[2]), "cmd": (c[3] if len(c) > 3 else "")[:300]}
    for line in parts[1].strip().splitlines():
        c = [x.strip() for x in line.split(",")]
        if len(c) < 3 or c[0] not in by_uuid:
            continue
        info = procs.get(c[1], {})
        by_uuid[c[0]]["procs"].append({"pid": num(c[1]), "mem": num(c[2]),
                                       "user": info.get("user", "未知"),
                                       "elapsed": info.get("elapsed"), "cmd": info.get("cmd", "")})
    return gpus


def poll(server):
    name, host = server["name"], server["host"]
    via_jump = False
    while True:
        modes = [via_jump] + ([not via_jump] if JUMP else [])
        out = err = None
        for m in modes:
            out, e = ssh(host, m)
            if out is not None:
                via_jump = m
                break
            err = err or e
        with lock:
            cur = state[name]
            if out is not None:
                state[name] = {"name": name, "host": host, "status": "ok", "gpus": parse(out),
                               "via": "jump" if via_jump else "direct", "updated": time.time()}
            else:
                cur = dict(cur, status="error", error=err, failed_at=time.time())
                state[name] = cur  # 保留上一次成功的数据，页面上标记为过期
        wake.wait(INTERVAL)


def liveness(run, srv, me):
    """For a run recorded as 运行中: "running", "maybe_interrupted" or "unknown".

    srv is the latest GPU poll of run["server"] (None if that name is not
    polled). Polls only see GPU processes, so runs without GPUs are unknown.
    The recorded pid may be a launcher whose children hold the GPUs, so our
    own process on one of the run's GPUs also counts as running.
    """
    gpus = run.get("gpus")
    if not isinstance(gpus, list) or not gpus or not srv or srv.get("status") != "ok":
        return "unknown"
    want = {num(g) for g in gpus} - {None}
    pid = num(run.get("pid"))
    for g in srv.get("gpus") or []:
        for p in g.get("procs") or []:
            if (pid is not None and p.get("pid") == pid) or (g.get("index") in want and p.get("user") == me):
                return "running"
    return "maybe_interrupted"


def run_liveness(run):
    name = run.get("server")
    with lock:
        srv = state.get(name) if isinstance(name, str) else None
    return liveness(run, srv, ME)


_rc = {"at": 0.0, "names": []}


def rc_entries():
    """Projects whose resident entry (tmux session rc-<project>, started by ~/bin/rc-start) runs here.
    Cached for 15 s; any tmux trouble just means "none known"."""
    now = time.time()
    if now - _rc["at"] > 15:
        try:
            out = subprocess.run(["tmux", "list-sessions", "-F", "#{session_name}"],
                                 capture_output=True, text=True, timeout=5).stdout
            _rc["names"] = sorted(s[3:] for s in out.split() if s.startswith("rc-"))
        except (OSError, subprocess.SubprocessError):
            _rc["names"] = []
        _rc["at"] = now
    return _rc["names"]


def api_projects():
    snap, status = project_cache.snapshot()
    rows = []
    for name in sorted(snap):
        row = projects.summary(snap[name])
        row["active_runs"] = [dict(r, liveness=run_liveness(r)) for r in row["active_runs"]]
        rows.append(row)
    return {"now": time.time(), "scan": status, "projects": rows, "todos": project_cache.global_todos(),
            "entries": rc_entries()}


def api_project(name):
    snap, status = project_cache.snapshot()
    data = snap.get(name) if projects.valid_name(name) else None
    if data is None:
        return None
    out = dict(data, scan=status, now=time.time())  # shallow copy: cached snapshots are never mutated
    out["runs"] = [dict(r, liveness=run_liveness(r["run"]) if r["run"].get("status") == "运行中" else None)
                   for r in data["runs"]]
    return out


# The board's own pages, read from the repo on each request
HTML, JS = "text/html; charset=utf-8", "text/javascript; charset=utf-8"
STATIC = {"/": ("index.html", HTML), "/index.html": ("index.html", HTML),
          "/project.html": ("project.html", HTML), "/common.js": ("common.js", JS), "/app.js": ("app.js", JS),
          "/guide.md": ("docs/guide.md", "text/plain; charset=utf-8")}

# Served board files must never run code, even when opened directly (SVG can carry scripts)
FILE_HEADERS = {"X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox"}


class Handler(BaseHTTPRequestHandler):
    def send(self, code, ctype, body, headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, obj):
        self.send(200, "application/json; charset=utf-8", json.dumps(obj, ensure_ascii=False).encode())

    def send_board_file(self, rest):
        name, _, rel = rest.partition("/")
        path = projects.safe_board_file(PROJECTS_ROOT, name, rel)
        if path is None:
            return self.send(404, "text/plain", b"not found")
        try:
            if os.stat(path).st_size > projects.MAX_FILE_BYTES:
                return self.send(413, "text/plain", b"file too large")
            data = projects._read_bytes(path, projects.MAX_FILE_BYTES + 1)
        except FileNotFoundError:
            return self.send(404, "text/plain", b"not found")
        except OSError as e:  # e.g. EIO on the shared disk
            return self.send(503, "text/plain", ("read failed: %s" % e).encode())
        if len(data) > projects.MAX_FILE_BYTES:
            return self.send(413, "text/plain", b"file too large")
        ctype = projects.FILE_TYPES[os.path.splitext(path)[1].lower()]
        self.send(200, ctype, data, FILE_HEADERS)

    def do_GET(self):
        path = unquote(self.path.split("?")[0])
        if path == "/api/projects":
            self.send_json(api_projects())
        elif path.startswith("/api/project/"):
            data = api_project(path[len("/api/project/"):])
            if data is None:
                self.send(404, "text/plain", b"no such project")
            else:
                self.send_json(data)
        elif path.startswith("/files/"):
            self.send_board_file(path[len("/files/"):])
        elif path == "/api/status":
            with lock:
                body = json.dumps({"now": time.time(), "interval": INTERVAL,
                                   "servers": [state[s["name"]] for s in SERVERS]}).encode()
            self.send(200, "application/json", body)
        elif path == "/api/refresh":
            wake.set()
            wake.clear()
            self.send(200, "application/json", b"{}")
        elif path in STATIC:
            name, ctype = STATIC[path]
            with open(os.path.join(HERE, name), "rb") as f:
                self.send(200, ctype, f.read())
        else:
            self.send(404, "text/plain", b"not found")

    def log_message(self, *a):
        pass


class UnixHTTPServer(ThreadingHTTPServer):
    address_family = socket.AF_UNIX

    def server_bind(self):
        # HTTPServer.server_bind expects (host, port); bind the path directly
        old = os.umask(0o177)  # socket file is created 0600
        try:
            socketserver.TCPServer.server_bind(self)
        finally:
            os.umask(old)
        self.server_name, self.server_port = "localhost", 0


def make_server():
    """Return (server, description of where it listens)."""
    if not SOCKET:
        return ThreadingHTTPServer(("127.0.0.1", PORT), Handler), "http://localhost:%d" % PORT
    path = os.path.expanduser(SOCKET)
    d = os.path.dirname(path)
    os.makedirs(d, mode=0o700, exist_ok=True)
    os.chmod(d, 0o700)  # makedirs mode is umask-filtered and skipped if d already exists
    if os.path.exists(path):
        probe = socket.socket(socket.AF_UNIX)
        try:
            probe.connect(path)
        except OSError:
            os.unlink(path)  # stale socket left by a previous run
        else:
            sys.exit("已有控制台实例在 %s 上运行，退出" % path)
        finally:
            probe.close()
    return UnixHTTPServer(path, Handler), "unix:" + path


if __name__ == "__main__":
    # Bind before starting pollers: a duplicate instance exits early, and the
    # temporary umask in server_bind cannot leak into ssh child processes
    httpd, where = make_server()
    for s in SERVERS:
        threading.Thread(target=poll, args=(s,), daemon=True).start()
    project_cache.start()
    print("CD的研究控制台已启动: %s" % where, flush=True)
    httpd.serve_forever()
