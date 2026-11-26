"""Forwarding file while the repository moves to platform/ and skill/: runs the skill's boardtool.py.
The crontab and the a100 hooks still call ~/gpu-board/boardtool.py; remove once they use the installed skill."""
import os
import runpy
import sys

target = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skill", "research-board", "scripts", "boardtool.py")
sys.argv[0] = target
runpy.run_path(target, run_name="__main__")
