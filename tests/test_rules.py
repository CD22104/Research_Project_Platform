"""The rules checklist (skill references/rules.md): every rule is where it says it is. Run: python3 -m unittest -v"""
import os
import re
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SKILL = os.path.join(REPO, "skill", "research-board")
RULES = os.path.join(SKILL, "references", "rules.md")
GLOBAL = os.path.expanduser("~/.claude/CLAUDE.md")

TOKEN = re.compile(r"「([^」]*)」|`([^`]+)`|(全局规则)")
PATHLIKE = re.compile(r"^[\w./-]+\.(md|py|js|html|json)$")


def norm(text):
    """Whitespace, backticks and bold marks do not count."""
    return re.sub(r"[\s`*]", "", text)


def entries(text):
    """[(number, title, [lines])] of the checklist's '### Rnn title' entries."""
    out = []
    for m in re.finditer(r"^### (R\d+) (.+)$", text, re.M):
        end = text.find("\n### ", m.end())
        body = text[m.end():] if end < 0 else text[m.end():end]
        out.append((m.group(1), m.group(2), [l.strip() for l in body.split("\n") if l.strip().startswith("- ")]))
    return out


def resolve(path, skill, repo):
    return os.path.join(repo if path.startswith(("tests/", "platform/")) else skill, path)


def verify(text, skill, repo, global_path):
    """Problems of a checklist: missing files, functions, tests, sentences; numbering; an entry without a
    guarantee, code without a test. Sentences in the global rules are checked only when global_path exists."""
    problems, cache = [], {}

    def read(p):
        if p not in cache:
            try:
                with open(p, encoding="utf-8") as f:
                    cache[p] = f.read()
            except OSError:
                cache[p] = None
        return cache[p]
    found = entries(text)
    nums = [n for n, _, _ in found]
    if nums != ["R%02d" % i for i in range(1, len(nums) + 1)]:
        problems.append("编号不连续或重复：%s" % " ".join(nums))
    for num, title, lines in found:
        kinds = {l[2:4] for l in lines}
        if not kinds & {"文档", "代码"}:
            problems.append("%s %s：没写由什么保证（文档或代码）" % (num, title))
        if "代码" in kinds and "测试" not in kinds:
            problems.append("%s %s：有代码没有测试" % (num, title))
        for line in lines:
            target = shown = None          # the file the names and sentences that follow belong to, as written
            for m in TOKEN.finditer(line):
                quote, tick, glob = m.groups()
                if glob:
                    target, shown = global_path, "全局规则"
                elif tick and PATHLIKE.match(tick):
                    target, shown = resolve(tick, skill, repo), tick
                    if read(target) is None:
                        problems.append("%s：文件 %s 不在" % (num, tick))
                elif target is None or read(target) is None:
                    continue
                elif tick:
                    pat = (r"^\s*def %s\b" if line.startswith("- 测试") else r"(?<![\w])%s(?![\w])") % re.escape(tick)
                    if not re.search(pat, read(target), re.M):
                        problems.append("%s：%s 里找不到 %s" % (num, shown, tick))
                elif quote is not None and norm(quote) not in norm(read(target)):
                    problems.append("%s：%s 里找不到「%s」" % (num, shown, quote))
    return problems


class Checklist(unittest.TestCase):
    def test_every_rule_is_where_it_says(self):
        with open(RULES, encoding="utf-8") as f:
            text = f.read()
        self.assertGreaterEqual(len(entries(text)), 25)
        g = GLOBAL if os.path.isfile(GLOBAL) else os.path.join(REPO, "no-global-rules-here")
        problems = verify(text, SKILL, REPO, g)
        self.assertEqual(problems, [], "\n".join(problems))
        if not os.path.isfile(GLOBAL):
            self.skipTest("no ~/.claude/CLAUDE.md here: the global rules' sentences were not checked")

    def test_the_checker_catches_what_goes_missing(self):
        with tempfile.TemporaryDirectory() as d:
            skill = os.path.join(d, "skill")
            os.makedirs(os.path.join(skill, "references"))
            os.makedirs(os.path.join(d, "tests"))
            with open(os.path.join(skill, "references", "a.md"), "w", encoding="utf-8") as f:
                f.write("先问用户，**说明**打算用\n哪几张卡。\n")
            with open(os.path.join(skill, "x.py"), "w") as f:
                f.write("def gate():\n    pass\nLIMIT = 3\n")
            with open(os.path.join(d, "tests", "test_x.py"), "w") as f:
                f.write("    def test_gate(self):\n")
            glob = os.path.join(d, "CLAUDE.md")
            with open(glob, "w", encoding="utf-8") as f:
                f.write("启动前先问我，并说明 `卡`。\n")
            ok = ("### R01 先问\n- 来源：全局规则「先问我，并说明 卡」\n- 文档：`references/a.md`「先问用户，说明打算用哪几张卡」\n"
                  "- 代码：`x.py` 的 `gate`、`LIMIT`\n- 测试：`tests/test_x.py` 的 `test_gate`\n")
            self.assertEqual(verify(ok, skill, d, glob), [])
            bad = (ok.replace("哪几张卡", "哪台服务器").replace("`gate`", "`gone`").replace("test_gate", "test_away")
                   + "### R03 漏了\n- 代码：`x.py` 的 `gate`\n" + "### R04 没保证\n- 来源：全局规则「先问我」\n")
            self.assertEqual(verify(bad, skill, d, glob), [
                "编号不连续或重复：R01 R03 R04",
                "R01：references/a.md 里找不到「先问用户，说明打算用哪台服务器」",
                "R01：x.py 里找不到 gone",
                "R01：tests/test_x.py 里找不到 test_away",
                "R03 漏了：有代码没有测试",
                "R04 没保证：没写由什么保证（文档或代码）"])
            self.assertIn("R01：文件 references/c.md 不在", verify(ok.replace("a.md", "c.md"), skill, d, glob))
            self.assertEqual(verify(ok.replace("并说明 卡", "并说明哪几张卡"), skill, d, glob), ["R01：全局规则 里找不到「先问我，并说明哪几张卡」"])
            self.assertEqual(verify(ok, skill, d, os.path.join(d, "absent.md")), [])   # no global rules here: not checked


if __name__ == "__main__":
    unittest.main()
