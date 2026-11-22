"""python -m boardrun run --attempt <id> [--kind eval] [--id <run id>] [--resumed-from <run id>]
                          [--config '<json>'] [--project <dir>] -- <command ...>

Records a command that is not Python (or not changed to use boardrun) as a run: exit code 0 is 完成,
any other code 失败, Ctrl-C / SIGTERM 被中断. The command sees BOARDRUN_ID and BOARDRUN_OUTPUT
(its results/<id>/ directory). Exits with the command's exit code.
"""
import argparse
import json
import os
import signal
import subprocess
import sys

from . import DONE, FAILED, INTERRUPTED, Run


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" not in argv:
        print(__doc__, file=sys.stderr)
        return 2
    cut = argv.index("--")
    ap = argparse.ArgumentParser(prog="python -m boardrun")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--attempt", required=True)
    r.add_argument("--kind", default="eval")
    r.add_argument("--id", dest="run_id")
    r.add_argument("--resumed-from")
    r.add_argument("--config", type=json.loads, default=None)
    r.add_argument("--project")
    a = ap.parse_args(argv[:cut])
    cmd = argv[cut + 1:]
    if not cmd:
        ap.error("no command after --")
    run = Run(a.attempt, kind=a.kind, config=a.config, resumed_from=a.resumed_from, run_id=a.run_id, project_dir=a.project)
    env = dict(os.environ, BOARDRUN_ID=run.id, BOARDRUN_OUTPUT=str(run.output_dir()))
    try:
        child = subprocess.Popen(cmd, env=env)
    except OSError as e:
        run.finish(FAILED, "could not start: %s" % e)
        return 127
    stopped = []

    def forward(signum, frame):
        stopped.append(signum)
        child.send_signal(signum)
    old = {s: signal.signal(s, forward) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        code = child.wait()
    finally:
        for s, h in old.items():
            signal.signal(s, h)
    if stopped or code in (-signal.SIGTERM, -signal.SIGINT, -signal.SIGKILL):
        run.finish(INTERRUPTED, "signal %s" % (stopped[0] if stopped else -code))
    elif code == 0:
        run.finish(DONE)
    else:
        run.finish(FAILED, "exit code %d" % code)
    return code if code >= 0 else 128 - code


if __name__ == "__main__":
    sys.exit(main())
