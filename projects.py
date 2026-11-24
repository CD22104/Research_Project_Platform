"""Project boards: parse each project's board/ directory on the shared disk.

Read-only. Understands record format v0 (see a project's board/FORMAT.md) and is
deliberately lenient: missing fields, extra fields and missing files must never
raise; callers get empty values instead.
"""
import json
import os
import re
import stat
import sys
import threading
import time
import traceback

MAX_BODY = 200 * 1024  # bytes of a Markdown file kept in the cache
HEAD_ROWS = 20         # rows kept from samples.jsonl / probe.jsonl
MAX_POINTS = 1000      # points kept per metrics.jsonl after downsampling

_COMMENT = re.compile(r"\s+#.*$")  # " # ..." ends a front-matter line; "C#" is kept


def _read_bytes(path, limit=None):
    """Read a file (at most limit bytes). Single choke point for disk reads."""
    with open(path, "rb") as f:
        return f.read() if limit is None else f.read(limit)


def read_text(path, limit=MAX_BODY):
    """Return (text, truncated). Invalid UTF-8 is replaced, never raised."""
    data = _read_bytes(path, limit + 1)
    return data[:limit].decode("utf-8", "replace"), len(data) > limit


def parse_value(raw):
    """Front-matter value: 'null' or empty -> None, '[a, b]' -> list, else text."""
    v = raw.strip()
    if v in ("", "null"):
        return None
    if v.startswith("[") and v.endswith("]"):
        return [x.strip() for x in v[1:-1].split(",") if x.strip()]
    return v


def parse_front_matter(text):
    """Split '---' / 'key: value' lines / '---' from the body.

    Without an opening '---', or without a closing one, the whole text is body.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text
    for end in range(1, len(lines)):
        if lines[end].strip() == "---":
            break
    else:
        return {}, text
    meta = {}
    for line in lines[1:end]:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        key, sep, val = _COMMENT.sub("", s).partition(":")
        if sep and key.strip():
            meta[key.strip()] = parse_value(val)
    return meta, "\n".join(lines[end + 1:])


def split_sections(body):
    """Map each '## title' to the text under it (fenced code blocks respected)."""
    sections, title, buf, fenced = {}, None, [], False
    for line in body.split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        if not fenced and line.startswith("## "):
            if title is not None:
                sections[title] = "\n".join(buf).strip()
            title, buf = line[3:].strip(), []
        elif title is not None:
            buf.append(line)
    if title is not None:
        sections[title] = "\n".join(buf).strip()
    return sections


DONE = ("成功", "部分成功", "失败", "放弃", "待确认")  # attempt statuses that carry a conclusion

# The "一页纸" at the top of an attempt: five short plain-language lines the user reads first.
ONEPAGER = ("想回答", "怎么做", "预期", "结果", "所以")
ONEPAGER_MAX = 150        # characters of a label's first line ("at most two sentences")
ONEPAGER_EXTRA_MAX = 300  # characters of extra non-table text under one label
ONEPAGER_TABLE_ROWS = 10  # table rows allowed under one label (e.g. a small ranking table under 结果)
_OP_LINE = re.compile(r"^\s*[-*]?\s*\**\s*(%s)\s*\**\s*[:：]\s*\**\s*(.*)$" % "|".join(ONEPAGER))


def parse_onepager(text):
    """The 一页纸 section -> {label: {"text": first line, "extra": following lines}}; unknown lines before
    the first label are ignored. Never raises."""
    out, cur = {}, None
    for line in (text or "").split("\n"):
        m = _OP_LINE.match(line)
        if m:
            cur = m.group(1)
            out[cur] = {"text": m.group(2).strip().rstrip("*").strip(), "extra": []}
        elif cur is not None and line.strip():
            out[cur]["extra"].append(line.rstrip())
    return {k: {"text": v["text"], "extra": "\n".join(v["extra"])} for k, v in out.items()}


def onepager_problems(meta, onepager, has_section):
    """What is missing or too long in an attempt's 一页纸 (list of messages)."""
    if not has_section:
        return ["没有「一页纸」一节（想回答 / 怎么做 / 预期 / 结果 / 所以，每项一两句白话）"]
    need = ["想回答", "怎么做", "预期"] + (["结果", "所以"] if meta.get("status") in DONE else [])
    out = ["「一页纸」缺「%s」" % k for k in need if not (onepager.get(k) or {}).get("text")]
    for k in ONEPAGER:
        v = onepager.get(k)
        if not v:
            continue
        if len(v["text"]) > ONEPAGER_MAX:
            out.append("「一页纸」的「%s」太长（%d 字，上限 %d）：只写一两句，细节放到下面各节" % (k, len(v["text"]), ONEPAGER_MAX))
        lines = v["extra"].split("\n") if v["extra"] else []
        rows = [x for x in lines if x.lstrip().startswith("|")]
        prose = sum(len(x.strip()) for x in lines if not x.lstrip().startswith("|"))
        if len(rows) > ONEPAGER_TABLE_ROWS + 2:  # header and separator rows do not count
            out.append("「一页纸」的「%s」下的表格太长（%d 行）：一页纸只放最能说明问题的一小张表" % (k, len(rows)))
        if prose > ONEPAGER_EXTRA_MAX:
            out.append("「一页纸」的「%s」下面的文字太长（%d 字）：细节放到下面各节" % (k, prose))
    return out


