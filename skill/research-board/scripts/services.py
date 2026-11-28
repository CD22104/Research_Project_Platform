"""Services a task session asks for (a vLLM server on a GPU, say), started and always stopped by a script.

A task session cannot start a long-lived service itself (its permission checks stop it), and nobody should wake
up every 20 minutes to do it. So the session writes a request, board/services/<name>.json, and `board serve`
(cron on the console's machine, every 5 minutes) does the rest:

  request  board/services/<name>.json         template, model, served_name, max_model_len, gpus, server, task,
                                              hours, note: nothing else, and never a command
  status   board/services/<name>.status.json  written here: 排队 / 暂缓 / 启动中 / 就绪 / 拒绝 (and 已停 / 失败,
                                              moved to closed/ with the rest)
  done     board/services/<name>.done         written by the requester when it no longer needs the service

The command comes from a template in the personal configuration (services), which the user approved; values from
the request are shell-quoted into it. A project may use at most the GPUs its latest adopted decision allows
(gpu_limit: [gpu-a100 4, gpu-h100 all]); without one nothing starts. Every service is stopped, like a defer, when
its requester is done, the request is gone, the task card is 完成 / 放弃 / 暂停, its hours are up, or it died.
One pass: one ssh per server; plan() decides, apply() acts. Standard library only.
"""
import datetime
import fcntl
import json
import os
import re
import shlex
import socket
import subprocess

import projects as P
import rbconfig

FIELDS = ("template", "model", "served_name", "max_model_len", "gpus", "server", "task", "hours", "note")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
QUEUED, HELD, STARTING, READY, STOPPED, FAILED, REFUSED = "排队", "暂缓", "启动中", "就绪", "已停", "失败", "拒绝"
ACTIVE = (STARTING, READY)
STOP_CARD = ("完成", "放弃", "暂停")   # a requester's card in one of these no longer needs its service
HOURS_DEFAULT, HOURS_MAX = 12, 24
MAX_GPUS = 8
START_TIMEOUT = 15 * 60               # seconds a service may take to answer its health check
FREE_MB = 1024                        # a card using less memory than this counts as free
WAITING = "$HOME/.local/state/research-board/waiting"   # boardtool.WAITING_DIR: sessions waiting for approval
LOG_DIR = "$HOME/.local/state/research-board/services"   # the services' output, on the server they run on
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "LogLevel=ERROR"]


def utc(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ts(s):
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write(path, obj):
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False, indent=1) + "\n")
    os.replace(tmp, path)


# ------------------------------------------------------------------ what is asked for
def read_requests(board):
    """[{"name", "req" (dict, or None when there is no request file), "bad" (why the file cannot be read, or None),
    "status" (dict or None), "done" (bool)}] for every name with a request or a status in board/services/."""
    d = os.path.join(board, "services")
    names = set()
    for fn in os.listdir(d) if os.path.isdir(d) else []:
        if fn.startswith(".") or ".tmp." in fn:
            continue
        for suffix in (".status.json", ".done", ".json"):
            if fn.endswith(suffix):
                names.add(fn[:-len(suffix)])
                break
    out = []
    for name in sorted(names):
        path, req, bad = os.path.join(d, name + ".json"), None, None
        if os.path.exists(path):
            req = _load(path)
            if not isinstance(req, dict):
                req, bad = {}, "申请不是一个 JSON 对象"
        status = _load(os.path.join(d, name + ".status.json"))
        out.append({"name": name, "req": req, "bad": bad, "status": status if isinstance(status, dict) else None,
                    "done": os.path.exists(os.path.join(d, name + ".done"))})
    return out


def _int(x, lo, hi):
    return isinstance(x, int) and not isinstance(x, bool) and lo <= x <= hi


def server_name(x, cfg):
    """A server as the configuration names it, from its full or short name; None when unknown."""
    names = [s["name"] for s in cfg["servers"]]
    short = {v: k for k, v in rbconfig.short_names(names).items()}
    return x if x in names else short.get(x)


