# 控制台开发（a100 `~/gpu-board`）

「CD的研究控制台」和周边工具的代码。在 App 的 desk 文件夹里开对话；开场先读本文件和 NOTES.md，代码只在 ~/gpu-board 里改。服务器、账号、规则分发、跨项目协调归「研究服务台」会话（tmux `rc-desk`，`~/desk`），不在这里做。

## 组成
| 文件 | 做什么 |
|---|---|
| `server.py` | HTTP 服务（unix socket `run/board.sock`）：显卡轮询、项目扫描、会话列表、四个写接口（回复、文档标记、论文打勾、显示名） |
| `projects.py` | 解析各项目 `board/`、记录检查、运行状态 `run_state`、事实包和概况用的汇总 |
| `sessions.py` | a100 上的 Claude 会话：`claude agents --json` 加会话记录的元数据（不读对话内容） |
| `boardtool.py`（`~/bin/board`） | `brief` 事实包、`check` 记录检查、`reconcile` 收尾、`task-start` / `task-stop` 任务会话、`dispatch` 派活 |
| `boardrun/` | 运行记录模块（发布到共享盘 `_shared/tools/boardrun`） |
| `index.html`、`common.js`、`work.js`、`design.js`、`app.js` | 网页：今天、项目页（研究 / 工作两组页签）、待办、会话、显卡、使用说明、设计思路 |
| `docs/board-format.md`、`docs/guide.md` | 记录格式（发布成 `_shared/BOARD_FORMAT.md`）、使用说明 |
| `tests/` | 约 100 个测试，含无头 Chromium 的页面和点击测试 |

## 规则
- 这是核心代码：先给 2–3 种方案和取舍，列出要替用户定的设计决定，函数签名和关键测试给用户确认后再实现；小步改，每步说明。用户已经同意过的方案（见 NOTES.md）可以直接做。
- 安全约束：控制台只写 `answers.jsonl`、`marks.jsonl`、`state/names.json`，写接口都要过 `write_refused`；不从网页启动任何程序；会话页只读元数据；agent 写的 HTML（图解）只能放在隔离的 iframe 里（`sandbox="allow-scripts"`，不给 same-origin）。
- 收尾程序只自动关闭带心跳（boardrun 启动）的运行；手写记录的运行只提示，不改。

## 测试和上线
- 测试：`set -o pipefail; python3 -m unittest discover -s tests 2>&1 | tail -3`。要先 `set -o pipefail`，否则 `| tail` 会吞掉失败、照样提交。
- 静态文件（html、js）改了，刷新页面就生效；`server.py`、`projects.py`、`sessions.py` 改了要重启：
  `tmux kill-session -t =gpuboard; tmux new-session -d -s gpuboard 'cd /home/dchen/gpu-board && /usr/bin/python3 server.py >> /home/dchen/gpu-board/server.log 2>&1'`
- 发布到共享盘：`scripts/publish-format.sh`（记录格式）、`scripts/publish-boardrun.sh`（boardrun）、`scripts/publish-board.sh`（给 h100 钩子用的 boardtool）。改了对应文件、测试通过后再发布。
- 看实际页面：控制台只在 unix socket 上，Chromium 连不上；起一个把 `/api` 转到 socket、静态文件读仓库的小代理（端口随意），再用 `chromium-browser --headless=new --screenshot=...`。snap 版 Chromium 只能写 `~/snap/chromium/common/` 下面；长图用 `ffmpeg -vf crop=...` 裁开看（机器上没有 PIL）。
- 提交：每一步测试全过后提交，提交信息写清改了什么、为什么。

## 别的
- 旧的服务台会话（2026-11-21 到 11-24）积累的记忆在 `~/.claude/projects/-home-dchen/memory/`，需要时读（例如后台等待脚本的坑）。