def needs_input(meta, sections):
    """Labels of what the user still has to fill in for an attempt."""
    out = []
    if not sections.get("预期", "").strip():
        out.append("预期")
    if meta.get("status") in DONE and not sections.get("结论和原因", "").strip():
        out.append("结论和原因")
    if meta.get("status") == "待确认":
        out.append("结论待确认")
    return out


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _complete_lines(path):
    """Yield complete lines (bytes); a last line without newline is still being written."""
    with open(path, "rb") as f:
        for line in f:
            if line.endswith(b"\n"):
                yield line


def read_metrics(path, max_points=MAX_POINTS):
    """Parse metrics.jsonl into a bounded, downsampled series.

    Returns {keys, points, total, bad_lines, downsampled}; points are dicts with
    'step' and numeric values only. Memory stays bounded for any file size.
    """
    points, keys, total, bad, stride, seen, last = [], {}, 0, 0, 1, 0, None
    for line in _complete_lines(path):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if not isinstance(row, dict):
            bad += 1
            continue
        p = {k: v for k, v in row.items() if k != "step" and _is_num(v)}
        if not p:
            continue
        for k in p:
            keys.setdefault(k, None)
        p["step"] = row["step"] if _is_num(row.get("step")) else total
        total += 1
        last = p
        seen += 1
        if seen >= stride:  # keep every stride-th point while reading
            seen = 0
            points.append(p)
            if len(points) >= 4 * max_points:
                points, stride = points[::2], stride * 2
    if last is not None and (not points or points[-1] is not last):
        points.append(last)  # always keep the latest point
    downsampled = len(points) < total
    if len(points) > max_points:
        n = len(points)
        idx = sorted({round(i * (n - 1) / (max_points - 1)) for i in range(max_points)})
        points, downsampled = [points[i] for i in idx], True
    return {"keys": list(keys), "points": points, "total": total,
            "bad_lines": bad, "downsampled": downsampled}


def read_jsonl_head(path, n=HEAD_ROWS):
    """First n complete rows of a .jsonl file; unparsable rows come back as {'_raw': text}."""
    rows = []
    for line in _complete_lines(path):
        if len(rows) >= n:
            break
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            rows.append({"_raw": line.decode("utf-8", "replace").rstrip("\n")})
    return rows


# ---------------------------------------------------------------- scanning

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")  # no leading "_" or "."
# method/: one file per step of how the method works; tasks/: the work view (what agents do);
# decisions/: what the user decided (候选 until the user adopts it in the console)
RECORD_DIRS = ("versions", "tracks", "attempts", "method", "tasks", "decisions")
SECTIONED = ("attempts", "tasks", "decisions")  # records whose "## " sections are parsed

TASK_KINDS = ("讨论", "执行", "写作", "分析", "审查", "调试")
TASK_STATUS = ("待开始", "进行中", "等运行", "等你", "可以继续", "完成", "暂停", "放弃")
TASK_OPEN = ("待开始", "进行中", "等运行", "等你", "可以继续")
DECISION_STATUS = ("候选", "采纳", "不采纳", "已替代")
DECISION_CHOICES = ("采纳", "不采纳")  # what the user can answer a 候选 decision with
DOC_KINDS = ("方案", "观察", "复盘", "报告", "说明", "纪要")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".svg")
TEXT = "text/plain; charset=utf-8"  # raw records are shown as text, never rendered
FILE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".svg": "image/svg+xml", ".json": TEXT, ".jsonl": TEXT, ".md": TEXT}
MAX_FILE_BYTES = 10 * 1024 * 1024
MISSING = (FileNotFoundError, NotADirectoryError)
SHARED = "_shared"  # cross-project directory under the root; not a project


def valid_name(name):
    return isinstance(name, str) and bool(NAME_RE.match(name))


def _load_json(path):
    return json.loads(_read_bytes(path, MAX_BODY * 50).decode("utf-8", "replace"))


def _err(e):
    return "%s: %s" % (type(e).__name__, e)


class _Board:
    """Reads one board/ directory.

    Parse problems are collected in .errors; OSErrors other than "missing"
    propagate so the caller can keep the previous snapshot of the project.
    Files that resolve outside board/ (symlinks) are ignored.
    """

    def __init__(self, board, memo, new_memo):
        self.board, self.real = board, os.path.realpath(board)
        self.memo, self.new_memo = memo, new_memo
        self.errors = []

    def path(self, rel):
        return os.path.join(self.board, rel)

    def listdir(self, rel):
        try:
            return sorted(os.listdir(self.path(rel)))
        except MISSING:
            return []

    def stat(self, rel):
        """stat of a regular file inside board/, else None."""
        p = self.path(rel)
        try:
            st = os.stat(p)
        except MISSING:
            return None
        if not stat.S_ISREG(st.st_mode):
            return None
        real = os.path.realpath(p)
        if not real.startswith(self.real + os.sep):
            self.errors.append({"file": rel, "error": "指向 board/ 之外的符号链接，已忽略"})
            return None
        return st

    def cached(self, rel, st, fn, default=None):
        """fn(path), reusing the previous scan's result if size and mtime are unchanged."""
        key, sig = self.path(rel), (st.st_mtime_ns, st.st_size)
        hit = self.memo.get(key)
        if hit is not None and hit[0] == sig:
            self.new_memo[key] = hit
            if hit[2]:
                self.errors.append(hit[2])
            return hit[1]
        try:
            val, err = fn(key), None
        except MISSING:
            return default
        except OSError:
            raise
        except Exception as e:  # malformed content
            val, err = default, {"file": rel, "error": _err(e)}
            self.errors.append(err)
        self.new_memo[key] = (sig, val, err)
        return val

    def record(self, rel, st):
        def load(p):
            text, cut = read_text(p)
            meta, body = parse_front_matter(text)
            return {"meta": meta, "body": body, "truncated": cut}
        r = self.cached(rel, st, load) or {"meta": {}, "body": "", "truncated": False}
        return dict(r, file=rel, mtime=st.st_mtime)

    def json_object(self, rel, st):
        data = self.cached(rel, st, _load_json)
        if data is not None and not isinstance(data, dict):
            self.errors.append({"file": rel, "error": "应为 JSON 对象"})
            return {}
        return data or {}

    def root_doc(self, name):
        """A fixed-name file next to board/ (e.g. NOTES.md), read as a record; None if missing or a symlink out."""
        root = os.path.dirname(self.board)
        p = os.path.join(root, name)
        try:
            st = os.stat(p)
        except MISSING:
            return None
        if not stat.S_ISREG(st.st_mode) or os.path.realpath(p) != os.path.join(os.path.realpath(root), name):
            return None
        return dict(self.record("../" + name, st), file=name)