def refusal(name, req, cfg, board):
    """Why this request cannot be served (one sentence for the requester), or None."""
    if not NAME_RE.match(name):
        return "名字「%s」只能用字母、数字和 . _ -" % name
    odd = sorted(set(req) - set(FIELDS))
    if odd:
        return "申请里有不认识的字段：%s（只能写 %s）" % ("、".join(odd), "、".join(FIELDS))
    if req.get("template") not in cfg["services"]:
        return "模板「%s」不在个人配置的 services 里（有：%s）" % (req.get("template"), "、".join(sorted(cfg["services"])) or "没有")
    models = os.path.realpath(os.path.join(cfg["shared_dir"], "models")) + os.sep
    m = req.get("model")
    if not (isinstance(m, str) and os.path.isabs(m) and os.path.realpath(m).startswith(models)):
        return "model 要写共享目录 models/ 下的完整路径（%s）" % models.rstrip(os.sep)
    if not os.path.exists(m):
        return "模型 %s 不存在" % m
    if not (isinstance(req.get("served_name"), str) and NAME_RE.match(req["served_name"])):
        return "served_name 只能用字母、数字和 . _ -"
    if not _int(req.get("max_model_len"), 1, 1 << 20):
        return "max_model_len 要写正整数"
    if "gpus" in req and not _int(req["gpus"], 1, MAX_GPUS):
        return "gpus 是卡数，写 1 到 %d" % MAX_GPUS
    h = req.get("hours", HOURS_DEFAULT)
    if not (isinstance(h, (int, float)) and not isinstance(h, bool) and 0 < h <= HOURS_MAX):
        return "hours 是最长占用的小时数，写 0 到 %d 之间" % HOURS_MAX
    if req.get("server") is not None and not server_name(req["server"], cfg):
        return "server「%s」不在配置的服务器里" % req["server"]
    t = req.get("task")
    if not (isinstance(t, str) and P.valid_name(t) and os.path.isfile(os.path.join(board, "tasks", t + ".md"))):
        return "task 要写申请它的任务卡 id（board/tasks/<id>.md 要存在）"
    return None


def gpu_limit(decisions, answers, cfg):
    """({server: cards, or None for all of them}, problems) from the latest adopted decision that has gpu_limit
    (items "<server> <n>" or "<server> all"); (None, problems) when no adopted decision says it."""
    states = P.decision_states(decisions, answers)
    have = [r for r in decisions if states.get((r.get("meta") or {}).get("id")) == "采纳" and (r.get("meta") or {}).get("gpu_limit")]
    if not have:
        return None, []
    m = max(have, key=lambda r: (str(r["meta"].get("date") or ""), str(r["meta"].get("id"))))["meta"]
    out, problems = {}, []
    for item in P._list(m.get("gpu_limit")):
        parts = str(item).split()
        srv = server_name(parts[0], cfg) if len(parts) == 2 else None
        if srv is None or not (parts[1] == "all" or parts[1].isdigit()):
            problems.append("决定 %s 的 gpu_limit 里「%s」看不懂（写「服务器 卡数」或「服务器 all」）" % (m.get("id"), item))
            continue
        out[srv] = None if parts[1] == "all" else int(parts[1])
    return out, problems


# ------------------------------------------------------------------ what the servers look like
def _probe_script(health):
    lines = ["echo @@@GPU", "nvidia-smi --query-gpu=index,memory.used --format=csv,noheader,nounits 2>/dev/null",
             "echo @@@TMUX", "tmux list-sessions -F '#{session_name}' 2>/dev/null",
             "echo @@@PORTS", "ss -ltnH 2>/dev/null | awk '{print $4}'",
             "echo @@@WAIT", 'for f in "%s"/*.json; do [ -f "$f" ] && head -c 4096 "$f" | tr -d "\\n" && echo; done' % WAITING,
             "echo @@@HEALTH"]
    for key, url in sorted(health.items()):
        lines.append("echo %s $(curl -s -o /dev/null -m 5 -w '%%{http_code}' %s)" % (shlex.quote(key), shlex.quote(url)))
    return "\n".join(lines + ["echo @@@END"])


