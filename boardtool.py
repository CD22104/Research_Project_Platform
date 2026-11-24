"""board: command-line tools around the project boards (run on xtraa100; standard library only).

  board brief [DIR] [--hook]        the facts pack of the project at or above DIR (SessionStart hook)
  board check [DIR] [--file PATH] [--hook]
                                    record check; exit 2 with the problems (PostToolUse hook: only the edited file)
  board reconcile [--dry-run]       close runs whose process is gone, move waiting tasks on (cron, every 5 min)

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


def brief(project_dir, max_chars=6000, now=None):
    """The facts pack: what a new session (or one just compacted) must know about the project, built
    from board/ so it never disagrees with the console. Plain text, at most max_chars characters."""
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

    sections.append(("提醒", [
        "- 用户在对话里说的长期有效的话（要求、偏好、定下的做法），记成 board/decisions/ 里的「候选」，告诉用户「已记下，等你在控制台采纳」。",
        "- 一件具体的事开工前写任务卡（board/tasks/），做完写交接；实验最上面写「一页纸」。格式：/shared/ssd/dchen/_shared/BOARD_FORMAT.md。",
        "- 改 board/ 里的文件后，记录检查有问题会当场提示，照提示改掉。"]))

    def render(secs):
        out = list(head)
        for title, items in secs:
            out.append("")
            out.append("## " + title)
            out.extend(items)
        return "\n".join(out)

    text = render(sections)
    order = ["最近的结论", "等用户的", "进行中的任务（tasks/）", "已采纳的决定（照此执行；要改先问用户）", "在跑的运行", "项目规则"]
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
    if only_file is not None:
        rel = os.path.relpath(os.path.realpath(only_file), os.path.realpath(board))
        out = [c for c in out if c["where"] == rel]
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
        print(brief(proj))
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
    print("记录检查：%s 有 %d 个问题，请照格式（/shared/ssd/dchen/_shared/BOARD_FORMAT.md）改掉：\n%s"
          % (rel, len(probs), "\n".join("- " + c["what"] for c in probs[:20])), file=sys.stderr)
    return 2


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
    a = ap.parse_args(argv)
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
