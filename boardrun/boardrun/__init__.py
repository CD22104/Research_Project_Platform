"""boardrun: a run writes its own record into the project's board/runs/<id>/ while it runs.

    import boardrun
    with boardrun.Run(attempt="2026-11-22-warmup", kind="train", config=cfg) as run:
        for step, batch in ...:
            run.log(step, loss=loss, lr=lr)                     # curve       -> metrics.jsonl
        run.result("authz_test_iid", accuracy=acc, n=602)        # final numbers -> run.json metrics[group]
        run.sample(id=x.id, input=x.text, output=y, score=s)     # a few outputs -> samples.jsonl
        run.artifact("curve.png")                                 # small file copied next to run.json
        big = run.output_dir()                                    # results/<id>/: large outputs, not shown
    # normal exit -> 完成; an exception -> 失败 (with the error); Ctrl-C or SIGTERM -> 被中断

Commands that are not Python:  python -m boardrun run --attempt <id> --kind eval -- <command ...>

The record format is /shared/ssd/dchen/_shared/BOARD_FORMAT.md (run.json). Standard library only.
"""
import datetime
import json
import os
import pathlib
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import warnings

__all__ = ["Run", "find_project", "new_id", "RUNNING", "DONE", "FAILED", "INTERRUPTED"]

RUNNING, DONE, FAILED, INTERRUPTED = "运行中", "完成", "失败", "被中断"
MAX_SAMPLES = 200                         # samples.jsonl rows kept per run (the console shows the first 20)
MAX_ARTIFACT_BYTES = 10 * 1024 * 1024     # the console serves files up to this size
ARTIFACT_TYPES = {".png", ".jpg", ".jpeg", ".svg", ".json", ".md"}  # what the console can show
MAX_PENDING = 20000                       # lines kept in memory while the shared disk refuses writes
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
FALLBACK_DIR = pathlib.Path.home() / ".local" / "state" / "boardrun"


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id():
    """A fresh run id: UTC time plus 8 random hex digits, e.g. 20261122T051500Z-1a2b3c4d."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4)


def find_project(start=None):
    """The project directory: $BOARD_PROJECT_DIR if set, else the nearest directory at or above
    `start` (default: the current directory) that has board/project.json."""
    env = os.environ.get("BOARD_PROJECT_DIR")
    if env:
        return pathlib.Path(env).resolve()
    here = pathlib.Path(start or os.getcwd()).resolve()
    for d in [here, *here.parents]:
        if (d / "board" / "project.json").is_file():
            return d
    raise FileNotFoundError("no board/project.json at or above %s (set BOARD_PROJECT_DIR or pass project_dir)" % here)


def _gpus():
    v = os.environ.get("CUDA_VISIBLE_DEVICES")
    if v is None:
        return None
    return [int(x) if x.strip().isdigit() else x.strip() for x in v.split(",") if x.strip()]


def _run(cmd, cwd=None):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def _tmux():
    return _run(["tmux", "display-message", "-p", "#S"]) if os.environ.get("TMUX") else None


def _commit(project):
    """Short commit of the project's code, with -dirty when tracked files have uncommitted changes."""
    head = _run(["git", "rev-parse", "--short=12", "HEAD"], cwd=project)
    if not head:
        return None
    dirty = _run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=project)
    return head + ("-dirty" if dirty else "")


def _number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