def parse_probe(text):
    """The output of _probe_script -> {"ok", "gpus": {index: MiB used}, "tmux": set, "ports": set, "waiting": [records],
    "health": {key: True/False}}; ok is False when the output did not come back whole."""
    out = {"ok": "@@@END" in (text or ""), "gpus": {}, "tmux": set(), "ports": set(), "waiting": [], "health": {}}
    part = None
    for line in (text or "").split("\n"):
        if line.startswith("@@@"):
            part = line[3:].strip()
            continue
        line = line.strip()
        if not line:
            continue
        if part == "GPU":
            c = [x.strip() for x in line.split(",")]
            if len(c) == 2 and c[0].isdigit() and c[1].isdigit():
                out["gpus"][int(c[0])] = int(c[1])
        elif part == "TMUX":
            out["tmux"].add(line)
        elif part == "PORTS":
            p = line.rsplit(":", 1)[-1]
            if p.isdigit():
                out["ports"].add(int(p))
        elif part == "WAIT":
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict):
                out["waiting"].append(r)
        elif part == "HEALTH":
            key, _, code = line.partition(" ")
            out["health"][key] = code.strip() == "200"
    return out


def probe(servers, health, cfg, run=subprocess.run, here=None):
    """{server: parse_probe(...)}: one connection per server (the console's machine runs it locally).
    health: {server: {key: url}} of services waiting for their health check."""
    here = here or socket.gethostname().split(".")[0]
    hosts = {s["name"]: s["host"] for s in cfg["servers"]}
    out = {}
    for srv in servers:
        script = _probe_script(health.get(srv) or {})
        cmd = ["bash", "-c", script] if srv == here else SSH + [hosts[srv], script]
        try:
            p = run(cmd, capture_output=True, text=True, timeout=60)
            text = p.stdout if p.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            text = None
        out[srv] = parse_probe(text)
    return out