TODO_KEYS = ("id", "text", "kind", "where", "created", "done", "by", "link", "context", "options", "recommend", "result", "state")
ATTEMPT_CHOICES = ("成功", "部分成功", "失败", "放弃")  # what the user can confirm a 待确认 attempt as


def todo_options(t):
    """The answer choices of a to-do: its "options" if that is a list of short strings, else none."""
    opts = t.get("options")
    if not isinstance(opts, list):
        return []
    return [o for o in opts if isinstance(o, str) and o.strip() and len(o) <= 200][:12]


def read_answers(path, limit=20000):
    """answers.jsonl -> {id: latest row}. Only the console writes this file: one row per reply,
    the last row of an id wins (the user may change a reply before a session acts on it)."""
    out = {}
    for i, line in enumerate(_complete_lines(path)):
        if i >= limit:
            break
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("id"), str):
            out.pop(row["id"], None)
            out[row["id"]] = {k: row.get(k) for k in ("id", "answer", "text", "at")}
    return out


MARKS = ("看过", "确认")  # what the user can mark a document as in the console


def read_marks(path, limit=20000):
    """marks.jsonl -> {file: latest row}. Only the console writes it; "mark": null clears a mark.
    "mtime" is the document's mtime when it was marked, so a later edit shows as 有更新."""
    out = {}
    for i, line in enumerate(_complete_lines(path)):
        if i >= limit:
            break
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("file"), str):
            out.pop(row["file"], None)
            if row.get("mark") in MARKS:
                out[row["file"]] = {k: row.get(k) for k in ("file", "mark", "at", "mtime")}
    return out


def read_todos(path, limit=2000):
    """todos.jsonl -> {"items": [...], "bad": n}. Rows are appended; the last row of an id wins,
    so a session marks an item done by appending it again with "done" set."""
    items, bad = {}, 0
    for i, line in enumerate(_complete_lines(path)):
        if i >= limit:
            break
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if not isinstance(row, dict) or not isinstance(row.get("id"), (str, int)) or not row.get("text"):
            bad += 1
            continue
        items.pop(str(row["id"]), None)  # re-insert so the order follows the latest row
        items[str(row["id"])] = {k: row.get(k) for k in TODO_KEYS}
    return {"items": list(items.values()), "bad": bad}


