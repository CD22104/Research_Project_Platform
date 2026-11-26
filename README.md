# research-board：研究项目的记录方式、工具和控制台

用户和几个 Claude Code 会话一起推进研究项目时用的一整套做法：

- **skill/research-board/**：一个 Claude Code skill。`SKILL.md` 是总的做法，`references/` 是记录格式和各个流程（新建和结束项目、长任务、任务会话、共享资源、怎么让人看懂项目），`scripts/` 是 `board` 命令（事实包、记录检查、任务会话、收尾、派活）、记录运行的 Python 模块 `boardrun`，以及读配置的 `rbconfig.py`。只装 skill 也能用：记录格式、流程和命令都在里面。
- **platform/**：研究控制台。一个只用标准库的 Python 服务加几页网页，从各项目的 `board/` 生成项目首页（简报 + 项目地图）、实验流程、运行表和交叉表、文档、论文、任务看板，并轮询各服务器的显卡。它只写自己的三样东西（回复、文档标记、显示名），不启动任何程序。
- **tests/**：两部分的测试（`python3 -m unittest discover -s tests`；页面测试要有 Chromium）。

## 安装
1. **配置**：照 `config.example.json` 写 `~/.config/research-board/config.json`（或用环境变量 `RESEARCH_BOARD_CONFIG` 指到别处）。必填的只有项目根目录和服务器列表；个人和实验室的设置都只在这个文件里。
2. **skill**：把 `skill/research-board/` 整个拷到每台机器的 `~/.claude/skills/research-board/`。核对：在那个目录里运行 `sha256sum -c --quiet MANIFEST.sha256`；`MANIFEST.sha256` 自己的 sha256 就是版本号（改了 skill 之后运行 `scripts/make-manifest` 重新生成）。
3. **钩子**（可选，推荐）：在 `~/.claude/settings.json` 里让 SessionStart 运行 `~/.claude/skills/research-board/scripts/board brief --hook`，PostToolUse（Write、Edit）运行 `… board check --hook`。会话在项目里一开场就有事实包，改记录时当场看到格式问题。
4. **boardrun**：把 `skill/research-board/scripts/boardrun/` 放到所有服务器都能访问的工具目录（`scripts/publish-boardrun.sh`），项目里 `uv add --editable <工具目录>/boardrun`。
5. **控制台**（可选）：在一台机器上运行 `python3 platform/server.py`（监听配置里的 socket 或端口）；用定时任务每 5 分钟运行 `board reconcile` 和 `board dispatch`。

## 分享
`python3 scripts/export.py <目标目录>` 拷出一份可以分享的副本（只有一个提交）：去掉控制台开发自己的笔记，把只属于某个人的页面换成通用说明，并扫描主机名、路径、用户名、项目名等个人信息，扫到就停下。
