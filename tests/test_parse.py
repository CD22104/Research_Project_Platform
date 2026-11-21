"""Parser tests for projects.py (record format v0). Run: python3 -m unittest -v"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import projects as P  # noqa: E402

DEMO = os.path.join(HERE, "fixtures", "projects", "demo", "board")


class FrontMatter(unittest.TestCase):
    def test_values_lists_null_comments(self):
        meta, body = P.parse_front_matter(
            "---\nid: v2\nparent: v1   # a comment\nmerged: [t-a, t-b]\nempty: []\n"
            "gone: null\nblank:\ntitle: C# 实验\n# whole-line comment\n---\nbody text\n")
        self.assertEqual(meta, {"id": "v2", "parent": "v1", "merged": ["t-a", "t-b"], "empty": [],
                                "gone": None, "blank": None, "title": "C# 实验"})
        self.assertEqual(body, "body text\n")

    def test_value_with_colons_is_not_split(self):
        meta, _ = P.parse_front_matter("---\nstarted: 2026-11-21T05:00:00Z\nurl: https://x.org/a\n---\n")
        self.assertEqual(meta["started"], "2026-11-21T05:00:00Z")
        self.assertEqual(meta["url"], "https://x.org/a")

    def test_no_front_matter(self):
        self.assertEqual(P.parse_front_matter("## 假设\nabc"), ({}, "## 假设\nabc"))

    def test_unclosed_front_matter_is_body(self):
        text = "---\nid: x\nno closing line"
        self.assertEqual(P.parse_front_matter(text), ({}, text))

    def test_crlf(self):
        meta, body = P.parse_front_matter("---\r\nid: x\r\n---\r\nhello")
        self.assertEqual((meta, body), ({"id": "x"}, "hello"))


class Sections(unittest.TestCase):
    def test_split_and_fences(self):
        s = P.split_sections("intro\n## 假设\nA\n\n## 预期\n\n## 结果\n```\n## not a heading\n```\n")
        self.assertEqual(s, {"假设": "A", "预期": "", "结果": "```\n## not a heading\n```"})

    def test_needs_input(self):
        full = {"预期": "x", "结论和原因": "y"}
        self.assertEqual(P.needs_input({"status": "成功"}, full), [])
        self.assertEqual(P.needs_input({"status": "进行中"}, {"预期": "x"}), [])  # no conclusion yet is fine
        self.assertEqual(P.needs_input({"status": "失败"}, {"预期": "x"}), ["结论和原因"])
        self.assertEqual(P.needs_input({"status": "待确认"}, {}), ["预期", "结论和原因", "结论待确认"])
        self.assertEqual(P.needs_input({}, {}), ["预期"])


class Jsonl(unittest.TestCase):
    def test_fixture_metrics(self):
        m = P.read_metrics(os.path.join(DEMO, "runs", "r001", "metrics.jsonl"))
        self.assertEqual(m["keys"], ["loss", "lr"])        # 'note' is text, ignored
        self.assertEqual(m["total"], 50)                   # incomplete last line skipped
        self.assertEqual(m["bad_lines"], 1)                # corrupted middle line counted
        self.assertEqual(m["points"][-1]["step"], 2450)
        self.assertFalse(m["downsampled"])

    def test_downsample_bounded_keeps_ends(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            for s in range(100_000):
                f.write(json.dumps({"step": s, "loss": 1 / (s + 1)}) + "\n")
        try:
            m = P.read_metrics(f.name, max_points=500)
        finally:
            os.unlink(f.name)
        self.assertEqual(m["total"], 100_000)
        self.assertLessEqual(len(m["points"]), 500)
        self.assertTrue(m["downsampled"])
        self.assertEqual(m["points"][0]["step"], 0)
        self.assertEqual(m["points"][-1]["step"], 99_999)
        steps = [p["step"] for p in m["points"]]
        self.assertEqual(steps, sorted(steps))

    def test_rows_without_step_or_numbers(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write('{"loss": 1.0}\n{"text": "x"}\n[1, 2]\n\n{"loss": 0.5, "flag": true}\n')
        try:
            m = P.read_metrics(f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual([p["step"] for p in m["points"]], [0, 1])  # line index used as step
        self.assertEqual(m["keys"], ["loss"])                       # booleans are not metrics
        self.assertEqual(m["bad_lines"], 1)                         # the JSON array

    def test_head(self):
        rows = P.read_jsonl_head(os.path.join(DEMO, "examples", "probe.jsonl"))
        self.assertEqual(rows, [{"input": "probe 1", "expected": 1}, {"_raw": "not json"}])


class Text(unittest.TestCase):
    def test_truncate_and_bad_utf8(self):
        with tempfile.NamedTemporaryFile("wb", delete=False) as f:
            f.write(b"\xff" + "中".encode() * 10)
        try:
            text, cut = P.read_text(f.name, limit=7)
        finally:
            os.unlink(f.name)
        self.assertTrue(cut)
        self.assertTrue(text.startswith("�"))


if __name__ == "__main__":
    unittest.main()
