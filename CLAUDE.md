# 控制台开发（a100 `~/gpu-board`）

研究控制台（`platform/`）和 skill research-board（`skill/`）的代码。在 App 的 desk 文件夹里开对话；开场先读本文件和 NOTES.md，代码只在 ~/gpu-board 里改。服务器、账号、规则分发、跨项目协调归「研究服务台」会话（tmux `rc-desk`，`~/desk`），不在这里做。

## 组成
仓库分两部分（2026-11-26 起）。个人设置只在 `~/.config/research-board/config.json`（`rbconfig.py` 读；测试用 `$RESEARCH_BOARD_CONFIG`），仓库里只有 `config.example.json`。

| 位置 | 做什么 |
|---|---|
| `skill/research-board/` | skill 本体，原样装到各台 `~/.claude/skills/research-board/`（研究服务台分发，用户同意后）：`SKILL.md`、`references/`（`board-format.md` 等 7 份）、`scripts/`（`board`、`boardtool.py`、`projects.py`、`rbconfig.py`、`boardrun/`、`make-manifest`）、`MANIFEST.sha256`、`VERSION` |
| `skill/research-board/scripts/projects.py` | 解析各项目 `board/`、记录检查、运行状态、事实包和概况用的汇总（控制台也 import 它） |
| `skill/research-board/scripts/boardtool.py` | `board` 命令：`brief`、`check`、`reconcile`、`task-begin` / `task-start` / `task-stop`、`dispatch` |
| `platform/` | 控制台：`server.py`（HTTP，unix socket；显卡轮询、项目扫描、会话列表、四个写接口、`/site.js`）、`sessions.py`、网页（`index.html`、`common.js`、`work.js`、`design.js`、`home.js`、`app.js`）、`docs/guide.md` |
| `tests/` | 全部测试和夹具（不进 skill） |
| `scripts/` | `publish-boardrun.sh`、`publish-format.sh`（目的地读配置）、`export.py`（可分享的副本） |
| 根目录 | `README.md`、`config.example.json`；运行时文件 `run/`、`state/`、`server.log`（gitignore） |

## 规则
- 这是核心代码：先给 2–3 种方案和取舍，列出要替用户定的设计决定，函数签名和关键测试给用户确认后再实现；小步改，每步说明。用户已经同意过的方案（见 NOTES.md）可以直接做。
- 安全约束：控制台只写 `answers.jsonl`、`marks.jsonl`、`state/names.json`，写接口都要过 `write_refused`；不从网页启动任何程序；会话页只读元数据；agent 写的 HTML（图解）只能放在隔离的 iframe 里（`sandbox="allow-scripts"`，不给 same-origin）。
- 收尾程序只自动关闭带心跳（boardrun 启动）的运行；手写记录的运行只提示，不改。

## 测试和上线
- 测试：`set -o pipefail; python3 -m unittest discover -s tests 2>&1 | tail -3`。要先 `set -o pipefail`，否则 `| tail` 会吞掉失败、照样提交。全套跑时把输出存到文件里再看（偶发失败要留证据）。
- **改了 skill 目录里的任何文件**，运行 `skill/research-board/scripts/make-manifest` 重新生成文件清单再提交（有测试检查）。skill 的改动等于改规则：要研究服务台给用户看、用户同意后分发到各台才生效；钩子和 `~/bin/board`、crontab 用的都是各台装好的副本，不是仓库里的。
- 静态文件（html、js）改了，刷新页面就生效；`platform/server.py`、`sessions.py`、`projects.py` 改了要重启控制台：
  `tmux kill-session -t =gpuboard; tmux new-session -d -s gpuboard 'cd /home/dchen/gpu-board && /usr/bin/python3 platform/server.py >> /home/dchen/gpu-board/server.log 2>&1'`
- 发布到共享盘：`scripts/publish-boardrun.sh`（boardrun，项目按路径依赖它）、`scripts/publish-format.sh`（格式文档的副本，给旧项目的 `board/FORMAT.md`）。测试通过后再发布。`publish-board.sh` 已停用（各台改用装好的 skill）。
- 分享：`python3 scripts/export.py <目录>`；`tests/test_export.py` 用本机配置里的词扫描整个仓库，个人信息不能进 `skill/`、`platform/`（设计思路页和使用说明除外，导出时替换）、`tests/`、`scripts/`。
- 看实际页面：控制台只在 unix socket 上，Chromium 连不上；用测试里的 `start_server`（临时配置、本机端口）或类似的小脚本起一个只读的副本，再用 `chromium-browser --headless=new --screenshot=...`。snap 版 Chromium 只能写 `~/snap/chromium/common/` 下面；长图用 `ffmpeg -vf crop=...` 裁开看（机器上没有 PIL）。
- 提交：每一步测试全过后提交，提交信息写清改了什么、为什么。

## 别的
- 旧的服务台会话（2026-11-21 到 11-24）积累的记忆在 `~/.claude/projects/-home-dchen/memory/`，需要时读（例如后台等待脚本的坑）。