def scan_project(board, memo=None, new_memo=None):
    """Parse one project's board/ dir. Raises only OSError (I/O trouble)."""
    b = _Board(board, memo if memo is not None else {}, new_memo if new_memo is not None else {})
    updated = {}

    def bump(kind, t):
        updated[kind] = max(updated.get(kind) or 0, t)

    project, plan = {}, None
    st = b.stat("project.json")
    if st:
        bump("project", st.st_mtime)
        project = b.json_object("project.json", st)
    st = b.stat("plan.md")
    if st:
        bump("plan", st.st_mtime)
        plan = b.record("plan.md", st)

    records = {}
    for kind in RECORD_DIRS:
        items = []
        for fn in b.listdir(kind):
            st = b.stat(kind + "/" + fn) if fn.endswith(".md") and not fn.startswith(".") else None
            if not st:
                continue
            bump(kind, st.st_mtime)
            r = b.record(kind + "/" + fn, st)
            if kind in SECTIONED:
                r["sections"] = split_sections(r["body"])
            if kind == "attempts":
                r["needs_input"] = needs_input(r["meta"], r["sections"])
                r["onepager"] = parse_onepager(r["sections"].get("一页纸"))
            items.append(r)
        records[kind] = items

    runs = []
    for rid in b.listdir("runs"):
        d = "runs/" + rid
        if rid.startswith("."):
            continue
        try:  # not os.path.isdir: it hides I/O errors and the run would silently vanish
            if not stat.S_ISDIR(os.stat(b.path(d)).st_mode):
                continue
        except MISSING:
            continue
        run = {"dir": d, "id": rid, "run": {}, "series": None, "samples_head": [], "images": [], "mtime": None,
               "heartbeat": None}
        times = []
        st = b.stat(d + "/heartbeat")
        if st:  # touched every minute by boardrun while the run is alive; not counted as an update
            run["heartbeat"] = st.st_mtime
        st = b.stat(d + "/run.json")
        if st:
            times.append(st.st_mtime)
            run["run"] = b.json_object(d + "/run.json", st)
        st = b.stat(d + "/metrics.jsonl")
        if st:
            times.append(st.st_mtime)
            run["series"] = b.cached(d + "/metrics.jsonl", st, read_metrics)
        st = b.stat(d + "/samples.jsonl")
        if st:
            times.append(st.st_mtime)
            run["samples_head"] = b.cached(d + "/samples.jsonl", st, read_jsonl_head, []) or []
        for fn in b.listdir(d):
            if fn.lower().endswith(IMAGE_EXTS) and not fn.startswith("."):
                st = b.stat(d + "/" + fn)
                if st:
                    times.append(st.st_mtime)
                    run["images"].append(d + "/" + fn)
        if times:
            run["mtime"] = max(times)
            bump("runs", run["mtime"])
        runs.append(run)

    example = {"walkthrough": None, "probe_head": []}
    st = b.stat("examples/walkthrough.md")
    if st:
        bump("examples", st.st_mtime)
        example["walkthrough"] = b.record("examples/walkthrough.md", st)
    st = b.stat("examples/probe.jsonl")
    if st:
        bump("examples", st.st_mtime)
        example["probe_head"] = b.cached("examples/probe.jsonl", st, read_jsonl_head, []) or []

    # paper-style setup, the project flow chart and the to-do list (all optional)
    setup = flow = None
    st = b.stat("setup.json")
    if st:
        bump("setup", st.st_mtime)
        setup = b.json_object("setup.json", st)
    st = b.stat("flow.json")
    if st:
        flow = b.json_object("flow.json", st)
    todos = {"items": [], "bad": 0}
    st = b.stat("todos.jsonl")
    if st:
        bump("todos", st.st_mtime)
        todos = b.cached("todos.jsonl", st, read_todos, todos) or todos
        if todos["bad"]:
            b.errors.append({"file": "todos.jsonl", "error": "%d 行无法解析或缺少 id / text" % todos["bad"]})
    answers = {}
    st = b.stat("answers.jsonl")
    if st:
        bump("todos", st.st_mtime)
        answers = b.cached("answers.jsonl", st, read_answers, {}) or {}
    marks = {}
    st = b.stat("marks.jsonl")
    if st:
        marks = b.cached("marks.jsonl", st, read_marks, {}) or {}

    # general documents: board/plan.md is above; board/docs/*.md and two fixed files in the project root
    docs = []
    for fn in b.listdir("docs"):
        st = b.stat("docs/" + fn) if fn.endswith(".md") and not fn.startswith(".") else None
        if st:
            bump("docs", st.st_mtime)
            docs.append(b.record("docs/" + fn, st))
    for name in ("NOTES.md", "REPORT.md"):
        r = b.root_doc(name)
        if r:
            bump("docs", r["mtime"])
            docs.append(r)

    out = {"project": project, "plan": plan, "runs": runs, "example": example, "setup": setup, "flow": flow,
           "todos": todos["items"], "answers": answers, "marks": marks, "docs": docs, "updated": updated, "errors": b.errors}
    out.update(records)
    try:
        out["checks"] = check_project(out)
    except Exception as e:  # a check must never break the scan
        out["checks"] = []
        b.errors.append({"file": "(记录检查)", "error": _err(e)})
    return out


def _ids(records):
    return {r["meta"].get("id") for r in records if isinstance(r["meta"].get("id"), str)}


def _list(x):
    return x if isinstance(x, list) else []


def decision_states(decisions, answers):
    """{decision id: effective status}. A 候选 the user answered in the console counts as answered at
    once (before any session processes the reply); a decision replaced by an adopted one is 已替代."""
    state = {}
    for r in decisions:
        m = r.get("meta") or {}
        i = m.get("id")
        if not isinstance(i, str):
            continue
        s = m.get("status") if m.get("status") in DECISION_STATUS else "候选"
        a = (answers or {}).get("d:" + i)
        if s == "候选" and a and a.get("answer") in DECISION_CHOICES:
            s = a["answer"]
        state[i] = s
    for r in decisions:
        m = r.get("meta") or {}
        if state.get(m.get("id")) == "采纳":
            for old in _list(m.get("replaces")):
                if old in state and old != m.get("id"):
                    state[old] = "已替代"
    return state


_REF_KINDS = {"v": "versions", "t": "tracks", "a": "attempts", "m": "method", "k": "tasks", "d": "decisions"}


def ref_problem(ref, d):
    """Why an "about" reference (v:/t:/a:/m:/k:/d: + id) does not resolve, or None."""
    if not isinstance(ref, str) or ":" not in ref:
        return "「%s」不是 v: / t: / a: / m: / k: / d: 加 id 的写法" % ref
    kind, _, rid = ref.partition(":")
    if kind not in _REF_KINDS:
        return "「%s」不是 v: / t: / a: / m: / k: / d: 加 id 的写法" % ref
    if rid not in _ids(d.get(_REF_KINDS[kind]) or []):
        return "「%s」指向的记录不存在" % ref
    return None


def _id_matcher(ids):
    """Match names against setup ids; "{n}" in an id stands for a number (acc_step{n} covers acc_step1, acc_step2 ...)."""
    exact = {i for i in ids if isinstance(i, str) and "{n}" not in i}
    pats = [re.compile("^" + re.escape(i).replace(re.escape("{n}"), r"\d+") + "$")
            for i in ids if isinstance(i, str) and "{n}" in i]
    return lambda x: x in exact or any(p.match(x) for p in pats)


