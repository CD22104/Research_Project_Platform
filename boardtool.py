"""board: command-line tools around the project boards (run on xtraa100; standard library only).

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


# ------------------------------------------------------------------ command line
def main(argv=None):
    ap = argparse.ArgumentParser(prog="board", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("reconcile", help="close dead runs, move waiting tasks on")
    r.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
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
