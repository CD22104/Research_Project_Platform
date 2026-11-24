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
from urllib.parse import unquote, urlsplit

import projects
import sessions

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
_sc = CFG.get("sessions") or {}
session_watch = sessions.SessionWatch(PROJECTS_ROOT, claude_bin=_sc.get("claude_bin"), transcripts=_sc.get("transcripts"),
                                      interval=int(_sc.get("interval_seconds", 60)))
SESSIONS_ON = _sc.get("enabled", True)

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
    """What the GPU polls say about a run recorded as 运行中, as a probe for projects.run_state():
    "running" (its process is on its GPUs), "absent" (the server answered, the process is not there),
    "unreachable" (the server's last poll failed) or None (no GPUs recorded, or the server is not polled).

    srv is the latest GPU poll of run["server"]. The recorded pid may be a launcher whose children
    hold the GPUs, so our own process on one of the run's GPUs also counts as running.
    """
    gpus = run.get("gpus")
    if not srv or not isinstance(gpus, list) or not gpus:
        return None
    if srv.get("status") != "ok":
        return "unreachable"
    want = {num(g) for g in gpus} - {None}
    pid = num(run.get("pid"))
    for g in srv.get("gpus") or []:
        for p in g.get("procs") or []:
            if (pid is not None and p.get("pid") == pid) or (g.get("index") in want and p.get("user") == me):
                return "running"
    return "absent"


def run_liveness(run):
    name = run.get("server")
    with lock:
        srv = state.get(name) if isinstance(name, str) else None
    return liveness(run, srv, ME)


def with_state(row, run, heartbeat, now):
    """A run row with what the GPU polls say ("liveness", a probe) and what to show ("state":
    运行中 / 状态未知 / 已结束 / 已中断, see projects.run_state)."""
    probe = run_liveness(run) if run.get("status") == "运行中" else None
    return dict(row, liveness=probe, state=projects.run_state(run, heartbeat, probe, now))


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


# ---------------------------------------------------------------- what the console writes
# Only these, all from the console page itself (see write_refused):
#   replies to to-dos -> <project>/board/answers.jsonl (append only; sessions act on them when told to)
#   document marks    -> <project>/board/marks.jsonl (append only; 看过 / 确认, sessions may read them)
#   paper ticks       -> the same marks.jsonl, rows with "paper" instead of "file" (读过 or null)
#   display names     -> state/names.json here on a100 (console-only setting, not in the project)
STATE_DIR = os.path.expanduser(CFG.get("state_dir") or os.path.join(HERE, "state"))
NAMES_FILE = os.path.join(STATE_DIR, "names.json")
MAX_POST = 16 * 1024
MAX_REPLY = 2000
MAX_NAME = 24
write_lock = threading.Lock()


