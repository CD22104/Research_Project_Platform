"""Personal settings of the research-board tools and console, in one JSON file.

Everything that belongs to one person or one lab (where projects live, which GPU servers there are, what the
console is called) is read from here and nowhere else, so the skill and the console can be shared as they are.
The file is ~/.config/research-board/config.json, or wherever $RESEARCH_BOARD_CONFIG points; the repository
holds only config.example.json. Standard library only (Python 3.8+).
"""
import json
import os

DEFAULT_PATH = "~/.config/research-board/config.json"
ENV = "RESEARCH_BOARD_CONFIG"
REQUIRED = ("projects_root", "servers")
CONSOLE_DEFAULTS = {"name": "研究控制台", "socket": None, "port": 8765, "interval_seconds": 30,
                    "projects_interval_seconds": 60, "jump_host": None}
SESSIONS_DEFAULTS = {"enabled": True, "transcripts": None, "interval_seconds": 60}


class ConfigError(Exception):
    """The configuration file is missing or does not say what is needed; the message says how to fix it."""


def config_path():
    """$RESEARCH_BOARD_CONFIG, else the default place; ~ expanded."""
    return os.path.expanduser(os.environ.get(ENV) or DEFAULT_PATH)


def _path(p):
    return os.path.expanduser(p) if isinstance(p, str) and p else None


def load(path=None):
    """The validated configuration with defaults filled in and paths expanded:
    {"projects_root", "shared_dir", "tools_dir", "state_dir", "servers": [{"name", "host"}], "home_server",
     "claude_bin", "secrets_dir", "max_task_sessions", "console": {...}, "sessions": {...}, "path"}."""
    path = os.path.expanduser(path) if path else config_path()
    hint = "照仓库里的 config.example.json 写一份（或者用环境变量 %s 指到它）" % ENV
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        raise ConfigError("找不到配置文件 %s：%s" % (path, hint)) from None
    except ValueError as e:
        raise ConfigError("配置文件 %s 不是合法的 JSON：%s" % (path, e)) from None
    if not isinstance(raw, dict):
        raise ConfigError("配置文件 %s 应该是一个 JSON 对象：%s" % (path, hint))
    missing = [k for k in REQUIRED if raw.get(k) in (None, "")]
    if missing:
        raise ConfigError("配置文件 %s 缺少 %s：%s" % (path, "、".join(missing), hint))
    servers = raw["servers"]   # may be empty: records only, no GPU polling
    if not (isinstance(servers, list) and all(isinstance(s, dict) and isinstance(s.get("name"), str) and s["name"]
                                              and isinstance(s.get("host"), str) and s["host"] for s in servers)):
        raise ConfigError('配置文件 %s 的 servers 应该是 [{"name": "短名", "host": "完整主机名"}, ...]' % path)
    root = _path(raw["projects_root"])
    shared = _path(raw.get("shared_dir")) or os.path.join(root, "_shared")
    names = [s["name"] for s in servers]
    home = raw.get("home_server") or (names[0] if names else None)
    if home is not None and home not in names:
        raise ConfigError("配置文件 %s 的 home_server「%s」不在 servers 里" % (path, home))
    console = dict(CONSOLE_DEFAULTS, **(raw.get("console") or {}))
    console["socket"] = _path(console.get("socket"))
    return {"path": path, "projects_root": root, "shared_dir": shared,
            "tools_dir": _path(raw.get("tools_dir")) or os.path.join(shared, "tools"),
            "state_dir": _path(raw.get("state_dir")) or os.path.expanduser("~/.local/state/research-board"),
            "servers": [{"name": s["name"], "host": s["host"]} for s in servers], "home_server": home,
            "claude_bin": _path(raw.get("claude_bin")) or os.path.expanduser("~/.local/bin/claude"),
            "secrets_dir": _path(raw.get("secrets_dir")) or os.path.expanduser("~/.config/secrets"),
            "max_task_sessions": int(raw.get("max_task_sessions") or 4),
            "console": console, "sessions": dict(SESSIONS_DEFAULTS, **(raw.get("sessions") or {}))}


def short_names(names):
    """Short names for the page: the prefix all the names share is dropped (xtraa100, xtrah100 -> a100, h100).
    One name, or names that would become empty, are kept as they are."""
    names = list(names)
    if len(names) < 2:
        return {n: n for n in names}
    prefix = os.path.commonprefix(names)
    return {n: n[len(prefix):] or n for n in names}