def check_project(d, limit=200):
    """Broken links and missing definitions in a project's records: [{"where", "what"}].
    Only reports what a session writing the records can fix; never raises."""
    out = []

    def add(where, what):
        if len(out) < limit:
            out.append({"where": where, "what": what})

    run_ids = {str(r["run"].get("id") or r["id"]) for r in d["runs"]}
    att_ids, track_ids, ver_ids = _ids(d["attempts"]), _ids(d["tracks"]), _ids(d["versions"])
    merged = set()
    for v in d["versions"]:
        for t in _list(v["meta"].get("merged_tracks")):
            merged.add(t)
            if t not in track_ids:
                add(v["file"], "merged_tracks 里的支线「%s」不存在" % t)
    for t in d["tracks"]:
        fv = t["meta"].get("from_version")
        if fv and fv not in ver_ids:
            add(t["file"], "from_version「%s」不存在" % fv)
        if t["meta"].get("id") not in merged and fv not in ver_ids and ver_ids:
            add(t["file"], "这条支线没有挂到任何版本上（没有有效的 from_version，也不在任何版本的 merged_tracks 里）")
    for a in d["attempts"]:
        m = a["meta"]
        if not m:
            add(a["file"], "没有字段区（开头的 --- 块），读不到 id、支线和状态")
            continue
        if m.get("track") not in track_ids:
            add(a["file"], "所属支线「%s」不存在" % m.get("track") if m.get("track") else "没有写所属支线（track）")
        for rid in _list(m.get("runs")):
            if rid not in run_ids:
                add(a["file"], "引用的运行「%s」不存在" % rid)
        if "例子" not in (a.get("sections") or {}) and m.get("status") != "计划中":
            add(a["file"], "没有「例子」小节")
        for msg in onepager_problems(m, a.get("onepager") or {}, "一页纸" in (a.get("sections") or {})):
            add(a["file"], msg)
    for r in d["runs"]:
        at = r["run"].get("attempt")
        if at and at not in att_ids:
            add(r["dir"] + "/run.json", "attempt「%s」不存在" % at)
    steps = d.get("method") or []
    step_ids = _ids(steps)
    setup_methods = {x.get("id") for x in _list((d.get("setup") or {}).get("methods")) if isinstance(x, dict)}
    for st in steps:
        m = st["meta"]
        if not isinstance(m.get("id"), str):
            add(st["file"], "没有 id，方法图上画不出这一步")
            continue
        for key in ("parent", "variant_of"):
            if m.get(key) and m[key] not in step_ids:
                add(st["file"], "%s「%s」不是方法里的步骤" % (key, m[key]))
        if m.get("parent") and m.get("variant_of"):
            add(st["file"], "parent 和 variant_of 只写一个：变体和它的原步骤在同一层")
        for x in _list(m.get("next")):
            if x not in step_ids:
                add(st["file"], "next 里的「%s」不是方法里的步骤" % x)
        if m.get("back"):
            target = next((y for y in steps if y["meta"].get("id") == m["back"]), None)
            if target is None:
                add(st["file"], "back「%s」不是方法里的步骤" % m["back"])
            elif target["meta"].get("parent") != m.get("parent"):
                add(st["file"], "back「%s」不在同一层：循环只能回到同一层里前面的步骤" % m["back"])
        for t in _list(m.get("tracks")):
            if t not in track_ids:
                add(st["file"], "tracks 里的支线「%s」不存在" % t)
        for x in _list(m.get("methods")):
            if setup_methods and x not in setup_methods:
                add(st["file"], "methods 里的「%s」不在 setup.json 的 methods 里" % x)
    for a in d["attempts"]:
        for x in _list(a["meta"].get("method")):
            if x not in step_ids:
                add(a["file"], "method 里的「%s」不是方法里的步骤" % x)

    s = d.get("setup")
    if s:
        datasets = _id_matcher([x.get("id") for x in _list(s.get("datasets")) if isinstance(x, dict)])
        metrics = _id_matcher([x.get("id") for x in _list(s.get("metrics")) if isinstance(x, dict)])
        groups, cols = {}, {}
        for r in d["runs"]:
            m = r["run"].get("metrics")
            if not isinstance(m, dict):
                continue
            for g, v in m.items():
                if isinstance(v, dict):
                    groups[g] = groups.get(g, 0) + 1
                    for c in v:
                        cols[c] = cols.get(c, 0) + 1
                else:
                    cols[g] = cols.get(g, 0) + 1
        for g in sorted(x for x in groups if not datasets(x)):
            add("setup.json", "分组「%s」（%d 次运行用到）没有在 datasets 里定义" % (g, groups[g]))
        for c in sorted(x for x in cols if not metrics(x)):
            add("setup.json", "指标「%s」（%d 次运行用到）没有在 metrics 里定义" % (c, cols[c]))
        for x in _list(s.get("terms")):
            if not (isinstance(x, dict) and isinstance(x.get("term"), str) and x["term"].strip()
                    and isinstance(x.get("about"), str) and x["about"].strip()):
                add("setup.json", "terms 里每项要有 term（术语）和 about（一句白话解释）")
            elif len(x["about"]) > 120:
                add("setup.json", "术语「%s」的解释太长（%d 字）：一句白话就够" % (x["term"], len(x["about"])))
        for x in _list(s.get("methods")):
            if not isinstance(x, dict):
                continue
            if x.get("track") and x["track"] not in track_ids:
                add("setup.json", "方法「%s」的 track「%s」不存在" % (x.get("id"), x["track"]))
            for rid in _list(x.get("runs")):
                if rid not in run_ids:
                    add("setup.json", "方法「%s」引用的运行「%s」不存在" % (x.get("id"), rid))

    task_ids = _ids(d.get("tasks") or [])
    for t in d.get("tasks") or []:
        m, secs = t["meta"], t.get("sections") or {}
        if not isinstance(m.get("id"), str):
            add(t["file"], "没有 id（字段区写 id: <英文短名>，和文件名一致）")
            continue
        if t["file"] != "tasks/%s.md" % m["id"]:
            add(t["file"], "文件名要和 id 一致：tasks/%s.md" % m["id"])
        if m.get("kind") not in TASK_KINDS:
            add(t["file"], "kind「%s」不认识（%s）" % (m.get("kind"), " / ".join(TASK_KINDS)))
        if m.get("status") not in TASK_STATUS:
            add(t["file"], "status「%s」不认识（%s）" % (m.get("status"), " / ".join(TASK_STATUS)))
        for x in _list(m.get("after")):
            if x not in task_ids:
                add(t["file"], "after 里的任务「%s」不存在" % x)
        if m.get("about"):
            p = ref_problem(m["about"], d)
            if p:
                add(t["file"], "about " + p)
        if not secs.get("要做什么", "").strip():
            add(t["file"], "没有「要做什么」一节")
        if m.get("status") in ("完成", "等你", "可以继续") and not secs.get("交接", "").strip():
            add(t["file"], "状态是「%s」，但「交接」是空的（做了什么 / 证据在哪 / 没做完的 / 下一步 / 要你决定的）" % m.get("status"))

    dec_ids = _ids(d.get("decisions") or [])
    states = decision_states(d.get("decisions") or [], d.get("answers") or {})
    replaced_by = {}
    for r in d.get("decisions") or []:
        m, secs = r["meta"], r.get("sections") or {}
        if not isinstance(m.get("id"), str):
            add(r["file"], "没有 id（字段区写 id: <英文短名>，和文件名一致）")
            continue
        if r["file"] != "decisions/%s.md" % m["id"]:
            add(r["file"], "文件名要和 id 一致：decisions/%s.md" % m["id"])
        if m.get("status") not in DECISION_STATUS:
            add(r["file"], "status「%s」不认识（%s）" % (m.get("status"), " / ".join(DECISION_STATUS)))
        if not secs.get("决定", "").strip():
            add(r["file"], "没有「决定」一节（一句话写定了什么）")
        if m.get("about"):
            p = ref_problem(m["about"], d)
            if p:
                add(r["file"], "about " + p)
        for old in _list(m.get("replaces")):
            if old not in dec_ids:
                add(r["file"], "replaces 里的决定「%s」不存在" % old)
            elif states.get(m["id"]) == "采纳":
                if old in replaced_by:
                    add(r["file"], "决定「%s」已经被「%s」替代，不能再被替代一次" % (old, replaced_by[old]))
                else:
                    replaced_by[old] = m["id"]

    for doc in d.get("docs") or []:
        if not doc["file"].startswith("docs/"):
            continue
        m = doc.get("meta") or {}
        if not m.get("kind"):
            add(doc["file"], "没有 kind（%s）" % " / ".join(DOC_KINDS))
        elif m["kind"] not in DOC_KINDS:
            add(doc["file"], "kind「%s」不认识（%s）" % (m["kind"], " / ".join(DOC_KINDS)))
        if m.get("about"):
            p = ref_problem(m["about"], d)
            if p:
                add(doc["file"], "about " + p)
        for x in _list(m.get("decisions")):
            if x not in dec_ids:
                add(doc["file"], "decisions 里的决定「%s」不存在" % x)

    h = ((d.get("project") or {}).get("display") or {}).get("headline")
    if isinstance(h, dict) and h.get("group") and h.get("metric"):
        seen = any(isinstance((r["run"].get("metrics") or {}).get(h["group"]), dict)
                   and h["metric"] in r["run"]["metrics"][h["group"]] for r in d["runs"])
        if not seen:
            add("project.json", "display.headline 指向的「%s / %s」在运行记录里找不到" % (h["group"], h["metric"]))
    return out


