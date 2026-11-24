# 控制台开发：交接

## 交接（2026-11-24，从旧服务台会话拆出来）

### 今天做完的（都已提交，测试全过）
记录格式第 2 版（一页纸、任务卡、决定、纪要、术语、`{n}` 指标）→ 运行心跳和收尾 → 事实包和两个钩子 → 控制台重构（今天、项目头、研究 / 工作两组页签、会话页、时间线、记录维护）→ 任务会话和派活 → 使用说明 → 待办按处理进度分组 → 设计思路页（可导出单个 HTML）→ 收尾只关带心跳的运行 → 暂停、归档的项目不进「今天」。设计思路页（`#/design`）写了整套做法和取舍。

### 要接着做的
1. **图解（用户 2026-11-24 已同意，可以直接做）**：项目里常让 agent 画图帮用户理解，统一成：
   - 每张图一个自包含的 HTML，放 `board/figures/<名字>.html`；`<title>` 写标题，`<meta name="about" content="a:...">` 写属于哪一步（写法同文档的 about），`<meta name="question" content="...">` 写这张图回答什么。不引用外部网址。
   - 内容约定：开头一句话说回答什么；先给一条真实样例，再讲抽象结论；数字能对到运行编号；坐标轴写中文和单位；深浅色都能看；手机宽度不出横向滚动条。
   - 控制台：扫描 `board/figures/*.html`（只读开头几 KB 取 title 和 meta），在「文档」页作为一类「图解」显示，按 about 归到流程的那一步下，详情里用隔离 iframe 显示（`sandbox="allow-scripts"`，不给 same-origin），另给「在新窗口打开」；可以标看过 / 确认（`post_mark` 允许的文件加上 figures）；实验详情的「这一步的文档」里也列出来。
   - `/files/` 下 `.html` 用单独的 CSP：`default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; sandbox allow-scripts`（脚本能运行，但没有同源、不能联网）。
   - 记录检查：没有 title、about 指向不存在的记录、文件超过 10 MB。
   - `docs/board-format.md` 加一节「figures/（图解）」，测试覆盖解析、检查、页面、隔离。做完发布格式，告诉研究服务台去分发规则里那句「画图做成自包含 HTML 放 board/figures/」。
   - 已有的 HTML（例如 jev-authz 的 `board/legacy-site/`）不动。
2. **boardrun 的「代码有没有改动」检查要排除记录**：现在用 `git status --porcelain --untracked-files=no` 看整个仓库，`board/` 里的记录（例如正在跑的运行的 `run.json`）、`NOTES.md`、`CLAUDE.md` 改了也算「有未提交改动」，项目记录一更新，下一次运行就被拒。改成只看代码：排除 `board`、`NOTES.md`、`REPORT.md`、`CLAUDE.md`（git pathspec `:(exclude)...`）。`tests/test_boardrun.py` 第 9 个测试现在靠改 `board/project.json` 制造改动，要改成改一个代码文件，并加一条「只改 board/ 不算改动」的断言。改完发布 boardrun。
3. 以后可以做的（设计思路页第 18 节）：会话页看到其他服务器的会话、手机上看进度、自动回收闲置会话、记录维护的定时轮次、模型分级、迁移辅助。都要先和用户确认。

### 踩过的坑
- 测试串 `| tail` 后面接 `&& git commit` 时，要先 `set -o pipefail`，否则失败也会提交（今天发生过两次，后来补了提交）。
- h100、h200 拒绝频繁 ssh：收尾程序每台每轮只连一次。
- 手写的运行记录「进程不在」不等于被中断：今天收尾程序误标了 jev-fraud 的 4 个运行，其中 3 个其实跑完了，后来已改成只关带心跳的运行。