# ------------------------------------------------------------------ deciding
def plan(projects, probes, cfg, now):
    """What to do with every request, as [{"project", "name", "do", "reason", ...}], deciding nothing twice and
    running nothing. projects: [{"name", "board", "requests" (read_requests), "limit" ({server: n or None} or None),
    "cards" ({task id: status}), "runs" ([(server, [cards])] of its runs that are 运行中)}].
    do: keep / ready / stop / fail / close / refuse / no_limit / hold / queue / start."""
    acts = []
    taken = {}                                   # server -> cards given out in this pass
    ports = {}                                   # server -> ports given out in this pass
    order = [s["name"] for s in cfg["servers"]]
    for p in projects:
        used = {}                                # server -> cards this project holds (running runs, live services)
        for srv, cards in p["runs"]:
            used[srv] = used.get(srv, 0) + len(cards)
        for r in p["requests"]:
            st = r["status"] or {}
            if st.get("state") in ACTIVE and st.get("server"):
                used[st["server"]] = used.get(st["server"], 0) + len(st.get("gpus") or [])
        for r in p["requests"]:
            name, req, st = r["name"], r["req"], r["status"] or {}
            base = {"project": p["name"], "board": p["board"], "name": name, "req": req, "status": st}
            card = p["cards"].get((req or {}).get("task") or st.get("task"))
            if st.get("state") in ACTIVE:
                pr = probes.get(st.get("server")) or {}
                started = ts(st.get("started")) or now
                hours = (req or {}).get("hours", st.get("hours", HOURS_DEFAULT))
                why = ("申请方写了 .done" if r["done"] else "申请文件删掉了" if req is None
                       else "任务卡 %s 不在了" % req.get("task") if card is None
                       else "任务卡已经是「%s」" % card if card in STOP_CARD
                       else "超过了申请的 %s 小时" % hours if isinstance(hours, (int, float)) and now - started > hours * 3600 else None)
                if not pr.get("ok"):
                    acts.append(dict(base, do="keep", reason="%s 这一轮连不上" % st.get("server")))
                elif st.get("tmux") not in pr["tmux"]:
                    acts.append(dict(base, do="fail", reason="服务进程没了（tmux %s 不在）" % st.get("tmux")))
                elif why:
                    acts.append(dict(base, do="stop", reason=why))
                elif st.get("state") == STARTING and pr["health"].get(name):
                    acts.append(dict(base, do="ready"))
                elif st.get("state") == STARTING and now - started > START_TIMEOUT:
                    acts.append(dict(base, do="fail", reason="%d 分钟还没通过健康检查" % (START_TIMEOUT // 60)))
                else:
                    acts.append(dict(base, do="keep"))
                continue
            if req is None or r["done"] or (card in STOP_CARD):
                why = "申请文件删掉了" if req is None else "申请方写了 .done" if r["done"] else "任务卡已经是「%s」" % card
                acts.append(dict(base, do="close", reason="还没起就不用了：" + why))
                continue
            no = r["bad"] or refusal(name, req, cfg, p["board"])
            if no:
                acts.append(dict(base, do="refuse", reason=no))
                continue
            if p["limit"] is None:
                acts.append(dict(base, do="no_limit", reason="没有已采纳的决定写着本项目最多占几张卡（gpu_limit）"))
                continue
            if any(w.get("project") == p["name"] and w.get("task") == req["task"]
                   for pr in probes.values() for w in pr.get("waiting") or []):
                acts.append(dict(base, do="hold", reason="申请它的任务卡 %s 的会话在等权限批准，先不起，免得空占卡" % req["task"]))
                continue
            want = req.get("gpus", 1)
            servers = [server_name(req["server"], cfg)] if req.get("server") else [s for s in order if s in p["limit"]]
            why_not, chosen = [], None
            for srv in servers:
                pr = probes.get(srv) or {}
                if srv not in p["limit"]:
                    why_not.append("%s 不在允许的服务器里" % srv)
                    continue
                if not pr.get("ok"):
                    why_not.append("%s 连不上" % srv)
                    continue
                cap = p["limit"][srv] if p["limit"][srv] is not None else len(pr["gpus"])
                free = [i for i in sorted(pr["gpus"]) if pr["gpus"][i] < FREE_MB and i not in taken.get(srv, set())]
                if used.get(srv, 0) + want > cap:
                    why_not.append("%s 上本项目已占 %d 张，上限 %d" % (srv, used.get(srv, 0), cap))
                    continue
                if len(free) < want:
                    why_not.append("%s 空卡 %d 张，要 %d 张" % (srv, len(free), want))
                    continue
                lo, hi = cfg["services"][req["template"]]["ports"]
                port = next((x for x in range(lo, hi + 1) if x not in pr["ports"] and x not in ports.get(srv, set())), None)
                if port is None:
                    why_not.append("%s 上 %d–%d 的端口都占满了" % (srv, lo, hi))
                    continue
                chosen = (srv, free[:want], port)
                break
            if not chosen:
                acts.append(dict(base, do="queue", reason="；".join(why_not) or "没有可用的服务器"))
                continue
            srv, cards, port = chosen
            taken.setdefault(srv, set()).update(cards)
            ports.setdefault(srv, set()).add(port)
            used[srv] = used.get(srv, 0) + len(cards)
            acts.append(dict(base, do="start", server=srv, gpus=cards, port=port, tmux="svc-%s-%s" % (p["name"], name)))
    return acts


# ------------------------------------------------------------------ acting
def command(template, req, gpus, port):
    """The service's shell command: the template with the request's values, each shell-quoted."""
    vals = {"gpus": ",".join(str(g) for g in gpus), "model": req["model"], "served_name": req["served_name"],
            "port": port, "max_model_len": req["max_model_len"]}
    return template["command"].format(**{k: shlex.quote(str(v)) for k, v in vals.items()})


def _on(srv, script, cfg, run, here):
    hosts = {s["name"]: s["host"] for s in cfg["servers"]}
    cmd = ["bash", "-c", script] if srv == here else SSH + [hosts[srv], script]
    try:
        p = run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    return p.returncode == 0, ((p.stderr or "") + (p.stdout or "")).strip()[-300:]


def _close(board, name, now):
    """Move the request, its status and .done into services/closed/ (a suffix keeps older ones)."""
    d, c = os.path.join(board, "services"), os.path.join(board, "services", "closed")
    os.makedirs(c, exist_ok=True)
    for suffix in (".json", ".status.json", ".done"):
        src = os.path.join(d, name + suffix)
        if os.path.exists(src):
            dst = os.path.join(c, name + suffix)
            if os.path.exists(dst):
                dst = os.path.join(c, "%s-%s%s" % (name, utc(now).replace(":", ""), suffix))
            os.replace(src, dst)


def _todo(board, row):
    path = os.path.join(board, "todos.jsonl")
    have = P.read_todos(path)["items"] if os.path.isfile(path) else []
    if any(str(t.get("id")) == row["id"] for t in have):
        return False
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return True


def apply(acts, cfg, now, run=subprocess.run, here=None):
    """Carry out plan(): start and stop over ssh, write the status files, move finished ones to closed/, write the
    few to-dos (a service failed; no gpu_limit). Only tmux sessions this script started (svc-*, named in a status
    file) are ever stopped. -> [{"project", "file", "what"}]"""
    here = here or socket.gethostname().split(".")[0]
    changes = []
    for a in acts:
        board, name, st, req = a["board"], a["name"], dict(a["status"]), a["req"] or {}
        spath = os.path.join(board, "services", name + ".status.json")
        what = None
        if a["do"] == "keep":
            if a.get("reason") and st.get("note") != a["reason"]:
                st["note"] = a["reason"]
                _write(spath, st)
            elif not a.get("reason") and st.get("note"):
                st.pop("note")
                _write(spath, st)
            continue
        if a["do"] in ("stop", "fail"):
            tm = st.get("tmux")
            if isinstance(tm, str) and tm.startswith("svc-") and P.valid_name(tm):
                _on(st.get("server"), "tmux kill-session -t %s 2>/dev/null; true" % shlex.quote("=" + tm), cfg, run, here)
            st.update(state=STOPPED if a["do"] == "stop" else FAILED, stopped=utc(now), reason=a["reason"])
            st.pop("note", None)
            _write(spath, st)
            _close(board, name, now)
            what = "%s 服务 %s（%s %s 号卡）：%s" % ("关掉" if a["do"] == "stop" else "失败，已关掉", name, st.get("server"),
                                               ",".join(map(str, st.get("gpus") or [])), a["reason"])
            if a["do"] == "fail":
                _todo(board, {"id": "svc-failed-%s-%s" % (name, (st.get("started") or "").replace(":", "")), "kind": "whenever",
                              "text": "服务 %s 失败了：%s" % (name, a["reason"]), "created": utc(now), "done": None,
                              "by": "board serve", "link": "tasks/%s" % st.get("task") if st.get("task") else None,
                              "context": "%s %s 号卡，日志在那台的 %s/%s-%s.log。申请方要用的话，重新写一份申请。"
                                         % (st.get("server"), ",".join(map(str, st.get("gpus") or [])), LOG_DIR, a["project"], name)})
        elif a["do"] == "ready":
            st.update(state=READY, ready_at=utc(now))
            st.pop("note", None)
            _write(spath, st)
            what = "服务 %s 就绪：%s" % (name, st.get("url"))
        elif a["do"] == "close":
            st.update(state=STOPPED, stopped=utc(now), reason=a["reason"])
            _write(spath, st)
            _close(board, name, now)
            what = "服务 %s：%s" % (name, a["reason"])
        elif a["do"] in ("refuse", "no_limit", "hold", "queue"):
            state = {"refuse": REFUSED, "no_limit": REFUSED, "hold": HELD, "queue": QUEUED}[a["do"]]
            if st.get("state") == state and st.get("reason") == a["reason"]:
                continue                                   # nothing new: leave the file alone
            st = {"state": state, "reason": a["reason"], "task": req.get("task"), "since": utc(now)}
            _write(spath, st)
            what = "服务 %s：%s（%s）" % (name, state, a["reason"])
            if a["do"] == "no_limit":
                _todo(board, {"id": "svc-no-gpu-limit", "kind": "decision", "created": utc(now), "done": None, "by": "board serve",
                              "text": "有任务申请了服务（%s），但没有已采纳的决定写着本项目最多占几张卡，所以没有起" % name,
                              "context": "在一条决定的字段区写 gpu_limit，例如 `gpu_limit: [gpu-a100 4, gpu-h100 all]`，"
                                         "你在控制台采纳后，下一轮（5 分钟内）就会起。",
                              "link": "decisions"})
        elif a["do"] == "start":
            tpl = cfg["services"][req["template"]]
            log = "%s/%s-%s.log" % (LOG_DIR, a["project"], name)
            try:
                body = command(tpl, req, a["gpus"], a["port"]).strip().rstrip(";")   # all of it to the log, however many commands
            except (KeyError, IndexError, ValueError) as e:                         # rbconfig checks this; never stop the pass
                ok, out = False, "模板「%s」填不进去：%s" % (req["template"], e)
            else:
                script = "mkdir -p %s && tmux new-session -d -s %s %s" % (
                    LOG_DIR, shlex.quote(a["tmux"]), shlex.quote("{ %s; } >> %s 2>&1" % (body, log)))
                ok, out = _on(a["server"], script, cfg, run, here)
            st = {"state": STARTING if ok else FAILED, "server": a["server"], "gpus": a["gpus"], "port": a["port"],
                  "url": tpl["health"].format(port=a["port"]).rsplit("/models", 1)[0] if "/models" in tpl["health"] else
                  "http://127.0.0.1:%d" % a["port"], "tmux": a["tmux"], "task": req["task"], "template": req["template"],
                  "hours": req.get("hours", HOURS_DEFAULT), "started": utc(now), "reason": None if ok else "启动失败：" + out}
            _write(spath, st)
            what = "起服务 %s：%s %s 号卡，端口 %d%s" % (name, a["server"], ",".join(map(str, a["gpus"])), a["port"], "" if ok else "（失败：%s）" % out)
            if not ok:
                _close(board, name, now)
                _todo(board, {"id": "svc-failed-%s-%s" % (name, st["started"].replace(":", "")), "kind": "whenever",
                              "text": "服务 %s 没能启动：%s" % (name, out or "没有输出"), "created": utc(now), "done": None,
                              "by": "board serve", "link": "tasks/%s" % req["task"],
                              "context": "%s 上的 tmux 没起来。申请方要用的话，修好以后重新写一份申请。" % a["server"]})
        if what:
            changes.append({"project": a["project"], "file": "services/%s" % name, "what": what})
    return changes


# ------------------------------------------------------------------ one pass
def serve(root, cfg, now, run=subprocess.run, dry_run=False, here=None):
    """One pass over every project's services (cron, every 5 minutes). -> changes (with dry_run: what would be
    done). Skips (returns []) while an earlier pass still holds the lock."""
    os.makedirs(cfg["state_dir"], exist_ok=True)
    lock = open(os.path.join(cfg["state_dir"], "serve.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        return [{"project": "-", "file": "-", "what": "上一轮还没跑完，这一轮跳过"}]
    try:
        projects = []
        for name in sorted(os.listdir(root)):
            board = os.path.join(root, name, "board")
            if not (P.valid_name(name) and os.path.isdir(os.path.join(board, "services"))):
                continue
            reqs = read_requests(board)
            if not reqs:
                continue
            d = P.scan_project(board)
            limit, _ = gpu_limit(d.get("decisions") or [], d.get("answers") or {}, cfg)
            runs = [(r["run"].get("server"), [g for g in r["run"].get("gpus") or []]) for r in d.get("runs") or []
                    if isinstance(r.get("run"), dict) and r["run"].get("status") == "运行中" and isinstance(r["run"].get("gpus"), list)]
            projects.append({"name": name, "board": board, "requests": reqs, "limit": limit, "runs": runs,
                             "cards": {t["meta"].get("id"): t["meta"].get("status") for t in d.get("tasks") or []}})
        if not projects:
            return []
        known = [s["name"] for s in cfg["servers"]]
        want, health = set(), {}
        if cfg.get("home_server"):
            want.add(cfg["home_server"])           # sessions waiting for approval are seen there
        for p in projects:
            if any(r["req"] is not None and not r["done"] and (r["status"] or {}).get("state") not in ACTIVE for r in p["requests"]):
                want.update(s for s in (p["limit"] or {}) if s in known)      # somewhere to start a request that waits
            for r in p["requests"]:
                st = r["status"] or {}
                if st.get("state") in ACTIVE and st.get("server") in known:
                    want.add(st["server"])
                    if st.get("state") == STARTING and st.get("template") in cfg["services"]:
                        health.setdefault(st["server"], {})[r["name"]] = cfg["services"][st["template"]]["health"].format(port=st.get("port"))
        probes = probe(sorted(want, key=known.index), health, cfg, run=run, here=here)
        acts = plan(projects, probes, cfg, now)
        if dry_run:
            return [{"project": a["project"], "file": "services/" + a["name"], "what": "%s %s" % (a["do"], a.get("reason") or "")}
                    for a in acts if a["do"] != "keep"]
        return apply(acts, cfg, now, run=run, here=here)
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