def _load_names():
    try:
        with open(NAMES_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return {k: v for k, v in d.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


names = _load_names()


def _clean(text, keep_newlines):
    """Drop control characters (keep newlines and tabs in replies)."""
    ok = "\n\t" if keep_newlines else ""
    return "".join(c for c in text if c in ok or (ord(c) >= 32 and ord(c) != 127)).strip()


def _utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def post_answer(body):
    """Append one reply to a to-do or to a 待确认 attempt. -> (http code, response object)"""
    name, tid, answer, text = body.get("project"), body.get("id"), body.get("answer"), body.get("text") or ""
    mode = body.get("mode")
    if not (isinstance(name, str) and isinstance(tid, str) and 0 < len(tid) <= 200 and isinstance(text, str)
            and (answer is None or isinstance(answer, str)) and mode in (None, "直接处理")):
        return 400, {"error": "请求格式不对"}
    text = _clean(text, True)
    if len(text) > MAX_REPLY:
        return 400, {"error": "补充说明太长（最多 %d 字）" % MAX_REPLY}
    if not answer and not text:
        return 400, {"error": "回复是空的"}
    snap, _ = project_cache.snapshot()
    if name == projects.SHARED:
        choices = next((projects.todo_options(t) for t in project_cache.global_todos()
                        if str(t.get("id")) == tid and not t.get("done")), None)
    elif projects.valid_name(name) and name in snap:
        if tid.startswith("a:"):
            choices = next((list(projects.ATTEMPT_CHOICES) for a in snap[name].get("attempts") or []
                            if a.get("meta", {}).get("id") == tid[2:] and "结论待确认" in (a.get("needs_input") or [])), None)
        elif tid.startswith("d:"):  # adopting a decision: only while its file still says 候选
            choices = next((list(projects.DECISION_CHOICES) for r in snap[name].get("decisions") or []
                            if r.get("meta", {}).get("id") == tid[2:] and r["meta"].get("status") in (None, "候选")), None)
        else:
            choices = next((projects.todo_options(t) for t in snap[name].get("todos") or []
                            if str(t.get("id")) == tid and not t.get("done")), None)
    else:
        return 404, {"error": "没有这个项目"}
    if choices is None:
        return 404, {"error": "这条已经办完或不存在，刷新看看"}
    if answer is not None and answer not in choices:
        return 400, {"error": "选项不在这条待办的选项里"}
    row = {"id": tid, "answer": answer, "text": text, "at": _utc_now(), "by": "console"}
    if mode and name != projects.SHARED:  # 直接处理: board dispatch starts a session for it within minutes
        row["mode"] = mode
    err = append_row(name, "answers", row)
    return (503, {"error": err}) if err else (200, {"ok": True, "row": row})


def append_row(name, which, row):
    """Append one row to the project's answers.jsonl or marks.jsonl and show it at once. -> error text or None"""
    line = (json.dumps(row, ensure_ascii=False) + "\n").encode()
    with write_lock:
        try:
            fd = os.open(project_cache.console_path(name, which), os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o644)
            try:
                os.write(fd, line)
            finally:
                os.close(fd)
        except OSError as e:
            return "写入失败：%s" % projects._err(e)
        try:
            project_cache.reload(name, which)
        except OSError:
            pass  # written; the next scan picks it up
    return None


def post_mark(body):
    """Mark a document as 看过 or 确认 (or clear the mark with null). The document's mtime is kept,
    so an edit after the mark shows as 有更新."""
    name, rel, mark = body.get("project"), body.get("file"), body.get("mark")
    if not (isinstance(name, str) and isinstance(rel, str) and (mark is None or mark in projects.MARKS)):
        return 400, {"error": "请求格式不对"}
    snap, _ = project_cache.snapshot()
    if not (projects.valid_name(name) and name in snap):
        return 404, {"error": "没有这个项目"}
    data = snap[name]
    docs = ([data["plan"]] if data.get("plan") else []) + list(data.get("docs") or []) + list(data.get("figures") or [])
    doc = next((x for x in docs if x.get("file") == rel), None)
    if doc is None:
        return 404, {"error": "没有这篇文档，刷新看看"}
    row = {"file": rel, "mark": mark, "at": _utc_now(), "mtime": doc.get("mtime"), "by": "console"}
    err = append_row(name, "marks", row)
    return (503, {"error": err}) if err else (200, {"ok": True, "row": row})


def post_paper_mark(body):
    """Tick a paper on the project's reading list (board/papers.jsonl) as 读过, or clear the tick with null."""
    name, pid, mark = body.get("project"), body.get("paper"), body.get("mark")
    if not (isinstance(name, str) and isinstance(pid, str) and 0 < len(pid) <= 200
            and (mark is None or mark in projects.PAPER_MARKS)):
        return 400, {"error": "请求格式不对"}
    snap, _ = project_cache.snapshot()
    if not (projects.valid_name(name) and name in snap):
        return 404, {"error": "没有这个项目"}
    if not any(p.get("id") == pid for p in snap[name].get("papers") or []):
        return 404, {"error": "清单里没有这篇论文，刷新看看"}
    row = {"paper": pid, "mark": mark, "at": _utc_now(), "by": "console"}
    err = append_row(name, "marks", row)
    return (503, {"error": err}) if err else (200, {"ok": True, "row": row})


def post_name(body):
    """Set (or clear, with an empty name) the name the console shows for a project."""
    name, label = body.get("project"), body.get("name")
    if not (isinstance(name, str) and isinstance(label, str)):
        return 400, {"error": "请求格式不对"}
    snap, _ = project_cache.snapshot()
    if not (projects.valid_name(name) and name in snap):
        return 404, {"error": "没有这个项目"}
    label = _clean(label, False)
    if len(label) > MAX_NAME:
        return 400, {"error": "名字最多 %d 个字" % MAX_NAME}
    with write_lock:
        new = dict(names)
        if label:
            new[name] = label
        else:
            new.pop(name, None)
        try:
            os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
            tmp = NAMES_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(new, f, ensure_ascii=False, indent=1)
            os.replace(tmp, NAMES_FILE)
        except OSError as e:
            return 503, {"error": "保存失败：%s" % projects._err(e)}
        names.clear()
        names.update(new)
    return 200, {"ok": True, "name": label}


def renamed(project, name):
    """project.json fields with the console name applied: title becomes the user's name, full_title keeps the original."""
    label = names.get(name)
    if not label:
        return project
    return dict(project, title=label, full_title=project.get("title") or name)


def api_projects():
    snap, status = project_cache.snapshot()
    rows = []
    for name in sorted(snap):
        row = projects.summary(snap[name])
        now = time.time()
        row["active_runs"] = [with_state(r, r, r.get("heartbeat"), now) for r in row["active_runs"]]
        if names.get(name):
            row["full_title"], row["title"] = row["title"], names[name]
        rows.append(row)
    return {"now": time.time(), "scan": status, "projects": rows, "todos": project_cache.global_todos(),
            "entries": rc_entries()}


def api_project(name):
    snap, status = project_cache.snapshot()
    data = snap.get(name) if projects.valid_name(name) else None
    if data is None:
        return None
    out = dict(data, scan=status, now=time.time())  # shallow copy: cached snapshots are never mutated
    out["project"] = renamed(data.get("project") or {}, name)
    now = time.time()
    out["runs"] = [with_state(r, r["run"], r.get("heartbeat"), now) for r in data["runs"]]
    out["decision_states"] = projects.decision_states(data.get("decisions") or [], data.get("answers") or {})
    out["papers_elsewhere"] = projects.papers_read_elsewhere(snap, name)
    return out


def api_sessions():
    """Claude sessions on this machine with the project and task card each one works on (metadata only)."""
    snap = session_watch.snapshot()
    projs, _ = project_cache.snapshot()
    task_of = {}  # (project, session id or name) -> task id, from the task cards' sessions lists
    for pname, data in projs.items():
        for t in data.get("tasks") or []:
            for s in projects._list(t["meta"].get("sessions")):
                task_of[(pname, str(s))] = t["meta"].get("id")
    rows = []
    for s in snap.get("sessions") or []:
        p = s.get("project")
        task = task_of.get((p, s["id"])) or task_of.get((p, s.get("name")))
        if not task and p and isinstance(s.get("name"), str) and "·" in s["name"]:
            head, _, tail = s["name"].partition("·")
            ids = {t["meta"].get("id") for t in (projs.get(p) or {}).get("tasks") or []}
            task = tail if head == p and tail in ids else None
        rows.append(dict(s, task=task))
    return {"now": time.time(), "at": snap.get("at"), "error": snap.get("error"), "enabled": SESSIONS_ON, "sessions": rows}


# The board's own pages, read from the repo on each request
HTML, JS = "text/html; charset=utf-8", "text/javascript; charset=utf-8"
STATIC = {"/": ("index.html", HTML), "/index.html": ("index.html", HTML),
          "/project.html": ("project.html", HTML), "/common.js": ("common.js", JS), "/app.js": ("app.js", JS),
          "/work.js": ("work.js", JS), "/design.js": ("design.js", JS),
          "/guide.md": ("docs/guide.md", "text/plain; charset=utf-8")}

# Served board files must never run code, even when opened directly (SVG can carry scripts)
FILE_HEADERS = {"X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox"}
# Figures (board/figures/*.html) may run their own inline scripts, but in a sandbox without an origin:
# they cannot read the console's page or storage, call its API, or load anything from the network
FIGURE_HEADERS = {"X-Content-Type-Options": "nosniff",
                  "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                                             "img-src data: blob:; font-src data:; sandbox allow-scripts"}


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
        ext = os.path.splitext(path)[1].lower()
        self.send(200, projects.FILE_TYPES[ext], data, FIGURE_HEADERS if ext == ".html" else FILE_HEADERS)

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
                body = json.dumps({"now": time.time(), "interval": INTERVAL, "me": ME,
                                   "servers": [state[s["name"]] for s in SERVERS]}).encode()
            self.send(200, "application/json", body)
        elif path == "/api/sessions":
            self.send_json(api_sessions())
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

    def write_refused(self):
        """Why a write request is refused, or None. Writes must come from the console page itself:
        a custom header and a JSON body (other sites' pages cannot send these without a CORS
        preflight, which is never answered), a local Host (no DNS rebinding) and a same-origin Origin."""
        host = self.headers.get("Host") or ""
        hostname = host.rsplit("]", 1)[0] + "]" if host.startswith("[") else host.split(":")[0]
        if hostname not in ("localhost", "127.0.0.1", "[::1]"):
            return "host"
        if self.headers.get("X-Board-Write") != "1":
            return "header"
        if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
            return "content type"
        origin = self.headers.get("Origin")
        if origin is not None and urlsplit(origin).netloc != host:
            return "origin"
        if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin"):
            return "fetch site"
        return None

    def do_POST(self):
        path = self.path.split("?")[0]
        handler = {"/api/answer": post_answer, "/api/name": post_name, "/api/mark": post_mark,
                   "/api/paper-mark": post_paper_mark}.get(path)
        if handler is None:
            return self.send(404, "text/plain", b"not found")
        why = self.write_refused()
        if why:
            return self.send(403, "text/plain", ("refused: %s" % why).encode())
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if not 0 < n <= MAX_POST:
            return self.send(413, "text/plain", b"body too large or missing")
        try:
            body = json.loads(self.rfile.read(n))
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return self.send(400, "text/plain", b"bad json")
        code, out = handler(body)
        self.send(code, "application/json; charset=utf-8", json.dumps(out, ensure_ascii=False).encode())

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
    if SESSIONS_ON:
        session_watch.start()
    print("CD的研究控制台已启动: %s" % where, flush=True)
    httpd.serve_forever()
