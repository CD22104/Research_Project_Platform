"""board: command-line tools around the project boards (run on xtraa100; standard library only).

  board brief [DIR] [--hook]        the facts pack of the project at or above DIR (SessionStart hook)
  board check [DIR] [--file PATH] [--hook]
                                    record check; exit 2 with the problems (PostToolUse hook: only the edited file)
  board reconcile [--dry-run]       close runs whose process is gone, move waiting tasks on (cron, every 5 min)
  board task-begin TASK [--session ID]
                                    the conversation the user opened in the project's folder takes this card:
                                    same checks and worktree as task-start, prints the steps (exit 1 when refused)
  board task-start PROJECT TASK     start the session of one task card in the background (tmux task-<project>-<task>,
                                    Remote Control; for the dispatcher, or when the user asks for it)
  board task-stop PROJECT TASK      stop it
  board dispatch [--dry-run]        start a session for replies marked 直接处理 (cron, every 5 min)

The record format is docs/board-format.md. Only `reconcile` writes into project boards, and only two
things: the status of a run that is recorded as 运行中 but whose process is gone, and the status of
a task card that waits for runs that have all ended.
"""
import argparse
import datetime
import json
import os
import re
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import projects as P  # noqa: E402

CONFIG = os.environ.get("GPUBOARD_CONFIG") or os.path.join(HERE, "servers.json")
TMUX_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
PASSES = 2  # a run must look dead on this many consecutive passes before its record is changed


def load_config(path=CONFIG):
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    root = os.path.expanduser(cfg.get("projects_root") or "/shared/ssd/dchen")
    state = os.path.expanduser(cfg.get("state_dir") or os.path.join(HERE, "state"))
    servers = {s["name"]: s["host"] for s in cfg.get("servers") or []}
    return {"root": root, "state_dir": state, "servers": servers}


