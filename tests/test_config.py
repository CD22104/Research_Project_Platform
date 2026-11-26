"""The one configuration file (skill/research-board/scripts/rbconfig.py). Run: python3 -m unittest -v"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "skill", "research-board", "scripts"))
import rbconfig as C  # noqa: E402

EXAMPLE = os.path.join(REPO, "config.example.json")


class Config(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def write(self, obj, name="config.json"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(obj if isinstance(obj, str) else json.dumps(obj))
        return path

    def test_example_loads_with_defaults_filled(self):
        cfg = C.load(EXAMPLE)
        self.assertEqual(cfg["projects_root"], "/shared/<you>/projects")
        self.assertEqual([s["name"] for s in cfg["servers"]], ["gpu-a100", "gpu-h100"])
        self.assertTrue(cfg["console"]["socket"].startswith(os.path.expanduser("~")))   # ~ expanded
        small = C.load(self.write({"projects_root": "~/p", "servers": [{"name": "a", "host": "a.x"}]}))
        self.assertEqual(small["shared_dir"], os.path.join(os.path.expanduser("~/p"), "_shared"))
        self.assertEqual(small["tools_dir"], os.path.join(small["shared_dir"], "tools"))
        self.assertEqual((small["home_server"], small["max_task_sessions"], small["console"]["port"], small["sessions"]["enabled"]),
                         ("a", 4, 8765, True))
        bare = C.load(self.write({"projects_root": "/p", "servers": []}, "bare.json"))        # records only, no GPU polling
        self.assertEqual((bare["servers"], bare["home_server"]), ([], None))

    def test_environment_variable_wins(self):
        path = self.write({"projects_root": "/p", "servers": [{"name": "a", "host": "a.x"}]})
        with mock.patch.dict(os.environ, {C.ENV: path}):
            self.assertEqual(C.config_path(), path)
            self.assertEqual(C.load()["path"], path)
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(C.config_path(), os.path.expanduser(C.DEFAULT_PATH))

    def test_problems_say_what_to_fix(self):
        cases = [(os.path.join(self.tmp, "nope.json"), "找不到配置文件", True),        # True: points to the example
                 (self.write("{not json", "bad.json"), "不是合法的 JSON", False),
                 (self.write({"servers": [{"name": "a", "host": "a.x"}]}, "noroot.json"), "缺少 projects_root", True),
                 (self.write({"projects_root": "/p"}, "noservers.json"), "缺少 servers", True),
                 (self.write({"projects_root": "/p", "servers": [{"name": "a"}]}, "nohost.json"), "servers 应该是", False),
                 (self.write({"projects_root": "/p", "servers": [{"name": "a", "host": "a.x"}], "home_server": "b"}, "home.json"), "home_server「b」", False)]
        for path, words, example in cases:
            with self.subTest(words=words):
                with self.assertRaises(C.ConfigError) as e:
                    C.load(path)
                self.assertIn(words, str(e.exception))
                self.assertEqual("config.example.json" in str(e.exception), example)

    def test_short_names(self):
        self.assertEqual(C.short_names(["xtraa100", "xtrah100", "xtraa6k01"]), {"xtraa100": "a100", "xtrah100": "h100", "xtraa6k01": "a6k01"})
        self.assertEqual(C.short_names(["gpu1"]), {"gpu1": "gpu1"})
        self.assertEqual(C.short_names(["node", "node2"]), {"node": "node", "node2": "2"})   # never empty


if __name__ == "__main__":
    unittest.main()
