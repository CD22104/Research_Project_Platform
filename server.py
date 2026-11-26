"""Forwarding file while the repository moves to platform/ and skill/: runs platform/server.py.
The crontab and the tmux session gpuboard still start ~/gpu-board/server.py; remove once they point to platform/."""
import os
import runpy
import sys

target = os.path.join(os.path.dirname(os.path.abspath(__file__)), "platform", "server.py")
sys.argv[0] = target
runpy.run_path(target, run_name="__main__")
