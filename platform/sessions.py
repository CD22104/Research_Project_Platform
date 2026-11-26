"""Claude sessions on this machine, for the console's 会话 page: who is working where, how big each
conversation has grown, and how many tokens it used. Metadata only: the console never shows what was said.

Two sources, both local to the machine the console runs on:
  - `claude agents --json`: the sessions alive right now (interactive, background, remote control) and
    whether each is busy or idle.
  - the transcripts under ~/.claude/projects/*/<session id>.jsonl: title, working directory, last
    activity, the context size of the last model call, and token use per day. Files are read
    incrementally (only the bytes added since the last pass), so a big transcript costs little.
"""
import calendar
import glob
import json
import os
import subprocess
import threading
import time

RECENT_DAYS = 14          # transcripts untouched for longer are not listed
MAX_FILES = 300           # transcripts considered per pass (most recent first)
MAX_READ = 64 * 1024 * 1024  # bytes read from one transcript per pass (a huge file catches up over passes)


def _day(ts):
    return ts[:10] if isinstance(ts, str) and len(ts) >= 10 else None


class _Transcript:
    """Running totals of one transcript file, updated from the bytes appended since the last read."""

    def __init__(self, path):
        self.path, self.inode, self.offset = path, None, 0
        self.sid = os.path.basename(path)[:-len(".jsonl")]
        self.title = self.cwd = self.last_ts = self.model = None
        self.context = None          # input tokens of the last model call (= how big the conversation is)
        self.days = {}               # "YYYY-MM-DD" -> [input incl. cache, output, calls]
        self.compactions = 0

    def update(self):
        st = os.stat(self.path)
        if self.inode != st.st_ino or st.st_size < self.offset:  # replaced or truncated: start over
            self.__init__(self.path)
            self.inode = st.st_ino
        if st.st_size == self.offset:
            return
        with open(self.path, "rb") as f:
            f.seek(self.offset)
            data = f.read(min(MAX_READ, st.st_size - self.offset))
        end = data.rfind(b"\n") + 1  # a last line without newline is still being written
        for line in data[:end].split(b"\n"):
            if line.strip():
                self._row(line)
        self.offset += end

    def _row(self, line):
        try:
            r = json.loads(line)
        except ValueError:
            return
        if not isinstance(r, dict):
            return
        if isinstance(r.get("customTitle"), str):
            self.title = r["customTitle"]
        if isinstance(r.get("cwd"), str) and not self.cwd:
            self.cwd = r["cwd"]
        if r.get("isCompactSummary"):
            self.compactions += 1
        ts = r.get("timestamp")
        if isinstance(ts, str):
            self.last_ts = max(self.last_ts or ts, ts)
        if r.get("type") != "assistant":
            return
        m = r.get("message") or {}
        u = m.get("usage") or {}
        n_in = sum(u.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        if not n_in:
            return
        self.context, self.model = n_in, m.get("model") or self.model
        d = _day(ts)
        if d:
            t = self.days.setdefault(d, [0, 0, 0])
            t[0] += n_in
            t[1] += u.get("output_tokens") or 0
            t[2] += 1


def _iso_to_epoch(ts):
    try:
        return calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
    except (TypeError, ValueError):
        return None


class SessionWatch:
    """Polls the two sources in a background thread; snapshot() returns the latest list."""

    def __init__(self, projects_root, claude_bin=None, transcripts=None, interval=60, log=None):
        self.root = os.path.realpath(projects_root)
        self.claude = claude_bin or os.path.expanduser("~/.local/bin/claude")
        self.dir = transcripts or os.path.expanduser("~/.claude/projects")
        self.interval, self.log = interval, log or (lambda m: None)
        self._files, self._lock, self._snap = {}, threading.Lock(), {"sessions": [], "at": None, "error": None}

    def project_of(self, cwd):
        """The project a working directory belongs to (first path part under the projects root), or None."""
        if not isinstance(cwd, str):
            return None
        real = os.path.realpath(cwd)
        if not real.startswith(self.root + os.sep):
            return None
        return real[len(self.root) + 1:].split(os.sep)[0] or None

    def live(self):
        try:
            p = subprocess.run([self.claude, "agents", "--json"], capture_output=True, text=True, timeout=30,
                               env=dict(os.environ, NO_COLOR="1"))
            rows = json.loads(p.stdout) if p.returncode == 0 else []
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
        return [r for r in rows if isinstance(r, dict) and isinstance(r.get("sessionId"), str)] if isinstance(rows, list) else []

    def scan_once(self, now=None):
        now = time.time() if now is None else now
        files = []
        for path in glob.glob(os.path.join(self.dir, "*", "*.jsonl")):
            try:
                mt = os.stat(path).st_mtime
            except OSError:
                continue
            if now - mt <= RECENT_DAYS * 86400:
                files.append((mt, path))
        files = [p for _, p in sorted(files, reverse=True)[:MAX_FILES]]
        keep = {}
        for path in files:
            t = self._files.get(path) or _Transcript(path)
            try:
                t.update()
            except OSError:
                continue
            keep[path] = t
        self._files = keep
        live = self.live()
        by_id = {t.sid: t for t in keep.values()}
        today = time.strftime("%Y-%m-%d", time.gmtime(now))
        week = {time.strftime("%Y-%m-%d", time.gmtime(now - i * 86400)) for i in range(7)}
        out, seen = [], set()

        def row(sid, t, lv):
            cwd = (lv or {}).get("cwd") or (t.cwd if t else None)
            last = _iso_to_epoch(t.last_ts) if t and t.last_ts else None
            days = t.days if t else {}
            return {"id": sid, "name": (lv or {}).get("name") or (t.title if t else None) or sid[:8],
                    "title": t.title if t else None, "cwd": cwd, "project": self.project_of(cwd),
                    "live": lv is not None, "status": (lv or {}).get("status"), "kind": (lv or {}).get("kind"),
                    "started": ((lv or {}).get("startedAt") or 0) / 1000 or None, "last_active": last,
                    "context": t.context if t else None, "compactions": t.compactions if t else 0,
                    "model": t.model if t else None, "resumable": t is not None,
                    "tokens_today": sum(days.get(today, [0, 0])[:2]),
                    "tokens_7d": sum(sum(v[:2]) for d, v in days.items() if d in week),
                    "calls_7d": sum(v[2] for d, v in days.items() if d in week)}
        for lv in live or []:
            out.append(row(lv["sessionId"], by_id.get(lv["sessionId"]), lv))
            seen.add(lv["sessionId"])
        for t in keep.values():
            if t.sid not in seen:
                out.append(row(t.sid, t, None))
        out.sort(key=lambda s: (not s["live"], -(s["last_active"] or 0)))
        with self._lock:
            self._snap = {"sessions": out, "at": now, "error": None if live is not None else "claude agents 读取失败"}
        return out

    def snapshot(self):
        with self._lock:
            return dict(self._snap)

    def start(self):
        def loop():
            while True:
                try:
                    self.scan_once()
                except Exception as e:  # never let the watcher die
                    self.log("[sessions] %s: %s" % (type(e).__name__, e))
                    with self._lock:
                        self._snap = dict(self._snap, error="%s: %s" % (type(e).__name__, e))
                time.sleep(self.interval)
        threading.Thread(target=loop, name="sessions", daemon=True).start()