class Run:
    """One training, evaluation or data run, recorded in <project>/board/runs/<id>/.

    attempt       the attempt this run belongs to (id in board/attempts/), or None
    kind          train / eval / data / smoke / anything else
    config        settings of this run (a JSON-serialisable dict), shown as a table
    resumed_from  id of the run this one continues (after moving servers or a crash)
    run_id        a readable id instead of the generated one; must not exist yet
    project_dir   the project directory; found from the current directory when omitted
    """

    def __init__(self, attempt, kind="eval", config=None, resumed_from=None, run_id=None, project_dir=None,
                 max_samples=MAX_SAMPLES):
        if attempt is not None and not (isinstance(attempt, str) and attempt):
            raise ValueError("attempt must be an attempt id or None")
        if not (isinstance(kind, str) and kind):
            raise ValueError("kind must be a non-empty string")
        if config is not None and not isinstance(config, dict):
            raise TypeError("config must be a dict")
        json.dumps(config or {})  # fails early on settings that cannot be recorded
        self.project = pathlib.Path(project_dir).resolve() if project_dir else find_project()
        if not (self.project / "board" / "project.json").is_file():
            raise FileNotFoundError("%s has no board/project.json" % self.project)
        self.id = run_id or new_id()
        if not ID_RE.match(self.id):
            raise ValueError("run id may only use letters, digits, '.', '_' and '-': %r" % self.id)
        self.dir = self.project / "board" / "runs" / self.id
        self.dir.mkdir(parents=True, exist_ok=False)  # a resumed run gets a new id; never overwrite a record
        self.record = {"id": self.id, "kind": kind, "attempt": attempt, "status": RUNNING,
                       "server": socket.gethostname().split(".")[0], "gpus": _gpus(), "pid": os.getpid(),
                       "tmux": _tmux(), "started": _now(), "ended": None, "commit": _commit(self.project),
                       "resumed_from": resumed_from, "config": dict(config or {}), "metrics": {}, "artifacts": {}}
        self.max_samples, self.n_samples = max_samples, 0
        self._pending = {"metrics.jsonl": [], "samples.jsonl": []}
        self._dirty, self._warned, self._finished = False, set(), False
        self._lock = threading.RLock()
        self._old_sigterm = None
        self._write_record()  # the first write must succeed: better to fail before the work starts

    # ------------------------------------------------------------ what the run reports
    def log(self, step, **values):
        """One point of the training curves: run.log(120, loss=0.31, lr=1e-4)."""
        bad = [k for k, v in values.items() if not _number(v)]
        if bad or not _number(step):
            raise TypeError("log() takes numbers only (not: %s)" % ", ".join(bad or ["step"]))
        self._append("metrics.jsonl", {"step": step, **values})

    def result(self, group, **metrics):
        """Final numbers of one group (usually a test set): run.result("test_iid", accuracy=0.95, n=602).
        Calling it again for the same group replaces those metrics."""
        if not (isinstance(group, str) and group):
            raise ValueError("group must be a non-empty string (a dataset id from setup.json)")
        bad = [k for k, v in metrics.items() if not _number(v)]
        if bad:
            raise TypeError("result() takes numbers only (not: %s)" % ", ".join(bad))
        with self._lock:
            self.record["metrics"].setdefault(group, {}).update(metrics)
            self._save()

    def sample(self, **row):
        """One example output (input, output, score ...). Returns False once max_samples were written."""
        with self._lock:
            if self.n_samples >= self.max_samples:
                return False
            self.n_samples += 1
            self._append("samples.jsonl", row)
            return True

    def artifact(self, path, name=None):
        """Register a file. Small images, JSON and Markdown are copied next to run.json so the console
        shows them; anything else is recorded by its path only."""
        src = pathlib.Path(path).resolve()
        name = name or src.name
        if src.suffix.lower() in ARTIFACT_TYPES and src.is_file() and src.stat().st_size <= MAX_ARTIFACT_BYTES:
            dst = self.dir / src.name
            try:
                if src != dst:
                    shutil.copyfile(src, dst)
                value = src.name
            except OSError as e:
                self._warn("copy", "could not copy %s into the run record: %s" % (src, e))
                value = str(src)
        else:
            value = str(src)
        with self._lock:
            self.record["artifacts"][name] = value
            self._save()
        return value

    def output_dir(self):
        """<project>/results/<id>/ for large outputs (full predictions, logs); created on first use."""
        d = self.project / "results" / self.id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def finish(self, status=DONE, error=None):
        """Mark the run ended. Called by `with`; call it yourself when not using `with`."""
        with self._lock:
            if self._finished:
                return
            self._finished = True
            self.record.update(status=status, ended=_now())
            if error:
                self.record["error"] = str(error)[:2000]
            for attempt in range(3):  # the final state matters most: retry a soft-mounted disk a few times
                self._flush()
                self._save()
                if not self._dirty and not any(self._pending.values()):
                    return
                time.sleep(2 * (attempt + 1))
            self._fallback()

    # ------------------------------------------------------------ with-block and signals
    def __enter__(self):
        if threading.current_thread() is threading.main_thread() and signal.getsignal(signal.SIGTERM) == signal.SIG_DFL:
            self._old_sigterm = signal.signal(signal.SIGTERM, self._on_sigterm)
        return self

    def __exit__(self, exc_type, exc, tb):
        self._restore_sigterm()
        if exc_type is None:
            self.finish(DONE)
        elif issubclass(exc_type, KeyboardInterrupt):
            self.finish(INTERRUPTED, "KeyboardInterrupt")
        elif issubclass(exc_type, SystemExit):
            code = exc.code if exc is not None else None
            self.finish(DONE if code in (0, None) else FAILED, None if code in (0, None) else "exit code %s" % code)
        else:
            self.finish(FAILED, "%s: %s" % (exc_type.__name__, exc))
        return False  # never swallow the exception

    def _on_sigterm(self, signum, frame):
        self.finish(INTERRUPTED, "SIGTERM")
        self._restore_sigterm()
        os.kill(os.getpid(), signal.SIGTERM)  # then die the way SIGTERM normally does

    def _restore_sigterm(self):
        if self._old_sigterm is not None:
            signal.signal(signal.SIGTERM, self._old_sigterm)
            self._old_sigterm = None

    # ------------------------------------------------------------ writing (the shared disk may refuse)
    def _write_record(self):
        tmp = self.dir / (".run.json.tmp.%d" % os.getpid())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.record, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.dir / "run.json")  # readers never see a half-written file

    def _save(self):
        try:
            self._write_record()
            self._dirty = False
        except OSError as e:
            self._dirty = True
            self._warn("record", "run.json not written (will retry): %s" % e)

    def _append(self, fname, row):
        with self._lock:
            pend = self._pending[fname]
            pend.append(json.dumps(row, ensure_ascii=False))
            if len(pend) > MAX_PENDING:
                del pend[: len(pend) - MAX_PENDING]
            self._flush()
            if self._dirty:
                self._save()

    def _flush(self):
        for fname, pend in self._pending.items():
            if not pend:
                continue
            try:
                with open(self.dir / fname, "a", encoding="utf-8") as f:
                    f.write("".join(line + "\n" for line in pend))
                pend.clear()
            except OSError as e:
                self._warn(fname, "%s not written (kept in memory, will retry): %s" % (fname, e))

    def _fallback(self):
        """The shared disk kept failing at the end: keep the record locally and say where."""
        try:
            FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
            path = FALLBACK_DIR / ("%s.json" % self.id)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"record": self.record, "pending": self._pending, "dir": str(self.dir)}, f, ensure_ascii=False)
            self._warn("fallback", "run record kept at %s; copy it to %s when the shared disk is back" % (path, self.dir))
        except OSError as e:
            self._warn("fallback", "run record could not be saved anywhere: %s" % e)

    def _warn(self, key, msg):
        if key not in self._warned:  # once per kind of trouble, not once per step
            self._warned.add(key)
            warnings.warn("boardrun %s: %s" % (self.id, msg), RuntimeWarning, stacklevel=3)
