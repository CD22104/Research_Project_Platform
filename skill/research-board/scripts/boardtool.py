"""board: command-line tools around the project boards (standard library only). Settings: rbconfig.py.

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
  board dispatch [--dry-run]        start a session for replies marked 直接处理, and the cards marked start: auto whose
                                    after-cards are all done (cron, every 5 min)
  board serve [--dry-run]           start and always stop the services task sessions asked for in board/services/
                                    (cron, every 5 min; services.py)
  board stop-check --hook           Stop hook: keep a session going once when it is leaving a task card without its
                                    handoff, or board/ edits without NOTES.md (stop_check)
  board waiting [--json]            sessions on this machine waiting for a permission answer
  board waiting [--clear] --hook    Notification hook (permission_prompt): record this session as waiting;
                                    with --clear (after each tool call, prompt, turn, session end): drop the record

The record format is ../references/board-format.md. Only `reconcile` writes into project boards, and only three
things: the status of a run that is recorded as 运行中 but whose process is gone, the status of a task card that
waits for runs that have all ended, and to-dos (ids run-failed-*) for failed runs whose attempt was not updated.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import projects as P  # noqa: E402
import rbconfig  # noqa: E402
import services as SV  # noqa: E402

BOARD = os.path.join(HERE, "board")                                                   # this skill's board command
FORMAT = os.path.join(os.path.dirname(HERE), "references", "board-format.md")         # the record format, beside it

TMUX_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
PASSES = 2  # a run must look dead on this many consecutive passes before its record is changed


def load_config(path=None):
    """The settings this tool needs, from the one configuration file (rbconfig); also sets the claude command and
    the limit of task sessions for this run."""
    global CLAUDE, MAX_SESSIONS
    cfg = rbconfig.load(path)
    CLAUDE, MAX_SESSIONS = cfg["claude_bin"], cfg["max_task_sessions"]
    return {"root": cfg["projects_root"], "state_dir": cfg["state_dir"],
            "servers": {s["name"]: s["host"] for s in cfg["servers"]}}


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
    One ssh per server per call (some servers refuse rapid repeated connections); local runs need no ssh.
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


# ------------------------------------------------------------------ failed runs must be recorded too
FAIL_GRACE = 30 * 60        # seconds a failed or interrupted run's attempt has to be updated before a to-do is written
FAILED_RUN = ("失败", "被中断")
FAIL_TODO = "run-failed-"   # id prefix of these to-dos; reconcile writes and closes them


def iso_ts(s):
    """'2026-11-25T05:47:31Z' -> seconds since the epoch, or None."""
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def _attempts(board):
    """{attempt id: (front matter, mtime)} of board/attempts/*.md (only the front matter is read)."""
    out = {}
    d = os.path.join(board, "attempts")
    for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if not fn.endswith(".md") or fn.startswith("."):
            continue
        path = os.path.join(d, fn)
        try:
            meta, _ = P.parse_front_matter(P.read_text(path, 16 * 1024)[0])
            out[meta["id"] if isinstance(meta.get("id"), str) else fn[:-3]] = (meta, os.stat(path).st_mtime)
        except OSError:
            continue
    return out


def unrecorded_failures(board, runs, since, now):
    """({attempt id: [(ended, run id, record)], oldest first}, attempts): runs that 失败 or were 被中断, ended after
    since and at least FAIL_GRACE before now, whose attempt (its runs list names the run, or the run names the
    attempt) has not been changed since the run ended."""
    atts = _attempts(board)
    named = {}
    for aid, (meta, _) in atts.items():
        for rid in P._list(meta.get("runs")):
            named.setdefault(str(rid), set()).add(aid)
    out = {}
    for rid, _, rec, _ in runs:
        t = iso_ts(rec.get("ended"))
        if rec.get("status") not in FAILED_RUN or t is None or t < since or now - t < FAIL_GRACE:
            continue
        for aid in sorted(named.get(rid, set()) | ({rec["attempt"]} if isinstance(rec.get("attempt"), str) else set())):
            if aid in atts and atts[aid][1] <= t:
                out.setdefault(aid, []).append((t, rid, rec))
    for items in out.values():
        items.sort(key=lambda x: (x[0], x[1]))
    return out, atts


def failure_todos(board, runs, since, now):
    """Rows to append to board/todos.jsonl -> [(row, what)]: one to-do per attempt with unrecorded failures (for its
    latest such run; one open at a time; not again for failures older than an earlier to-do of the attempt, so a
    "不用记" holds), and a closing row for each open one that no longer applies (the attempt was updated)."""
    todos = P.read_todos(os.path.join(board, "todos.jsonl"))["items"] if os.path.isfile(os.path.join(board, "todos.jsonl")) else []
    ids = {str(t.get("id")) for t in todos}
    mine = [t for t in todos if str(t.get("id")).startswith(FAIL_TODO) and t.get("by") == "board reconcile"]
    aid_of = lambda t: str(t.get("link") or "").partition("roadmap/a:")[2]
    fails, atts = unrecorded_failures(board, runs, since, now)
    rec_of = {rid: rec for rid, _, rec, _ in runs}
    rows, still_open = [], set()
    for t in mine:
        if t.get("done"):
            continue
        aid, rid = aid_of(t), str(t["id"])[len(FAIL_TODO):]
        if any(r == rid for _, r, _ in fails.get(aid, [])):
            still_open.add(aid)
            continue
        st = (rec_of.get(rid) or {}).get("status")
        result = ("运行 %s 现在是「%s」，不用再记" % (rid, st) if st not in FAILED_RUN
                  else "实验记录在运行结束后更新了" if aid in atts else "这个实验的记录不在了")
        rows.append((dict(t, done=utc(now), result=result), "待办 %s 标为完成：%s" % (t["id"], result)))
    for aid, items in sorted(fails.items()):
        asked = max([iso_ts(t.get("created")) or 0 for t in mine if aid_of(t) == aid] or [0])
        ended, rid, rec = items[-1]
        if aid in still_open or ended <= asked or FAIL_TODO + rid in ids:
            continue
        title = atts[aid][0].get("title") or aid
        err = " ".join(str(rec.get("error") or "").split())[:150]
        more = [r for _, r, _ in items[:-1]]
        row = {"id": FAIL_TODO + rid, "kind": "whenever", "created": utc(now), "done": None, "by": "board reconcile",
               "text": "实验「%s」的运行 %s %s，实验记录还没记下这次结果" % (title, rid, "失败了" if rec.get("status") == "失败" else "被中断了"),
               "link": "roadmap/a:" + aid,
               "context": "运行 %s 在 %s 结束，状态「%s」%s。%s失败也要记：选「记进尝试」，项目会话会把经过和原因写进实验的「结果」。"
                          % (rid, utc(ended), rec.get("status"), "：" + err if err else "",
                             "同一个实验还有 %d 次失败或中断的运行也没记：%s。" % (len(more), "、".join(more)) if more else ""),
               "options": ["记进尝试", "不用记"], "recommend": "记进尝试"}
        rows.append((row, "待办 %s：%s" % (row["id"], row["text"])))
    return rows


# ------------------------------------------------------------------ reconcile
def reconcile(root, now, probe, state_path=None, dry_run=False):
    """One pass over every project under root. Returns the changes made (or, with dry_run, that would be):
    [{"project", "file", "what"}].

    - A run recorded as 运行中 that run_state() calls 已中断 on PASSES consecutive passes is marked 被中断.
      A dry run reports what looks dead right now.
    - A task in 等运行 whose runs (run.json "task", or the task's "runs" list) have all ended becomes 可以继续.
    - A run that 失败 or was 被中断 and whose attempt was not updated since gets a to-do (failure_todos), which is
      closed once the attempt is updated. Only runs that ended after the first pass that saw this rule
      (failed_since in the state) count; without state_path, all of them.
    probe(list of run records) -> list of probe results (see make_probe); only runs without a fresh
    heartbeat are probed. Counts between passes live in state_path (JSON); without it every pass starts at zero.
    """
    seen, since = {}, 0
    if state_path:
        try:
            with open(state_path, encoding="utf-8") as f:
                old = json.load(f)
        except (OSError, ValueError):
            old = {}
        old = old if isinstance(old, dict) else {}
        seen = old.get("dead") or {}
        since = old["failed_since"] if isinstance(old.get("failed_since"), (int, float)) else now
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
        rows = failure_todos(board, runs, since, now)
        for row, what in rows:
            changes.append({"project": name, "file": "todos.jsonl", "what": what})
        if rows and not dry_run:
            try:
                with open(os.path.join(board, "todos.jsonl"), "a", encoding="utf-8") as f:
                    f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r, _ in rows))
            except OSError:
                pass  # the next pass tries again: nothing was recorded as written
    if state_path and not dry_run:
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        write_atomic(state_path, json.dumps({"dead": dead_now, "at": utc(now), "failed_since": since}, ensure_ascii=False))
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
            t = a["meta"].get("title")
            recent.append("- [%s]%s %s：%s" % (a["meta"].get("id"), " " + t if isinstance(t, str) and t else "", a["meta"].get("status") or "", so))
        if len(recent) >= 3:
            break
    sections.append(("最近的结论", recent or ["（还没有）"]))
    sections.append(("论文（board/papers.jsonl）", papers_lines(d)))

    sections.append(("提醒", [
        "- 用户在对话里说的长期有效的话（要求、偏好、定下的做法），记成 board/decisions/ 里的「候选」，告诉用户「已记下，等你在控制台采纳」。",
        "- 一件具体的事开工前写任务卡（board/tasks/），做完写交接；实验最上面写「一页纸」。格式：%s。" % FORMAT,
        "- 用户说「做 <任务 id>」：这个对话就是那张卡的任务会话。先运行 %s task-begin <任务 id>%s，"
        "照它打印的步骤做；它拒绝时把原因告诉用户，不要绕过。" % (BOARD, " --session " + sid if sid else ""),
        "- 讨论或更新方案时，把用到的、建议用户读的论文记进 board/papers.jsonl，写明为什么读（why）；"
        "亲自打开链接、核对过标题才写 checked。用户在控制台勾「读过」。",
        "- 实验（attempts）字段区写 title（中文短名，不超过 20 字）和 method（检验或改的是方法里的哪一步）；用户确认的结论改变了某一步的做法时，同步改 method/ 里那一步。",
        "- 一批工作告一段落时（夜间调度停下、几张任务卡做完、用户离开超过半天回来前），写一篇简报：docs/<日期>-brief.md，kind: 简报，"
        "四节：现在到哪了 / 新做了什么 / 先看这几个 / 等你决定的，不超过 1500 字；用 {{a:<实验 id>}} 指向实验。控制台把最新一篇放在项目首页最上面。",
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
    try:
        list_waiting()  # a new session start is a good moment to drop the records of sessions that are gone
    except OSError:
        pass
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
    print("记录检查：%s 有 %d 个问题，请照格式（%s）改掉：\n%s"
          % (rel, len(probs), FORMAT, "\n".join("- " + (row(c) + "：" if row(c) else "") + c["what"] for c in probs[:20])), file=sys.stderr)
    return 2


# ------------------------------------------------------------------ sessions waiting for a permission answer
# The Notification hook (permission_prompt) writes one file per waiting session on this machine; hooks that
# fire once the session moves on (a tool finished, the user wrote, the turn or the session ended) remove it.
# The console's poll of each server lists these files. Local state only: nothing goes into project boards.
WAITING_DIR = "~/.local/state/research-board/waiting"   # the same fixed path in scripts/board and platform/server.py
WHAT_MAX = 120                                          # characters of "what it asks to do"
SID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{7,99}$")
ACTIVE_CARD = ("进行中", "等运行", "等你", "可以继续")  # a card in one of these is still held by its session


def waiting_dir():
    return os.path.expanduser(WAITING_DIR)


def last_tool_call(transcript, max_bytes=512 * 1024):
    """(name, input) of the last tool call in a session transcript (JSON lines), or (None, None)."""
    try:
        with open(transcript, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - max_bytes))
            lines = f.read().decode("utf-8", "replace").split("\n")
    except (OSError, TypeError, ValueError):
        return None, None
    for line in reversed(lines):
        if '"tool_use"' not in line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        msg = row.get("message") if isinstance(row, dict) else None
        content = msg.get("content") if isinstance(msg, dict) else None
        uses = [c for c in content if isinstance(c, dict) and c.get("type") == "tool_use"] if isinstance(content, list) else []
        if uses:
            inp = uses[-1].get("input")
            return str(uses[-1].get("name") or "?"), inp if isinstance(inp, dict) else {}
    return None, None


def waiting_what(name, inp):
    """What the session asks to do, short and safe to show: Bash only by its own description (never the
    command), file tools by path, web tools by host, anything else by the tool's name."""
    if not name:
        return None
    inp = inp if isinstance(inp, dict) else {}
    path = next((inp[k] for k in ("file_path", "notebook_path", "path") if isinstance(inp.get(k), str)), None)
    if name == "Bash":
        d = inp.get("description")
        text = "Bash：" + d if isinstance(d, str) and d.strip() else "Bash"
    elif path:
        text = "%s %s" % (name, path)
    elif isinstance(inp.get("url"), str):
        text = "%s %s" % (name, urllib.parse.urlsplit(inp["url"]).hostname or "?")
    else:
        text = name
    text = " ".join(text.split())
    return text if len(text) <= WHAT_MAX else text[:WHAT_MAX - 1] + "…"


def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except (OSError, OverflowError, TypeError, ValueError):
        return False
    return True


def _claude_pid():
    """The Claude Code process this hook runs for: $CLAUDE_PID when set and alive, else the nearest ancestor
    whose executable lives under a claude directory, else the parent process."""
    v = os.environ.get("CLAUDE_PID", "")
    if v.isdigit() and pid_alive(int(v)):
        return int(v)
    pid = os.getppid()
    for _ in range(8):
        try:
            if "claude" in os.path.realpath("/proc/%d/exe" % pid).lower():
                return pid
            with open("/proc/%d/stat" % pid) as f:
                pid = int(f.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
        if pid <= 1:
            break
    return os.getppid()


def _tmux_name():
    """Name of the tmux session this process runs in, or None."""
    if not os.environ.get("TMUX"):
        return None
    pane = os.environ.get("TMUX_PANE")
    try:
        p = subprocess.run(["tmux", "display-message", "-p"] + (["-t", pane] if pane else []) + ["#S"],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    name = p.stdout.strip()
    return name if p.returncode == 0 and TMUX_RE.match(name) else None


def session_cards(project_dir, refs):
    """[(task id, status)] of the project's task cards whose sessions name any of refs (session ids or names)."""
    refs = {r for r in refs if r}
    d = os.path.join(project_dir, "board", "tasks")
    out = []
    for fn in sorted(os.listdir(d)) if refs and os.path.isdir(d) else []:
        if not fn.endswith(".md") or fn.startswith("."):
            continue
        try:
            meta, _ = P.parse_front_matter(P.read_text(os.path.join(d, fn), 16 * 1024)[0])
        except OSError:
            continue
        if refs & {str(x) for x in P._list(meta.get("sessions"))}:
            out.append((meta["id"] if isinstance(meta.get("id"), str) else fn[:-3], meta.get("status")))
    return out


def waiting_record(data, root=None, now=None):
    """The state of a session that waits for a permission answer, from the Notification hook input; None
    for any other notification, or without a usable session id."""
    kind = data.get("notification_type")
    msg = data.get("message") if isinstance(data.get("message"), str) else ""
    if kind != "permission_prompt" and not (kind is None and "permission" in msg.lower()):
        return None
    sid = data.get("session_id")
    if not (isinstance(sid, str) and SID_RE.match(sid)):
        return None
    cwd = data.get("cwd") if isinstance(data.get("cwd"), str) else os.getcwd()
    proj = find_project(cwd, root) if os.path.isdir(cwd) else None
    tmux = _tmux_name()
    task = None
    if proj:
        m = re.match(r"^task-%s-(.+)$" % re.escape(os.path.basename(proj)), tmux or "")
        if m and os.path.isfile(os.path.join(proj, "board", "tasks", m.group(1) + ".md")):
            task = m.group(1)
        else:
            cards = session_cards(proj, [sid])
            task = next((t for t, s in cards if s in ACTIVE_CARD), cards[-1][0] if cards else None)
    name, inp = last_tool_call(data.get("transcript_path"))
    return {"session": sid, "since": utc(time.time() if now is None else now), "host": socket.gethostname().split(".")[0],
            "pid": _claude_pid(), "cwd": cwd, "project": os.path.basename(proj) if proj else None, "task": task,
            "tmux": tmux, "what": waiting_what(name, inp), "message": " ".join(msg.split())[:200] or None}


def list_waiting(sweep=True):
    """The sessions waiting on this machine, longest first. Files of sessions whose process is gone are
    removed (sweep) and never listed."""
    d = waiting_dir()
    out = []
    for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if not fn.endswith(".json"):
            continue
        path = os.path.join(d, fn)
        try:
            with open(path, encoding="utf-8") as f:
                rec = json.load(f)
        except (OSError, ValueError):
            rec = None
        if not (isinstance(rec, dict) and isinstance(rec.get("pid"), int) and pid_alive(rec["pid"])):
            if sweep:
                try:
                    os.remove(path)
                except OSError:
                    pass
            continue
        out.append(rec)
    return sorted(out, key=lambda r: str(r.get("since")))


def hook_waiting(root, clear=False, now=None):
    """Notification (permission_prompt): record this session as waiting. With clear (after a tool call, a
    prompt, the end of a turn or of the session): remove its record. Silent, always 0."""
    data = _hook_input()
    sid = data.get("session_id")
    if not (isinstance(sid, str) and SID_RE.match(sid)):
        return 0
    path = os.path.join(waiting_dir(), sid + ".json")
    if clear:
        try:
            os.remove(path)
        except OSError:
            pass
        return 0
    rec = waiting_record(data, root, now)
    if rec is None:
        return 0
    os.makedirs(waiting_dir(), mode=0o700, exist_ok=True)
    try:  # a second prompt while the first is still unanswered: keep when the waiting began
        with open(path, encoding="utf-8") as f:
            old = json.load(f)
        if isinstance(old, dict) and old.get("pid") == rec["pid"] and isinstance(old.get("since"), str):
            rec["since"] = old["since"]
    except (OSError, ValueError):
        pass
    write_atomic(path, json.dumps(rec, ensure_ascii=False))
    list_waiting()  # sweep the files of sessions that are gone
    return 0


# ------------------------------------------------------------------ task sessions and the dispatcher
MAX_SESSIONS = 4                  # task and reply sessions running at once, all projects together (config: max_task_sessions)
CODE_KINDS = ("执行", "调试")       # task kinds that change code: one at a time per project, in a worktree
STARTABLE = ("待开始", "可以继续", "进行中")
CLAUDE = os.path.expanduser("~/.local/bin/claude")   # config: claude_bin
SESSION_RE = re.compile(r"^(task|reply)-([A-Za-z0-9][A-Za-z0-9._-]*)$")


TASK_ENV = "RESEARCH_BOARD_TASK"         # "<project>/<task>" in a session task_start started (its commands inherit it)
SESSION_ENV = "CLAUDE_CODE_SESSION_ID"   # Claude Code gives a session's commands the session's id


def calling_task(root, env=None, session_id=None):
    """(project, task) when the caller is a task session, else None: a session task_start started ($RESEARCH_BOARD_TASK),
    or a conversation whose session id (session_id, else $CLAUDE_CODE_SESSION_ID) is on a card it still holds."""
    env = os.environ if env is None else env
    proj, sep, task = str(env.get(TASK_ENV) or "").partition("/")
    if sep and proj and task:
        return proj, task
    sid = session_id or env.get(SESSION_ENV)
    if not (isinstance(sid, str) and SID_RE.match(sid)):
        return None
    for (proj, task), meta in sorted(_task_cards(root).items()):
        if meta.get("status") in ACTIVE_CARD and sid in {str(x) for x in P._list(meta.get("sessions"))}:
            return proj, task
    return None


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


def task_start(root, project, task, run=subprocess.run, model=None, live=None, env=None):
    """Start the session of one task card in the background (tmux, Remote Control). -> {"session", "dir", "worktree",
    "name"}; raises TaskError when it must not start, also when a task session asks (sessions are at most three
    levels deep: project session -> task session -> subagents). live: rows of `claude agents --json` (None: read them).
    env: the caller's environment (default os.environ)."""
    proj = os.path.join(root, project)
    board = os.path.join(proj, "board")
    me = calling_task(root, env)
    if me:
        raise TaskError("任务会话不能再开任务会话：本会话在做 %s 的任务卡 %s。需要另开一张卡的事，写进本卡交接的「下一步」"
                        "或留一条待办，由项目会话或用户来开。" % me)
    if not (P.valid_name(project) and os.path.isfile(os.path.join(board, "project.json"))):
        raise TaskError("没有项目 %s" % project)
    meta, sections = read_task(board, task)
    names = tmux_sessions(run)
    if meta.get("status") in STARTABLE:  # read the live sessions only when the card could start at all
        live = (live_sessions() or []) if live is None else live
    _check_start(root, project, task, meta, busy_tasks(root, names, live or []))
    name = task_session_name(project, task)
    workdir, worktree = _prepare_worktree(proj, task, meta, run)
    snapshot_handoff(project, task, sections, time.time())
    prompt = task_prompt(project, task, meta.get("kind"), workdir, board, "自己的会话名「%s·%s」" % (project, task))
    cmd = [CLAUDE, "--remote-control", "%s·%s" % (project, task)] + (["--model", model] if model else []) + [prompt]
    shell = "unset ANTHROPIC_API_KEY; export %s=%s; exec %s" % (TASK_ENV, _q("%s/%s" % (project, task)), " ".join(_q(x) for x in cmd))
    p = run(["tmux", "new-session", "-d", "-s", name, "-c", workdir, shell], capture_output=True, text=True, timeout=20)
    if p.returncode != 0:
        raise TaskError("tmux 启动失败：%s" % (p.stderr or p.stdout).strip()[-300:])
    return {"session": name, "dir": workdir, "worktree": worktree, "name": "%s·%s" % (project, task)}


def task_begin(root, project, task, session_id=None, live=None, run=subprocess.run, env=None):
    """The conversation the user opened in the project's folder in the App (they said 「做 <task>」) takes this card:
    the same checks and worktree as task_start, and the same steps it would have been started with. A session
    that still holds another card is refused (one card per session); taking its own card again is fine.
    live: rows of `claude agents --json` (None: read them; when they cannot be read only tmux sessions count).
    env: the caller's environment (default os.environ). -> {"workdir", "worktree", "steps"}; raises TaskError
    when the card must not start now."""
    proj = os.path.join(root, project)
    board = os.path.join(proj, "board")
    held = calling_task(root, env, session_id)
    if held and held != (project, task):
        raise TaskError("本会话已经在做任务卡 %s，一个会话只做一张卡。要做 %s，请用户另开一个对话说「做 %s」。" % (held[1], task, task))
    if not (P.valid_name(project) and os.path.isfile(os.path.join(board, "project.json"))):
        raise TaskError("没有项目 %s" % project)
    meta, sections = read_task(board, task)
    note = None
    if live is None and meta.get("status") in STARTABLE:
        live = live_sessions()
        if live is None:
            note = "（读不到 claude agents，同时在做的任务只按 tmux 里的会话算了。）"
    _check_start(root, project, task, meta, busy_tasks(root, tmux_sessions(run), live or []), session_id)
    workdir, worktree = _prepare_worktree(proj, task, meta, run)
    snapshot_handoff(project, task, sections, time.time())
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
        prompt = ("处理回复：只处理用户在控制台勾了「直接处理」的这几条：%s。按 %s 的「处理回复」做："
                  "每条先在 todos.jsonl 追加一行 state: 已领取，做完写 done 和 result；回复有歧义或和现状冲突时不要猜，"
                  "写 state: 受阻 和原因，并留一条待办问用户。其他回复不要动。" % ("、".join(want), FORMAT))
        cmd = [CLAUDE, "-p", "--permission-mode", "auto", "--name", "%s·处理回复" % name, prompt]
        log = os.path.join(os.path.dirname(state_path), "dispatch-%s.log" % name)
        shell = "unset ANTHROPIC_API_KEY; export %s=%s; %s >> %s 2>&1" % (REPLY_ENV, _q(name), " ".join(_q(x) for x in cmd), _q(log))
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


def relay(root, run=subprocess.run, live=None):
    """Cards marked start: auto (the project session wrote it after asking the user) that are 待开始 and whose
    after-cards are all 完成 are started in the background (task_start, so the limit of task sessions holds; one
    whose session already runs is left alone). -> [{"project", "task", "session" or "error"}]"""
    cards = _task_cards(root)
    ready = [(proj, task) for (proj, task), meta in sorted(cards.items())
             if meta.get("start") == "auto" and meta.get("status") == "待开始"
             and all((cards.get((proj, str(a))) or {}).get("status") == "完成" for a in P._list(meta.get("after")))]
    out = []
    for proj, task in ready:
        if task_session_name(proj, task) in tmux_sessions(run):
            continue
        if live is None:
            live = live_sessions() or []
        try:
            out.append({"project": proj, "task": task, "session": task_start(root, proj, task, run=run, live=live, env={})["session"]})
        except TaskError as e:
            out.append({"project": proj, "task": task, "error": str(e)})
    return out


# ------------------------------------------------------------------ end-of-turn check (Stop hook)
# Stop fires at the end of every turn, not only when a session is done, so the check looks at what the turn
# left behind: a task card moved on with the handoff of an earlier session, or work done with an untouched card;
# board/ edited while NOTES.md (the project session's handoff) is hours old. It keeps the session going once,
# with the reason; the session may do it or say why not. The same situation never stops it twice.
STATE_HOME = "~/.local/state/research-board"    # this machine's hook state (never in project boards)
REPLY_ENV = "RESEARCH_BOARD_REPLY"              # set in the sessions dispatch starts to process replies
HANDOFF_DONE = ("完成", "等你", "可以继续", "等运行")
NOTES_STALE = 2 * 3600                          # seconds; NOTES.md older than this after board/ edits: ask once
EDIT_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
STOP_DONE = ("收尾检查：任务卡 {task}（{title}）已经是「{status}」，但交接还是接卡时的样子，这次没有更新。"
             "结束前请在 {path} 的「## 交接」写上这次的五项：做了什么、证据在哪（文件、运行编号、提交号）、没做完的、下一步、要你决定的。"
             "写好后简短告诉用户就可以停下。")
STOP_WORK = ("收尾检查：本会话接手任务卡 {task}（{title}）以后{did}，但任务卡的交接还没动过。"
             "如果这次的工作已经做完，或者要停下来等用户、等运行，先在 {path} 写交接（五项）并改状态（完成 / 等你 / 等运行）；"
             "如果只是停下来问用户、还要接着做，回复一句「还在做」即可。")
STOP_NOTES = ("收尾检查：本会话改过 board/（{files}），而 NOTES.md {age}。换会话时靠 NOTES.md 接上："
              "请把这次定下的事、做到哪、下一步补进 {notes} 的交接。如果这次的改动不值得记，回复一句说明即可。")


def _state_path(*parts):
    return os.path.join(os.path.expanduser(STATE_HOME), *parts)


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def ts_any(s):
    """An ISO 8601 time with or without fractions ('2026-11-27T08:59:12.345Z') -> seconds, or None."""
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (AttributeError, TypeError, ValueError):
        return None


def handoff_sha(sections):
    """Fingerprint of a task card's 交接 (whitespace does not count)."""
    return hashlib.sha256(" ".join(str((sections or {}).get("交接") or "").split()).encode("utf-8")).hexdigest()


def _snapshot_path(project, task):
    return _state_path("handoff", "%s--%s.json" % (project, task))


def snapshot_handoff(project, task, sections, now):
    """When a session takes a card (task-begin, task-start): remember its 交接 as it is, so the Stop hook can tell
    whether this session wrote its own. A failure only means no check later."""
    try:
        path = _snapshot_path(project, task)
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        write_atomic(path, json.dumps({"project": project, "task": task, "sha": handoff_sha(sections), "at": now}))
    except OSError:
        pass


def project_dir_of(path, root):
    """The project a path is in (a task worktree <project>/.worktrees/<task> counts as its project), or None."""
    proj = find_project(path, root) if isinstance(path, str) and os.path.exists(path) else None
    if proj and os.path.basename(os.path.dirname(proj)) == ".worktrees":
        proj = find_project(os.path.dirname(os.path.dirname(proj)), root)
    return proj


def session_card(root, env, session_id):
    """(project, task) of the card this session works on, whatever its status now: $RESEARCH_BOARD_TASK, else a
    card naming the session id (one it still holds first)."""
    proj, sep, task = str(env.get(TASK_ENV) or "").partition("/")
    if sep and proj and task:
        return proj, task
    if not (isinstance(session_id, str) and SID_RE.match(session_id)):
        return None
    hits = sorted((meta.get("status") not in ACTIVE_CARD, key) for key, meta in _task_cards(root).items()
                  if session_id in {str(x) for x in P._list(meta.get("sessions"))})
    return hits[0][1] if hits else None


def task_stop_reason(root, project, task, run=subprocess.run):
    """(situation key, reason) when this task session must not end its turn yet, else None."""
    snap = _load_json(_snapshot_path(project, task))
    board = os.path.join(root, project, "board")
    try:
        meta, sections = read_task(board, task)
    except TaskError:
        return None
    sha = handoff_sha(sections)
    if not snap or snap.get("sha") != sha:       # taken before this check existed, or the handoff was written
        return None
    status, title = meta.get("status"), meta.get("title") or task
    path = os.path.join(board, "tasks", task + ".md")
    key = "%s/%s %s %s" % (project, task, status, sha[:16])
    if status in HANDOFF_DONE:
        return key, STOP_DONE.format(task=task, title=title, status=status, path=path)
    if status != "进行中" or not isinstance(snap.get("at"), (int, float)):
        return None
    wt = os.path.join(root, project, ".worktrees", task)
    ok, out = _git(wt if os.path.isdir(wt) else os.path.join(root, project), "log", "--since=" + utc(snap["at"]), "--format=%H", run=run)
    commits = len(out.split()) if ok and out else 0
    runs = sum(1 for _, _, rec, _ in read_runs(board) if rec.get("task") == task and (iso_ts(rec.get("started")) or 0) >= snap["at"])
    did = "、".join(x for x in ("提交了 %d 次代码" % commits if commits else "", "启动了 %d 次运行" % runs if runs else "") if x)
    return (key, STOP_WORK.format(task=task, title=title, did=did, path=path)) if did else None


def board_edits(transcript, board, max_bytes=32 * 1024 * 1024):
    """[(time or None, path)] of this session's Write/Edit/MultiEdit/NotebookEdit calls on files under board/."""
    prefix = os.path.realpath(board) + os.sep
    try:
        with open(transcript, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - max_bytes))
            lines = f.read().decode("utf-8", "replace").split("\n")
    except (OSError, TypeError, ValueError):
        return []
    out = []
    for line in lines:
        if '"tool_use"' not in line or "/board/" not in line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        msg = row.get("message") if isinstance(row, dict) else None
        content = msg.get("content") if isinstance(msg, dict) else None
        for c in content if isinstance(content, list) else []:
            inp = c.get("input") if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") in EDIT_TOOLS else None
            fp = (inp.get("file_path") or inp.get("notebook_path")) if isinstance(inp, dict) else None
            if isinstance(fp, str) and os.path.realpath(fp).startswith(prefix):
                out.append((ts_any(row.get("timestamp")), fp))
    return out


def notes_stop_reason(proj, transcript, now):
    """The reason when this session edited board/ after NOTES.md last changed and NOTES.md is older than
    NOTES_STALE, else None."""
    edits = board_edits(transcript, os.path.join(proj, "board"))
    notes = os.path.join(proj, "NOTES.md")
    try:
        changed = os.stat(notes).st_mtime
    except OSError:
        changed = None
    after = sorted({os.path.relpath(p, proj) for t, p in edits if changed is None or (t or 0) > changed})
    if not after or (changed is not None and now - changed < NOTES_STALE):
        return None
    files = "%d 个文件：%s" % (len(after), "、".join(after[:3]) + ("等" if len(after) > 3 else ""))
    age = "还不存在" if changed is None else "已经 %d 小时没更新" % int((now - changed) // 3600)
    return STOP_NOTES.format(files=files, age=age, notes=notes)


def stop_check(root, data, now, env=None, run=subprocess.run):
    """The Stop hook's decision: a reason to keep the session going once (shown to the session), or None.
    - Task session (session_card): its card is 完成 / 等你 / 可以继续 / 等运行 while the 交接 is as it was when the
      session took the card; or the card is 进行中 and since then the session committed code or started runs with
      the 交接 untouched. Cards taken before this check existed have no snapshot and are never checked.
    - Any other session in a project, except reply sessions: notes_stop_reason, at most once per NOTES_STALE.
    Never while stop_hook_active (the session already continues because of a Stop hook); never twice for the
    same situation (STATE_HOME/stop/<session id>.json)."""
    env = os.environ if env is None else env
    sid = data.get("session_id")
    if data.get("stop_hook_active") or not (isinstance(sid, str) and SID_RE.match(sid)):
        return None
    proj = project_dir_of(data.get("cwd"), root)
    if not proj and not env.get(TASK_ENV):
        return None
    path = _state_path("stop", sid + ".json")
    state = _load_json(path) or {}
    seen = state.get("seen") if isinstance(state.get("seen"), dict) else {}
    card = session_card(root, env, sid)
    if card:
        got = task_stop_reason(root, card[0], card[1], run=run)
        if not got or got[0] in seen:
            return None
        key, reason = got
    else:
        last = seen.get("notes")
        if env.get(REPLY_ENV) or not proj or (isinstance(last, (int, float)) and now - last < NOTES_STALE):
            return None
        key, reason = "notes", notes_stop_reason(proj, data.get("transcript_path"), now)
        if not reason:
            return None
    seen[key] = now
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        write_atomic(path, json.dumps({"seen": seen}, ensure_ascii=False))
    except OSError:
        return None  # could not remember it: better not to stop the session than to stop it every turn
    return reason


def hook_stop(root):
    """Stop: print {"decision": "block", "reason": ...} when stop_check says so; silent otherwise. Always 0."""
    reason = stop_check(root, _hook_input(), time.time())
    if reason:
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    return 0


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
    dp = sub.add_parser("dispatch", help="start a session for replies marked 直接处理; start cards marked start: auto")
    dp.add_argument("--dry-run", action="store_true")
    sv = sub.add_parser("serve", help="start and stop the services task sessions asked for (board/services/)")
    sv.add_argument("--dry-run", action="store_true")
    sc = sub.add_parser("stop-check", help="Stop hook: keep a session going once when its handoff is missing")
    sc.add_argument("--hook", action="store_true", required=True, help="read the Stop hook input on stdin; never fail")
    w = sub.add_parser("waiting", help="sessions on this machine waiting for a permission answer")
    w.add_argument("--hook", action="store_true", help="Notification hook input on stdin: record this session; never fail")
    w.add_argument("--clear", action="store_true", help="with --hook: drop this session's record")
    w.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "serve":
        rc = rbconfig.load()
        for c in SV.serve(rc["projects_root"], rc, time.time(), dry_run=a.dry_run):
            print("%s %s%s %s: %s" % (utc(time.time()), "(dry run) " if a.dry_run else "", c["project"], c["file"], c["what"]))
        return 0
    if a.cmd == "stop-check":
        try:
            return hook_stop(load_config()["root"])
        except Exception:  # a hook must never get in the way of a session
            return 0
    if a.cmd == "waiting":
        if a.hook:
            try:
                root = None
                if not a.clear:
                    try:
                        root = load_config()["root"]
                    except Exception:  # without the configuration the record just names no project
                        pass
                return hook_waiting(root, clear=a.clear)
            except Exception:  # a hook must never get in the way of a session
                return 0
        if a.clear:
            ap.error("--clear goes with --hook")
        rows = list_waiting()
        if a.json:
            print(json.dumps(rows, ensure_ascii=False))
        for r in [] if a.json else rows:
            print("%s 起在等 · %s · %s · %s" % (r.get("since"), "/".join(x for x in (r.get("project"), r.get("task")) if x) or r.get("cwd"),
                                             r.get("what") or r.get("message") or "?", r.get("session", "")[:8]))
        return 0
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
                for x in [] if a.dry_run else relay(cfg["root"]):
                    print("%s %s: 接力开卡 %s：%s" % (utc(time.time()), x["project"], x["task"], x.get("session") or "没开成，" + x["error"]))
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
