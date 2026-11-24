# 控制台开发：交接

## 交接（2026-11-24，从旧服务台会话拆出来）

### 今天做完的（都已提交，测试全过）
记录格式第 2 版（一页纸、任务卡、决定、纪要、术语、`{n}` 指标）→ 运行心跳和收尾 → 事实包和两个钩子 → 控制台重构（今天、项目头、研究 / 工作两组页签、会话页、时间线、记录维护）→ 任务会话和派活 → 使用说明 → 待办按处理进度分组 → 设计思路页（可导出单个 HTML）→ 收尾只关带心跳的运行 → 暂停、归档的项目不进「今天」。设计思路页（`#/design`）写了整套做法和取舍。

### 图解（2026-11-24 做完，提交 b08ee3b…0201395，格式和 boardtool 已发布，控制台已重启）
- 后端：`projects.read_figure_head` 只读 `board/figures/*.html` 开头 8 KB（到 `<body>` 或 `<svg>` 为止）取 title / about / question，扫描结果里单独一个 `figures` 列表；指向 `figures/` 之外的链接不列（和 `/files/` 一致）。检查：开头 8 KB 没有 title、about 指不到、超过 10 MB、链接指向别处。
- 服务：`/files/` 的 `.html` 只放行 `figures/` 下一层，用 `FIGURE_HEADERS` 的 CSP；`legacy-site/` 等旧 HTML 照旧 404。`post_mark` 接受 figures。
- 页面：文档页的一类「图解」，id 是文件名；详情是 `sandbox="allow-scripts"` 的 iframe（固定高度约一屏）加「在新窗口打开」。`setMain` 在每分钟重画时保留同一张图（同文件、同 mtime）的 iframe，图里的操作不丢。
- 测试夹具 `demo/board/figures/lr-warmup.html` 里有一段探测脚本，点击测试靠它验证读不到父页面、localStorage、API，且重画后只加载一次。

### 要接着做的
1. **还差一步：告诉研究服务台**在分发规则里加一句「画图做成自包含 HTML 放 board/figures/（格式见 BOARD_FORMAT.md 的「图解」一节）」。2026-11-24 问过用户要不要发、发给哪个会话（「A100: 研究服务台」还是 tmux rc-desk 的常驻入口），等用户回答。
2. **boardrun 的「代码有没有改动」检查要排除记录**：现在用 `git status --porcelain --untracked-files=no` 看整个仓库，`board/` 里的记录（例如正在跑的运行的 `run.json`）、`NOTES.md`、`CLAUDE.md` 改了也算「有未提交改动」，项目记录一更新，下一次运行就被拒。改成只看代码：排除 `board`、`NOTES.md`、`REPORT.md`、`CLAUDE.md`（git pathspec `:(exclude)...`）。`tests/test_boardrun.py` 第 9 个测试现在靠改 `board/project.json` 制造改动，要改成改一个代码文件，并加一条「只改 board/ 不算改动」的断言。改完发布 boardrun。
3. 以后可以做的（设计思路页第 18 节）：会话页看到其他服务器的会话、手机上看进度、自动回收闲置会话、记录维护的定时轮次、模型分级、迁移辅助。都要先和用户确认。

### 踩过的坑
- 2026-11-24 全套测试有一次偶发失败（共跑 8 遍只失败 1 次，没留输出，不知道是哪条）；再遇到时把输出存下来再查。
- 测试串 `| tail` 后面接 `&& git commit` 时，要先 `set -o pipefail`，否则失败也会提交（今天发生过两次，后来补了提交）。
- h100、h200 拒绝频繁 ssh：收尾程序每台每轮只连一次。
- 手写的运行记录「进程不在」不等于被中断：今天收尾程序误标了 jev-fraud 的 4 个运行，其中 3 个其实跑完了，后来已改成只关带心跳的运行。
