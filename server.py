#!/usr/bin/env python3
"""GPU 看板：通过 SSH 轮询各服务器的 nvidia-smi，在 http://localhost:8765 展示。

只依赖 Python 标准库和本机的 ssh（使用 ~/.ssh/config 与免密密钥）。
服务器列表在 servers.json 里改。
"""
import json
import os
import socket
import socketserver
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "servers.json"), encoding="utf-8") as f:
    CFG = json.load(f)

PORT = int(CFG.get("port", 8765))
INTERVAL = int(CFG.get("interval_seconds", 30))
JUMP = CFG.get("jump_host") or None
SERVERS = CFG["servers"]
# If set, listen only on this unix socket (no TCP port), so only its owner can connect
SOCKET = CFG.get("socket") or None

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
    except ValueError:
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


class Handler(BaseHTTPRequestHandler):
    def send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/status":
            with lock:
                body = json.dumps({"now": time.time(), "interval": INTERVAL,
                                   "servers": [state[s["name"]] for s in SERVERS]}).encode()
            self.send(200, "application/json", body)
        elif path == "/api/refresh":
            wake.set()
            wake.clear()
            self.send(200, "application/json", b"{}")
        elif path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                self.send(200, "text/html; charset=utf-8", f.read())
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
            sys.exit("已有看板实例在 %s 上运行，退出" % path)
        finally:
            probe.close()
    return UnixHTTPServer(path, Handler), "unix:" + path


if __name__ == "__main__":
    # Bind before starting pollers: a duplicate instance exits early, and the
    # temporary umask in server_bind cannot leak into ssh child processes
    httpd, where = make_server()
    for s in SERVERS:
        threading.Thread(target=poll, args=(s,), daemon=True).start()
    print("GPU 看板已启动: %s" % where, flush=True)
    httpd.serve_forever()