def scan_root(root, prev=None, memo=None, new_memo=None, log=None):
    """Scan every project directly under root.

    Raises OSError only if root itself cannot be listed. A project hit by an
    I/O error keeps its previous snapshot, marked with "stale".
    """
    prev = prev or {}
    out = {}
    for name in sorted(os.listdir(root)):
        if not valid_name(name):
            continue
        board = os.path.join(root, name, "board")
        try:
            os.stat(os.path.join(board, "project.json"))
            data = scan_project(board, memo, new_memo)
        except MISSING:
            continue
        except OSError as e:
            stale = {"error": _err(e), "at": time.time()}
            if log:
                log("[projects] %s: I/O error, keeping previous data: %s" % (name, stale["error"]))
            out[name] = dict(prev.get(name) or empty_project(name), stale=stale)
            continue
        data.update(name=name, scanned_at=time.time(), stale=None)
        out[name] = data
    return out


def empty_project(name):
    return {"name": name, "project": {}, "plan": None, "versions": [], "tracks": [], "attempts": [], "method": [],
            "tasks": [], "decisions": [], "runs": [], "example": {"walkthrough": None, "probe_head": []}, "setup": None, "flow": None,
            "todos": [], "answers": {}, "marks": {}, "docs": [], "checks": [], "updated": {}, "errors": [], "scanned_at": None}


def with_answers(todos, answers):
    return [dict(t, answer=answers.get(str(t.get("id")))) for t in todos]


def first_line(text, n=120):
    """The first line of prose in a Markdown section, without list markers or emphasis."""
    for line in (text or "").split("\n"):
        line = line.strip().lstrip("-*").strip().replace("**", "")
        if line and not line.startswith("|") and not line.startswith("#"):
            return line[:n] + ("…" if len(line) > n else "")
    return ""


def handoff_next(sections):
    """The 下一步 line of a task card's 交接."""
    for line in (sections.get("交接") or "").split("\n"):
        m = re.match(r"^\s*[-*]?\s*下一步\s*[:：]\s*(.+)$", line)
        if m:
            return m.group(1).strip()[:120]
    return ""