def utc(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_atomic(path, text):
    """Write a file in one step (temp file + rename): the shared disk is soft-mounted."""
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def set_front_matter(text, updates):
    """Set keys in the leading '---' block of a Markdown record, keeping every other line as it is."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise ValueError("no front matter")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise ValueError("front matter not closed")
    left = dict(updates)
    for i in range(1, end):
        key = lines[i].split(":", 1)[0].strip()
        if ":" in lines[i] and key in left:
            lines[i] = "%s: %s" % (key, left.pop(key))
    lines[end:end] = ["%s: %s" % kv for kv in left.items()]
    return "\n".join(lines)


def projects_under(root):
    for name in sorted(os.listdir(root)):
        if P.valid_name(name) and os.path.isfile(os.path.join(root, name, "board", "project.json")):
            yield name, os.path.join(root, name, "board")


def read_runs(board):
    """[(run_id, path of run.json, record, heartbeat mtime)] for every run with a readable run.json."""
    out = []
    runs = os.path.join(board, "runs")
    for rid in sorted(os.listdir(runs)) if os.path.isdir(runs) else []:
        path = os.path.join(runs, rid, "run.json")
        try:
            with open(path, encoding="utf-8") as f:
                rec = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict):
            continue
        try:
            hb = os.stat(os.path.join(runs, rid, "heartbeat")).st_mtime
        except OSError:
            hb = None
        out.append((rid, path, rec, hb))
    return out


# ------------------------------------------------------------------ probing runs' processes
def _probe_line(key, pid, tmux):
    """Shell that prints '<key> alive' or '<key> gone' for one run (its pid owned by me, else its tmux session)."""
    if pid is not None:
        test = 'u=$(ps -o user= -p %d 2>/dev/null | tr -d " "); [ "$u" = "$(id -un)" ]' % pid
    else:
        test = "tmux has-session -t '=%s' 2>/dev/null" % tmux
    return "if %s; then echo '%s alive'; else echo '%s gone'; fi" % (test, key, key)


def make_probe(servers, run_cmd=None, here=None):
    """probe(records) -> one of "running" / "absent" / "unreachable" / None per record, in order.
    One ssh per server per call (the H100/H200 hosts refuse rapid connections); local runs need no ssh.
    None means nothing was recorded to look for (no pid, no tmux) or the server is not known.
    servers: {short name: ssh host}."""
    here = here or socket.gethostname().split(".")[0]

    def default_run(cmd):
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            return None
        return p.stdout if p.returncode == 0 else None

    run_cmd = run_cmd or default_run

    def probe(records):
        out, by_server = [None] * len(records), {}
        for i, rec in enumerate(records):
            pid = rec.get("pid") if isinstance(rec.get("pid"), int) and not isinstance(rec.get("pid"), bool) and rec.get("pid") > 0 else None
            tmux = rec.get("tmux") if isinstance(rec.get("tmux"), str) and TMUX_RE.match(rec.get("tmux")) else None
            server = rec.get("server")
            if (pid is None and tmux is None) or (server != here and server not in servers):
                continue
            by_server.setdefault(server, []).append((i, _probe_line("r%d" % i, pid, tmux)))
        for server, items in by_server.items():
            script = "\n".join(line for _, line in items)
            if server == here:
                text = run_cmd(["bash", "-c", script])
            else:
                text = run_cmd(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "LogLevel=ERROR",
                                servers[server], script])
            got = {}
            for line in (text or "").split("\n"):
                parts = line.strip().split()
                if len(parts) == 2 and parts[1] in ("alive", "gone"):
                    got[parts[0]] = parts[1]
            for i, _ in items:
                v = got.get("r%d" % i)
                out[i] = "unreachable" if text is None or v is None else {"alive": "running", "gone": "absent"}[v]
        return out
    return probe


# ------------------------------------------------------------------ reconcile
def reconcile(root, now, probe, state_path=None, dry_run=False):
    """One pass over every project under root. Returns the changes made (or, with dry_run, that would be):
    [{"project", "file", "what"}].

    - A run recorded as 运行中 that run_state() calls 已中断 on PASSES consecutive passes is marked 被中断.
      A dry run reports what looks dead right now.
    - A task in 等运行 whose runs (run.json "task", or the task's "runs" list) have all ended becomes 可以继续.
    probe(list of run records) -> list of probe results (see make_probe); only runs without a fresh
    heartbeat are probed. Counts between passes live in state_path (JSON); without it every pass starts at zero.
    """
    seen = {}
    if state_path:
        try:
            with open(state_path, encoding="utf-8") as f:
                seen = json.load(f).get("dead") or {}
        except (OSError, ValueError):
            seen = {}
    boards = [(name, board, read_runs(board)) for name, board in projects_under(root)]
    ask = [(name, rid, path, rec, hb) for name, board, runs in boards for rid, path, rec, hb in runs
           if rec.get("status") == "运行中" and not (hb is not None and now - hb <= P.HEARTBEAT_STALE)]
    answers = probe([x[3] for x in ask]) if ask else []
    dead_now, changes, closed = {}, [], set()
    for (name, rid, path, rec, hb), got in zip(ask, answers):
        if P.run_state(rec, hb, got, now) != "已中断":
            continue
        key = "%s/%s" % (name, rid)
        dead_now[key] = seen.get(key, 0) + 1
        if dead_now[key] < PASSES and not dry_run:
            continue
        what = ("心跳停在 %s，进程已经不在" % utc(hb)) if hb else "记录写着运行中，但进程已经不在"
        changes.append({"project": name, "file": "runs/%s/run.json" % rid, "what": "标为被中断：" + what})
        closed.add(key)
        dead_now.pop(key, None)
        if not dry_run:
            rec = dict(rec, status="被中断", ended=rec.get("ended") or utc(now),
                       error=rec.get("error") or "收尾程序：" + what, closed_by="board reconcile")
            write_atomic(path, json.dumps(rec, ensure_ascii=False, indent=1))
    for name, board, runs in boards:
        status = {rid: ("被中断" if "%s/%s" % (name, rid) in closed else rec.get("status")) for rid, _, rec, _ in runs}
        by_task = {}
        for rid, path, rec, hb in runs:
            if isinstance(rec.get("task"), str):
                by_task.setdefault(rec["task"], []).append(rid)
        tdir = os.path.join(board, "tasks")
        for fn in sorted(os.listdir(tdir)) if os.path.isdir(tdir) else []:
            if not fn.endswith(".md") or fn.startswith("."):
                continue
            path = os.path.join(tdir, fn)
            try:
                text, _ = P.read_text(path)
            except OSError:
                continue
            meta, _ = P.parse_front_matter(text)
            if meta.get("status") != "等运行" or not isinstance(meta.get("id"), str):
                continue
            mine = set(by_task.get(meta["id"], [])) | {x for x in P._list(meta.get("runs")) if x in status}
            if not mine or any(status.get(r) == "运行中" for r in mine):
                continue
            changes.append({"project": name, "file": "tasks/" + fn,
                            "what": "等运行 → 可以继续（%d 次运行都结束了）" % len(mine)})
            if not dry_run:
                write_atomic(path, set_front_matter(text, {"status": "可以继续", "updated": utc(now)}))
    if state_path and not dry_run:
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        write_atomic(state_path, json.dumps({"dead": dead_now, "at": utc(now)}, ensure_ascii=False))
    return changes


# ------------------------------------------------------------------ facts pack
def find_project(start, root=None):
    """The project directory at or above start (it has board/project.json), or None.
    With root, only projects inside root count (the fixtures of this repository are not projects)."""
    here = os.path.realpath(start)
    if os.path.isfile(here):
        here = os.path.dirname(here)
    root = os.path.realpath(root) if root else None
    while True:
        if os.path.isfile(os.path.join(here, "board", "project.json")):
            if root is None or here.startswith(root + os.sep):
                return here
            return None
        up = os.path.dirname(here)
        if up == here:
            return None
        here = up


_first_line = P.first_line
_handoff_next = P.handoff_next
unprocessed_replies = P.unprocessed_replies


def papers_lines(d, must_max=5, read_max=10):
    """The facts pack's 论文 section: counts, the 必读 papers the user has not ticked, then the ones
    the user has ticked 读过, so a discussion knows what it can assume the user has read."""
    papers, marks = d.get("papers") or [], d.get("paper_marks") or {}
    if not papers:
        return ["（还没有）"]

    def one(p):
        t = str(p.get("title") or p["id"])
        return "[%s] %s%s" % (p["id"], t if len(t) <= 80 else t[:79] + "…", "" if p.get("checked") else "（未核实）")

    c = P.paper_counts(d)
    must = [p for p in papers if p.get("priority") == "必读" and p["id"] not in marks]
    read = [p for p in papers if p["id"] in marks]
    out = ["- 共 %d 篇，用户读过 %d 篇，必读未读 %d 篇。读过的可以直接谈细节；没读过的，提到时先用一两句交代它做了什么。"
           % (c["n"], c["read"], c["must_unread"])]
    out += ["- 必读未读：" + one(p) for p in must[:must_max]]
    if len(must) > must_max:
        out.append("- 必读未读还有 %d 篇（见控制台「论文」页）" % (len(must) - must_max))
    out += ["- 用户读过：" + one(p) for p in read[:read_max]]
    if len(read) > read_max:
        out.append("- 读过的还有 %d 篇（见控制台「论文」页）" % (len(read) - read_max))
    return out


def brief(project_dir, max_chars=6000, now=None, session_id=None):
    """The facts pack: what a new session (or one just compacted) must know about the project, built
    from board/ so it never disagrees with the console. Plain text, at most max_chars characters.
    session_id (from the SessionStart hook) is printed so the conversation can name itself on a task card."""
    now = time.time() if now is None else now
    board = os.path.join(project_dir, "board")
    d = P.scan_project(board)
    p = d.get("project") or {}
    name = os.path.basename(os.path.normpath(project_dir))
    states = P.decision_states(d.get("decisions") or [], d.get("answers") or {})
    head = ["【事实包】%s（%s）· board brief 生成于 %s。状态以 board/ 为准，这里是摘要；控制台上看得到同样的内容。"
            % (p.get("title") or name, name, utc(now)),
            "状态：%s；当前版本：%s。研究问题：%s" % (p.get("status") or "未写", p.get("current_version") or "未定",
                                          p.get("question") or "未写")]
    sid = session_id if isinstance(session_id, str) and re.match(r"^[A-Za-z0-9-]{8,64}$", session_id) else None
    if sid:
        head.append("本会话 id：%s" % sid)
    sections = []

    rules = ""
    try:
        with open(os.path.join(project_dir, "CLAUDE.md"), encoding="utf-8") as f:
            rules = P.split_sections(f.read()).get("长期约束", "")
    except OSError:
        pass
    sections.append(("项目规则", [rules.strip()] if rules.strip() else
                     ["见项目 CLAUDE.md（在项目目录里开会话时会自动读入；不在项目目录时先读它）。"]))

    adopted = []
    for r in d.get("decisions") or []:
        i = r["meta"].get("id")
        if states.get(i) == "采纳":
            adopted.append("- [%s] %s：%s" % (i, r["meta"].get("title") or i, _first_line((r.get("sections") or {}).get("决定"))))
    sections.append(("已采纳的决定（照此执行；要改先问用户）", adopted or ["（还没有）"]))

    tasks = []
    for t in d.get("tasks") or []:
        m = t["meta"]
        if m.get("status") in P.TASK_OPEN:
            nxt = _handoff_next(t.get("sections") or {})
            tasks.append("- [%s] %s · %s：%s%s" % (m.get("id"), m.get("status"), m.get("kind") or "?", m.get("title") or "",
                                                 ("。下一步：" + nxt) if nxt else ""))
    sections.append(("进行中的任务（tasks/）", tasks or ["（没有）"]))

    runs = []
    for r in d.get("runs") or []:
        rec = r.get("run") or {}
        if rec.get("status") == "运行中":
            st = P.run_state(rec, r.get("heartbeat"), None, now)
            runs.append("- %s @%s 卡 %s（%s%s）：%s" % (rec.get("id") or r["id"], rec.get("server") or "?", rec.get("gpus"),
                                                   rec.get("attempt") or "-", (" / 任务 " + rec["task"]) if rec.get("task") else "",
                                                   "心跳正常" if st == "运行中" else "心跳停了，可能已结束"))
    sections.append(("在跑的运行", runs or ["（没有）"]))

    waiting = []
    cands = [r["meta"]["id"] for r in d.get("decisions") or [] if states.get(r["meta"].get("id")) == "候选"]
    if cands:
        waiting.append("- 候选决定，等用户在控制台采纳：" + "、".join(cands))
    conf = [a["meta"].get("id") for a in d.get("attempts") or [] if a["meta"].get("status") == "待确认"]
    if conf:
        waiting.append("- 结论等用户确认的尝试：" + "、".join(str(x) for x in conf))
    todos = [t for t in d.get("todos") or [] if not t.get("done")]
    if todos:
        waiting.append("- 未完成的待办 %d 条：%s" % (len(todos), "；".join("%s（%s）" % (t["id"], str(t.get("text"))[:40]) for t in todos[:8])
                                                 + ("……" if len(todos) > 8 else "")))
    replies = unprocessed_replies(d)
    if replies:
        waiting.append("- 未处理的回复 %d 条（%s）：开场提一句；用户说「处理回复」才处理。" % (len(replies), "、".join(replies)))
    sections.append(("等用户的", waiting or ["（没有）"]))

    recent = []
    for a in sorted(d.get("attempts") or [], key=lambda a: str(a["meta"].get("date") or ""), reverse=True):
        so = ((a.get("onepager") or {}).get("所以") or {}).get("text") or _first_line((a.get("sections") or {}).get("结论和原因"))
        if so:
            recent.append("- [%s] %s：%s" % (a["meta"].get("id"), a["meta"].get("status") or "", so))
        if len(recent) >= 3:
            break
    sections.append(("最近的结论", recent or ["（还没有）"]))
    sections.append(("论文（board/papers.jsonl）", papers_lines(d)))

    sections.append(("提醒", [
        "- 用户在对话里说的长期有效的话（要求、偏好、定下的做法），记成 board/decisions/ 里的「候选」，告诉用户「已记下，等你在控制台采纳」。",
        "- 一件具体的事开工前写任务卡（board/tasks/），做完写交接；实验最上面写「一页纸」。格式：/shared/ssd/dchen/_shared/BOARD_FORMAT.md。",
        "- 用户说「做 <任务 id>」：这个对话就是那张卡的任务会话。先运行 /home/dchen/bin/board task-begin <任务 id>%s，"
        "照它打印的步骤做；它拒绝时把原因告诉用户，不要绕过。" % (" --session " + sid if sid else ""),
        "- 讨论或更新方案时，把用到的、建议用户读的论文记进 board/papers.jsonl，写明为什么读（why）；"
        "亲自打开链接、核对过标题才写 checked。用户在控制台勾「读过」。",
        "- setup.json 里新加数据集时，抽 10 条原样放进 board/examples/data/<数据集 id>.jsonl（有类别的每类都抽到），"
        "用户在控制台的实验设置页直接看数据长什么样。",
        "- 改 board/ 里的文件后，记录检查有问题会当场提示，照提示改掉。"]))

    def render(secs):
        out = list(head)
        for title, items in secs:
            out.append("")
            out.append("## " + title)
            out.extend(items)
        return "\n".join(out)

    text = render(sections)
    order = ["论文（board/papers.jsonl）", "最近的结论", "等用户的","进行中的任务（tasks/）", "已采纳的决定（照此执行；要改先问用户）", "在跑的运行", "项目规则"]
    for title in order:  # shorten the least important lists first
        while len(text) > max_chars:
            sec = next(s for s in sections if s[0] == title)
            if len(sec[1]) <= 1:
                break
            sec[1].pop()
            if not sec[1][-1].endswith("（更多见控制台）"):
                sec[1][-1] += "（更多见控制台）"
            text = render(sections)
    if len(text) > max_chars:
        text = text[:max_chars - 8] + "…（已截断）"
    return text


# ------------------------------------------------------------------ record check
def check(project_dir, only_file=None):
    """Record-check problems of a project: [{"where", "what"}]. With only_file (a path inside board/),
    only that file's problems, unreadable-file errors included."""
    board = os.path.join(project_dir, "board")
    d = P.scan_project(board)
    out = list(d.get("checks") or []) + [{"where": e.get("file"), "what": e.get("error")} for e in d.get("errors") or []]
    if only_file is not None:  # a file's own problems, and those of its rows ("papers.jsonl · <id>")
        rel = os.path.relpath(os.path.realpath(only_file), os.path.realpath(board))
        out = [c for c in out if c["where"] == rel or str(c["where"]).startswith(rel + " · ")]
    return out


def _hook_input():
    try:
        data = json.loads(sys.stdin.read() or "{}")
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def hook_brief(root):
    """SessionStart: print the facts pack when the session starts inside a project (nothing otherwise)."""
    data = _hook_input()
    proj = find_project(data.get("cwd") or os.getcwd(), root)
    if proj:
        print(brief(proj, session_id=data.get("session_id")))
    return 0


def hook_check(root):
    """PostToolUse (Write/Edit): when the edited file is in a project's board/, report its problems to the
    session (exit 2 puts stderr in front of Claude); silent otherwise."""
    data = _hook_input()
    path = (data.get("tool_input") or {}).get("file_path") or ""
    if not path or "/board/" not in path.replace(os.sep, "/"):
        return 0
    proj = find_project(path, root)
    if not proj or not os.path.realpath(path).startswith(os.path.realpath(os.path.join(proj, "board")) + os.sep):
        return 0
    probs = check(proj, path)
    if not probs:
        return 0
    rel = os.path.relpath(os.path.realpath(path), os.path.realpath(proj))
    row = lambda c: str(c["where"]).partition(" · ")[2]  # which row of the file, e.g. the paper's id
    print("记录检查：%s 有 %d 个问题，请照格式（/shared/ssd/dchen/_shared/BOARD_FORMAT.md）改掉：\n%s"
          % (rel, len(probs), "\n".join("- " + (row(c) + "：" if row(c) else "") + c["what"] for c in probs[:20])), file=sys.stderr)
    return 2


# ------------------------------------------------------------------ task sessions and the dispatcher
MAX_SESSIONS = 4                  # task and reply sessions running at once, all projects together
CODE_KINDS = ("执行", "调试")       # task kinds that change code: one at a time per project, in a worktree
STARTABLE = ("待开始", "可以继续", "进行中")
CLAUDE = os.path.expanduser("~/.local/bin/claude")
SESSION_RE = re.compile(r"^(task|reply)-([A-Za-z0-9][A-Za-z0-9._-]*)$")


class TaskError(Exception):
    """Why a task session was not started (shown to whoever asked)."""


def tmux_sessions(run=subprocess.run):
    try:
        p = run(["tmux", "list-sessions", "-F", "#{session_name}"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    return p.stdout.split() if p.returncode == 0 else []


def task_session_name(project, task):
    return "task-%s-%s" % (project, task)


def read_task(board, task):
    path = os.path.join(board, "tasks", task + ".md")
    if not (P.valid_name(task) and os.path.isfile(path)):
        raise TaskError("没有任务卡 board/tasks/%s.md" % task)
    text, _ = P.read_text(path)
    meta, body = P.parse_front_matter(text)
    return meta, P.split_sections(body)


def _git(cwd, *args, run=subprocess.run):
    p = run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60)
    return p.returncode == 0, (p.stdout + p.stderr).strip()


def live_sessions():
    """Claude sessions alive on this machine (rows of `claude agents --json`: sessionId, name, cwd, ...), or None
    when they cannot be read. Conversations opened in a project's folder in the App are among them."""
    try:
        p = subprocess.run([CLAUDE, "agents", "--json"], capture_output=True, text=True, timeout=30,
                           env=dict(os.environ, NO_COLOR="1"))
        rows = json.loads(p.stdout) if p.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return [r for r in rows if isinstance(r, dict) and isinstance(r.get("sessionId"), str)] if isinstance(rows, list) else None


def _task_cards(root):
    """{(project, task id): front matter} of every task card under root (only the front matter is read)."""
    out = {}
    for name, board in projects_under(root):
        d = os.path.join(board, "tasks")
        try:
            files = sorted(os.listdir(d))
        except OSError:
            continue
        for fn in files:
            if not fn.endswith(".md") or fn.startswith("."):
                continue
            try:
                meta, _ = P.parse_front_matter(P.read_text(os.path.join(d, fn), 16 * 1024)[0])
            except OSError:
                continue
            out[(name, meta["id"] if isinstance(meta.get("id"), str) else fn[:-3])] = meta
    return out


def busy_tasks(root, tmux_names, live):
    """The sessions that count against MAX_SESSIONS, one key per card however it was started:
    {"task-<project>-<task>" or "reply-<project>": [{"session": id or None, "label": text}]}.
    - tmux sessions task-* and reply-* (started by task-start and dispatch) count as before;
    - a live Claude session counts when a card that is 进行中 names it in sessions (by id or name), or when it
      is called "<project>·<task>" after such a card. A finished card's idle conversation does not count."""
    busy = {}
    for n in tmux_names:
        if SESSION_RE.match(n):
            busy.setdefault(n, []).append({"session": None, "label": "tmux " + n})
    if live:
        refs = {}
        for (proj, task), meta in _task_cards(root).items():
            if meta.get("status") != "进行中":
                continue
            for r in ["%s·%s" % (proj, task)] + [str(x) for x in P._list(meta.get("sessions"))]:
                refs.setdefault(r, (proj, task))
        for x in live:
            hit = refs.get(x["sessionId"]) or refs.get(x.get("name"))
            if not hit:
                continue
            key = task_session_name(*hit)
            if any(e["session"] == x["sessionId"] for e in busy.get(key, [])):
                continue
            if busy.get(key) and x.get("name") == "%s·%s" % hit:
                continue  # the session task-start started, already counted through tmux
            busy.setdefault(key, []).append({"session": x["sessionId"],
                                             "label": "对话「%s」（%s）" % (x.get("name") or "", x["sessionId"][:8])})
    return busy


def _check_start(root, project, task, meta, busy, session_id=None):
    """Raise TaskError when this card must not start now: its status, another session on it, the limit of
    MAX_SESSIONS, or another code card being worked on in the same project. Shared by task_start and task_begin."""
    board = os.path.join(root, project, "board")
    if meta.get("status") not in STARTABLE:
        raise TaskError("任务卡的状态是「%s」，只有 %s 的卡能开工" % (meta.get("status"), " / ".join(STARTABLE)))
    me = task_session_name(project, task)
    others = [e for e in busy.get(me, []) if not (session_id and e["session"] == session_id)]
    if any(e["session"] is None for e in others):
        raise TaskError("这张卡的会话已经在运行（tmux %s）；在 App 里点「%s·%s」接着说" % (me, project, task))
    if others:
        raise TaskError("这张卡已经有对话在做：%s。在那个对话里接着说，或者先结束它" % "、".join(e["label"] for e in others))
    running = sorted(k for k in busy if k != me)
    if len(running) >= MAX_SESSIONS:
        raise TaskError("已经有 %d 个任务会话在运行（上限 %d）：%s。先等一个结束，或者 board task-stop 停掉一个"
                        % (len(running), MAX_SESSIONS, "、".join(running)))
    if meta.get("kind") in CODE_KINDS:
        code = []
        for k in running:
            m = re.match(r"^task-%s-(.+)$" % re.escape(project), k)
            if m:
                try:
                    if read_task(board, m.group(1))[0].get("kind") in CODE_KINDS:
                        code.append(m.group(1))
                except TaskError:
                    pass
        if code:
            raise TaskError("这个项目已经有改代码的任务在做（%s）；同一个项目同时只做一张改代码的卡" % "、".join(code))


def _prepare_worktree(proj, task, meta, run=subprocess.run):
    """(workdir, worktree): a code card with a scope works in <project>/.worktrees/<task> on branch task/<task>."""
    if not (meta.get("kind") in CODE_KINDS and P._list(meta.get("scope"))):
        return proj, None
    ok, _ = _git(proj, "rev-parse", "--verify", "HEAD", run=run)
    if not ok:
        return proj, None
    worktree = os.path.join(proj, ".worktrees", task)
    _exclude_worktrees(proj)
    if not os.path.isdir(worktree):
        ok, out = _git(proj, "worktree", "add", worktree, "-b", "task/" + task, run=run)
        if not ok:
            ok, out = _git(proj, "worktree", "add", worktree, "task/" + task, run=run)  # the branch exists already
        if not ok:
            raise TaskError("建 worktree 失败：%s" % out[-300:])
    return worktree, worktree


def task_prompt(project, task, kind, workdir, board, me):
    """The steps of a task session. me: how the session names itself in the card's sessions."""
    lines = ["做任务 %s（%s）。" % (task, kind or "任务"),
             "1. 先读任务卡 %s/tasks/%s.md、事实包和项目 CLAUDE.md；把%s加进任务卡的 sessions，status 改成「进行中」。" % (board, task, me),
             "2. 只做这张卡写的事；范围外的发现写进交接，不要顺手做。需要用户决定的写进待办，然后把卡改成「等你」。",
             "3. 训练或评测用 boardrun 启动（--task %s）后，把卡改成「等运行」、写好交接就可以停下，不用守着。" % task,
             "4. 做完写交接（做了什么 / 证据在哪 / 没做完的 / 下一步 / 要你决定的），改状态，告诉用户结果。"]
    if workdir != os.path.dirname(board):
        lines.append("5. 代码在这个 worktree（%s，分支 task/%s）里改和提交；记录一律写到项目主目录的 %s。收尾时先 rebase 到主线、跑测试，"
                     "没动核心代码且没有冲突就合并回主线并删掉 worktree；动了核心代码或有冲突，把卡改成「等你」并在待办里请用户决定合并。" % (workdir, task, board))
    return "\n".join(lines)


def task_start(root, project, task, run=subprocess.run, model=None, live=None):
    """Start the session of one task card in the background (tmux, Remote Control). -> {"session", "dir", "worktree",
    "name"}; raises TaskError when it must not start. live: rows of `claude agents --json` (None: read them)."""
    proj = os.path.join(root, project)
    board = os.path.join(proj, "board")
    if not (P.valid_name(project) and os.path.isfile(os.path.join(board, "project.json"))):
        raise TaskError("没有项目 %s" % project)
    meta, _ = read_task(board, task)
    names = tmux_sessions(run)
    if meta.get("status") in STARTABLE:  # read the live sessions only when the card could start at all
        live = (live_sessions() or []) if live is None else live
    _check_start(root, project, task, meta, busy_tasks(root, names, live or []))
    name = task_session_name(project, task)
    workdir, worktree = _prepare_worktree(proj, task, meta, run)
    prompt = task_prompt(project, task, meta.get("kind"), workdir, board, "自己的会话名「%s·%s」" % (project, task))
    cmd = [CLAUDE, "--remote-control", "%s·%s" % (project, task)] + (["--model", model] if model else []) + [prompt]
    shell = "unset ANTHROPIC_API_KEY; exec " + " ".join(_q(x) for x in cmd)
    p = run(["tmux", "new-session", "-d", "-s", name, "-c", workdir, shell], capture_output=True, text=True, timeout=20)
    if p.returncode != 0:
        raise TaskError("tmux 启动失败：%s" % (p.stderr or p.stdout).strip()[-300:])
    return {"session": name, "dir": workdir, "worktree": worktree, "name": "%s·%s" % (project, task)}


def task_begin(root, project, task, session_id=None, live=None, run=subprocess.run):
    """The conversation the user opened in the project's folder in the App (they said 「做 <task>」) takes this card:
    the same checks and worktree as task_start, and the same steps it would have been started with.
    live: rows of `claude agents --json` (None: read them; when they cannot be read only tmux sessions count).
    -> {"workdir", "worktree", "steps"}; raises TaskError when the card must not start now."""
    proj = os.path.join(root, project)
    board = os.path.join(proj, "board")
    if not (P.valid_name(project) and os.path.isfile(os.path.join(board, "project.json"))):
        raise TaskError("没有项目 %s" % project)
    meta, _ = read_task(board, task)
    note = None
    if live is None and meta.get("status") in STARTABLE:
        live = live_sessions()
        if live is None:
            note = "（读不到 claude agents，同时在做的任务只按 tmux 里的会话算了。）"
    _check_start(root, project, task, meta, busy_tasks(root, tmux_sessions(run), live or []), session_id)
    workdir, worktree = _prepare_worktree(proj, task, meta, run)
    me = "本会话 id「%s」" % session_id if session_id else "本会话的 id（事实包开头有）"
    steps = task_prompt(project, task, meta.get("kind"), workdir, board, me)
    if worktree:
        steps += "\n注意：这个对话开在项目主目录，改代码、跑测试和提交都要在上面的 worktree 里（cd 过去或用它的绝对路径）。"
    head = "可以开工：%s（%s）。工作目录：%s" % (meta.get("title") or task, meta.get("kind") or "任务", workdir)
    return {"workdir": workdir, "worktree": worktree, "steps": "\n".join(x for x in (head, note, steps) if x)}


def _exclude_worktrees(proj):
    """Keep .worktrees/ out of git status through .git/info/exclude (local; the tracked .gitignore stays clean)."""
    path = os.path.join(proj, ".git", "info", "exclude")
    try:
        with open(path, encoding="utf-8") as f:
            if ".worktrees/" in f.read().split():
                return
    except OSError:
        pass
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n.worktrees/\n")
    except OSError:
        pass


def _q(s):
    return "'" + str(s).replace("'", "'\\''") + "'"


def task_stop(project, task, run=subprocess.run):
    name = task_session_name(project, task)
    p = run(["tmux", "kill-session", "-t", "=" + name], capture_output=True, text=True, timeout=10)
    if p.returncode != 0:
        raise TaskError("没有在运行的会话 %s" % name)
    return name


def dispatch(root, state_path, now, run=subprocess.run, dry_run=False, live=None):
    """Start one headless session per project for replies the user marked 直接处理 and no session has taken yet.
    A reply is taken once (state_path remembers it); at most MAX_SESSIONS sessions run at once. -> [started]"""
    try:
        with open(state_path, encoding="utf-8") as f:
            taken = json.load(f).get("taken") or {}
    except (OSError, ValueError):
        taken = {}
    names = tmux_sessions(run)
    running = None  # counted (busy_tasks) only when there is something to start
    started = []
    for name, board in projects_under(root):
        d = P.scan_project(board)
        want = [rid for rid in P.unprocessed_replies(d)
                if (d["answers"][rid].get("mode") == "直接处理") and taken.get("%s/%s" % (name, rid)) != d["answers"][rid].get("at")]
        if not want or "reply-" + name in names:
            continue
        if running is None:
            live = (live_sessions() or []) if live is None else live
            running = len(busy_tasks(root, names, live))
        if running >= MAX_SESSIONS:
            break
        prompt = ("处理回复：只处理用户在控制台勾了「直接处理」的这几条：%s。按 /shared/ssd/dchen/_shared/BOARD_FORMAT.md 的「处理回复」做："
                  "每条先在 todos.jsonl 追加一行 state: 已领取，做完写 done 和 result；回复有歧义或和现状冲突时不要猜，"
                  "写 state: 受阻 和原因，并留一条待办问用户。其他回复不要动。" % "、".join(want))
        cmd = [CLAUDE, "-p", "--permission-mode", "auto", "--name", "%s·处理回复" % name, prompt]
        log = os.path.join(os.path.dirname(state_path), "dispatch-%s.log" % name)
        shell = "unset ANTHROPIC_API_KEY; %s >> %s 2>&1" % (" ".join(_q(x) for x in cmd), _q(log))
        started.append({"project": name, "replies": want})
        if dry_run:
            continue
        p = run(["tmux", "new-session", "-d", "-s", "reply-" + name, "-c", os.path.dirname(board), shell],
                capture_output=True, text=True, timeout=20)
        if p.returncode != 0:
            started[-1]["error"] = (p.stderr or p.stdout).strip()[-200:]
            continue
        running += 1
        for rid in want:
            taken["%s/%s" % (name, rid)] = d["answers"][rid].get("at")
    if not dry_run:
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        write_atomic(state_path, json.dumps({"taken": taken, "at": utc(now)}, ensure_ascii=False))
    return started


# ------------------------------------------------------------------ command line
def main(argv=None):
    ap = argparse.ArgumentParser(prog="board", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("brief", help="facts pack of a project")
    b.add_argument("dir", nargs="?", default=".")
    b.add_argument("--hook", action="store_true", help="read the SessionStart hook input on stdin; never fail")
    c = sub.add_parser("check", help="record check of a project")
    c.add_argument("dir", nargs="?", default=".")
    c.add_argument("--file")
    c.add_argument("--hook", action="store_true", help="read the PostToolUse hook input on stdin; never fail")
    r = sub.add_parser("reconcile", help="close dead runs, move waiting tasks on")
    r.add_argument("--dry-run", action="store_true")
    tb = sub.add_parser("task-begin", help="this conversation takes a task card (run inside the project)")
    tb.add_argument("task")
    tb.add_argument("--session", help="this conversation's session id (the facts pack shows it)")
    tb.add_argument("--dir", default=".", help="a directory inside the project (default: here)")
    ts = sub.add_parser("task-start", help="start the session of one task card in the background")
    ts.add_argument("project")
    ts.add_argument("task")
    ts.add_argument("--model")
    tp = sub.add_parser("task-stop", help="stop the session of one task card")
    tp.add_argument("project")
    tp.add_argument("task")
    dp = sub.add_parser("dispatch", help="start a session for replies marked 直接处理")
    dp.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd in ("task-begin", "task-start", "task-stop", "dispatch"):
        cfg = load_config()
        try:
            if a.cmd == "task-begin":
                root, here = os.path.realpath(cfg["root"]), os.path.realpath(a.dir)
                project = here[len(root) + 1:].split(os.sep)[0] if here.startswith(root + os.sep) else ""
                if not (P.valid_name(project) and os.path.isfile(os.path.join(root, project, "board", "project.json"))):
                    raise TaskError("%s 不在任何项目里（%s 下面带 board/project.json 的目录）" % (here, root))
                print(task_begin(root, project, a.task, session_id=a.session)["steps"])
            elif a.cmd == "task-start":
                x = task_start(cfg["root"], a.project, a.task, model=a.model)
                print("已开工：tmux %s，目录 %s%s。在 App 里点「%s」可以接着说。" % (x["session"], x["dir"],
                      "（worktree，分支 task/%s）" % a.task if x["worktree"] else "", x["name"]))
            elif a.cmd == "task-stop":
                print("已停止 %s" % task_stop(a.project, a.task))
            else:
                for x in dispatch(cfg["root"], os.path.join(cfg["state_dir"], "dispatch.json"), time.time(), dry_run=a.dry_run):
                    print("%s %s%s: %s%s" % (utc(time.time()), "(dry run) " if a.dry_run else "", x["project"], "、".join(x["replies"]),
                                             " 失败：" + x["error"] if x.get("error") else ""))
        except TaskError as e:
            print("board: %s" % e, file=sys.stderr)
            return 1
        return 0
    if getattr(a, "hook", False):
        try:
            cfg = load_config()
            return hook_brief(cfg["root"]) if a.cmd == "brief" else hook_check(cfg["root"])
        except Exception:  # a hook must never get in the way of a session
            return 0
    if a.cmd in ("brief", "check"):
        proj = find_project(a.dir)
        if not proj:
            print("board: no board/project.json at or above %s" % os.path.abspath(a.dir), file=sys.stderr)
            return 1
        if a.cmd == "brief":
            print(brief(proj))
            return 0
        probs = check(proj, a.file)
        for x in probs:
            print("%s: %s" % (x["where"], x["what"]))
        return 2 if probs else 0
    if a.cmd == "reconcile":
        cfg = load_config()
        changes = reconcile(cfg["root"], time.time(), make_probe(cfg["servers"]),
                            os.path.join(cfg["state_dir"], "reconcile.json"), dry_run=a.dry_run)
        for c in changes:
            print("%s %s%s %s: %s" % (utc(time.time()), "(dry run) " if a.dry_run else "", c["project"], c["file"], c["what"]))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