def unprocessed_replies(d):
    """Replies in answers.jsonl that no session has acted on yet: [id]."""
    todos = {str(t.get("id")): t for t in d.get("todos") or []}
    attempts = {a["meta"].get("id"): a for a in d.get("attempts") or [] if isinstance(a["meta"].get("id"), str)}
    decisions = {r["meta"].get("id"): r for r in d.get("decisions") or [] if isinstance(r["meta"].get("id"), str)}
    out = []
    for rid in d.get("answers") or {}:
        if rid.startswith("a:"):
            a = attempts.get(rid[2:])
            if a and a["meta"].get("status") == "待确认":
                out.append(rid)
        elif rid.startswith("d:"):
            r = decisions.get(rid[2:])
            if r and r["meta"].get("status") in (None, "候选"):
                out.append(rid)
        elif rid in todos and not todos[rid].get("done"):
            out.append(rid)
    return out


def work_summary(data):
    """The work view of a project for the project list: open tasks, decisions, the latest 一页纸."""
    answers = data.get("answers") or {}
    states = decision_states(data.get("decisions") or [], answers)
    tasks, n_tasks = [], {}
    for t in data.get("tasks") or []:
        m = t.get("meta") or {}
        st = m.get("status") if m.get("status") in TASK_STATUS else "未写"
        n_tasks[st] = n_tasks.get(st, 0) + 1
        if st in TASK_OPEN:
            tasks.append({"id": m.get("id"), "title": m.get("title") or m.get("id"), "kind": m.get("kind"), "status": st,
                          "about": m.get("about"), "updated": m.get("updated") or m.get("created"),
                          "next": handoff_next(t.get("sections") or {})})
    cand, adopted = [], []
    for r in data.get("decisions") or []:
        m = r.get("meta") or {}
        i = m.get("id")
        row = {"id": i, "title": m.get("title") or i, "decision": first_line((r.get("sections") or {}).get("决定")),
               "date": m.get("date"), "answer": answers.get("d:%s" % i)}
        if states.get(i) == "候选" or (states.get(i) in DECISION_CHOICES and m.get("status") in (None, "候选")):
            cand.append(dict(row, state=states.get(i)))
        if states.get(i) == "采纳":
            adopted.append(row)
    latest = []
    for a in sorted(data.get("attempts") or [], key=lambda a: str((a.get("meta") or {}).get("date") or ""), reverse=True):
        op = a.get("onepager") or {}
        so = (op.get("所以") or {}).get("text")
        if so:
            latest.append({"id": a["meta"].get("id"), "title": a["meta"].get("title") or a["meta"].get("id"),
                           "status": a["meta"].get("status"), "so": so, "q": (op.get("想回答") or {}).get("text")})
        if len(latest) >= 2:
            break
    return {"tasks_open": tasks, "n_tasks": n_tasks, "decisions_candidate": cand,
            "decisions_adopted": sorted(adopted, key=lambda x: str(x.get("date") or ""), reverse=True),
            "latest_onepager": latest, "unprocessed": unprocessed_replies(data)}


HEARTBEAT_STALE = 300   # seconds without a heartbeat before a run counts as possibly dead
RUN_STATES = ("运行中", "状态未知", "已结束", "已中断")


def run_state(run, heartbeat_mtime, probe, now):
    """What to show for a run, from its record, its heartbeat and a probe of its process.

    run              the run.json dict
    heartbeat_mtime  mtime of runs/<id>/heartbeat, or None (older runs have none)
    probe            "running" (its process was seen), "absent" (the server answered and the process is
                     gone), "unreachable" (the server did not answer) or None (no way to look)
    Returns 运行中 / 状态未知 / 已结束 / 已中断. A run is never called 已中断 without a probe saying the
    process is gone: a stale heartbeat alone may be a slow shared disk.
    """
    if (run or {}).get("status") != "运行中":
        return "已结束"
    if heartbeat_mtime is not None and now - heartbeat_mtime <= HEARTBEAT_STALE:
        return "运行中"
    if probe == "running":
        return "运行中"
    if probe == "absent":
        return "已中断"
    return "状态未知"


def summary(data):
    """One row of the project list."""
    p = data.get("project") or {}
    times = [t for t in (data.get("updated") or {}).values() if t]
    active = []
    for r in data.get("runs") or []:
        run = r.get("run") or {}
        if run.get("status") == "运行中":
            active.append({"id": run.get("id") or r.get("id"), "server": run.get("server"), "status": "运行中",
                           "gpus": run.get("gpus"), "pid": run.get("pid"), "tmux": run.get("tmux"),
                           "attempt": run.get("attempt"), "task": run.get("task"), "heartbeat": r.get("heartbeat"),
                           "started": run.get("started")})
    def count(records):
        out = {}
        for r in records:
            s = r.get("meta", {}).get("status")
            s = s if isinstance(s, str) and s else "未写"  # a list or number must not break counting
            out[s] = out.get(s, 0) + 1
        return out

    answers = data.get("answers") or {}
    attempts = data.get("attempts") or []
    pending = []  # attempts waiting for the user (an empty 预期 alone is not counted)
    for a in attempts:
        needs = [x for x in a.get("needs_input") or [] if x != "预期"]
        if needs:
            m = a.get("meta", {})
            aid = m.get("id") if isinstance(m.get("id"), str) else None
            proposed = m.get("proposed") if m.get("proposed") in ATTEMPT_CHOICES else None
            pending.append({"file": a.get("file"), "id": m.get("id"), "status": m.get("status"), "needs": needs,
                            "title": m.get("title"), "track": m.get("track"), "proposed": proposed,
                            "conclusion": ((a.get("sections") or {}).get("结论和原因") or "")[:600],
                            "answer": answers.get("a:" + aid) if aid and "结论待确认" in needs else None})
    return {"name": data.get("name"), "title": p.get("title") or data.get("name"),
            "status": p.get("status"), "conclusion": p.get("conclusion"),
            "current_version": p.get("current_version"), "updated": p.get("updated"),
            "mtime": max(times) if times else None, "active_runs": active,
            "n_errors": len(data.get("errors") or []), "stale": data.get("stale"),
            "scanned_at": data.get("scanned_at"),
            "n_tracks": len(data.get("tracks") or []), "track_status": count(data.get("tracks") or []),
            "n_attempts": len(attempts), "attempt_status": count(attempts),
            "n_runs": len(data.get("runs") or []), "pending": pending,
            "question": p.get("question"), "updated_kinds": data.get("updated") or {}, **work_summary(data),
            "todos": with_answers([t for t in data.get("todos") or [] if not t.get("done")], answers),
            "todos_done": with_answers(sorted((t for t in data.get("todos") or [] if t.get("done")),
                                              key=lambda t: str(t.get("done")), reverse=True)[:20], answers),
            "n_checks": len(data.get("checks") or [])}


class ProjectCache:
    """Scans projects in a background thread, one scan at a time.

    Readers get the last finished snapshot immediately; a slow or hung shared
    disk only delays the next snapshot (status.running_since shows it).
    """

    def __init__(self, root, interval=60, log=None):
        self.root, self.interval = root, interval
        self.log = log or (lambda msg: print(msg, file=sys.stderr, flush=True))
        self._lock = threading.Lock()
        self._projects, self._memo, self._todos, self._todos_sig = {}, {}, [], None
        self._answers, self._answers_sig = {}, None  # replies to the cross-project to-dos (_shared/answers.jsonl)
        self._status = {"root": root, "started": None, "finished": None, "duration": None,
                        "running_since": None, "error": None, "error_at": None}

    def scan_once(self):
        t0 = time.time()
        with self._lock:
            self._status["running_since"] = t0
        new_memo = {}
        try:
            projects = scan_root(self.root, self._projects, self._memo, new_memo, self.log)
            todos, path = self._todos, os.path.join(self.root, "_shared", "todos.jsonl")
            try:
                st = os.stat(path)
                sig = (st.st_mtime_ns, st.st_size)
                if sig != self._todos_sig:  # unchanged file: keep the previous parse
                    todos, self._todos_sig = read_todos(path)["items"], sig
            except MISSING:
                todos, self._todos_sig = [], None
            except OSError as e:  # keep the previous list on I/O trouble
                self.log("[projects] _shared/todos.jsonl: %s" % _err(e))
            answers, path = self._answers, self.answers_path(SHARED)
            try:
                st = os.stat(path)
                sig = (st.st_mtime_ns, st.st_size)
                if sig != self._answers_sig:  # unchanged file: keep the previous parse
                    answers, self._answers_sig = read_answers(path), sig
            except MISSING:
                answers, self._answers_sig = {}, None
            except OSError as e:
                self.log("[projects] _shared/answers.jsonl: %s" % _err(e))
        except Exception as e:
            self.log("[projects] scan of %s failed, keeping previous data:\n%s"
                     % (self.root, traceback.format_exc()))
            with self._lock:
                self._status.update(running_since=None, error=_err(e), error_at=time.time())
            return False
        t1 = time.time()
        with self._lock:
            self._projects, self._memo, self._todos, self._answers = projects, new_memo, todos, answers
            self._status.update(started=t0, finished=t1, duration=round(t1 - t0, 3),
                                running_since=None, error=None)
        return True

    def _loop(self):
        while True:
            self.scan_once()
            time.sleep(self.interval)

    def start(self):
        threading.Thread(target=self._loop, name="project-scan", daemon=True).start()

    def snapshot(self):
        """(projects, status). Snapshots are replaced, never mutated: treat as read-only."""
        with self._lock:
            return self._projects, dict(self._status)

    def global_todos(self):
        """Cross-project to-dos from <root>/_shared/todos.jsonl (all rows, done or not), with replies."""
        with self._lock:
            return with_answers(self._todos, self._answers)

    def console_path(self, name, which="answers"):
        """Where the console's own files live: answers.jsonl (replies) or marks.jsonl (document marks)."""
        if name == SHARED:
            return os.path.join(self.root, SHARED, which + ".jsonl")
        return os.path.join(self.root, name, "board", which + ".jsonl")

    def answers_path(self, name):
        return self.console_path(name, "answers")

    def reload(self, name, which="answers"):
        """Re-read one of the console's files now (after the console appended to it), so the change shows at once."""
        data = (read_answers if which == "answers" else read_marks)(self.console_path(name, which))
        with self._lock:
            if name == SHARED and which == "answers":
                self._answers, self._answers_sig = data, None
            elif name in self._projects:
                projects = dict(self._projects)  # snapshots are replaced, never mutated
                projects[name] = dict(projects[name], **{which: data})
                self._projects = projects

    def reload_answers(self, name):
        self.reload(name, "answers")


def safe_board_file(root, name, relpath):
    """Real path of an allowed file under root/name/board/, or None."""
    if not valid_name(name) or not isinstance(relpath, str) or not relpath:
        return None
    if relpath.startswith("/") or "\\" in relpath or "\0" in relpath:
        return None
    parts = relpath.split("/")
    if any(p in ("", ".", "..") or p.startswith(".") for p in parts):
        return None
    board = os.path.realpath(os.path.join(root, name, "board"))
    full = os.path.realpath(os.path.join(board, *parts))
    if not full.startswith(board + os.sep):
        return None
    for p in (relpath, full):
        if os.path.splitext(p)[1].lower() not in FILE_TYPES:
            return None
    return full
