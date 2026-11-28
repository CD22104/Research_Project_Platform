---
name: research-board
description: 在有 board/ 的项目里工作、新建/结束/暂停项目、启动长任务时，先加载 skill research-board。研究项目的整套做法：项目状态记在项目目录的 board/ 里（方案、方法、实验、运行、任务卡、决定、文档、简报），用户在控制台上看进度、做决定；包括记录格式、新建和结束项目、长任务、任务会话、共享资源，以及怎么让用户看懂项目。
---

# research-board：研究项目怎么推进、怎么记录

用户和几个 Claude 会话一起推进一个研究项目。项目的状态写在项目目录的 `board/` 里，不在对话里：对话随时可以换新，记录不能丢。研究控制台从 `board/` 生成项目首页（简报 + 地图）、实验流程、运行表和交叉表，用户在上面看进度、回复待办、确认结论。

## 总的做法
1. **讨论 → 方案**：项目会话和用户讨论，写 `plan.md`、`setup.json`（实验设置）、`method/`（方法拆成步骤）。用户说的长期有效的话，当场记成 `decisions/` 里的「候选」，告诉用户「已记下」；用户在控制台采纳后生效。只管这一次的决定写进对应的任务卡，不写成决定。
2. **拆任务**：一件具体的事写成任务卡 `tasks/<id>.md`，由一个任务会话从头做到尾。
3. **执行**：训练、评测、数据处理用 boardrun 启动，自己写 `runs/<运行编号>/`。
4. **记录**：每次实验一份 `attempts/`（最上面是「一页纸」，写 `title` 和 `method`），文档 `docs/`，图解 `figures/`，数据样例 `examples/data/`，论文 `papers.jsonl`。
5. **下结论**：会话起草（`status: 待确认`），用户确认。开新支线、关闭支线、定新版本、给实验下结论，都由用户确认，会话只起草。
6. **回写**：确认的结论改变了方案或方法的某一步，项目会话同步改 `plan.md`、`method/`、`setup.json`，必要时定新版本。
7. **告诉用户**：一批工作告一段落时写简报（`docs/<日期>-brief.md`，`kind: 简报`），控制台把它放在项目首页最上面。

状态以 `board/` 为准。项目根目录的 `NOTES.md` 只放换会话时的交接和环境说明，不另记一份进度。

## 什么时候读哪份
| 要做的事 | 读 |
|---|---|
| 写或改 `board/` 里的任何记录；看控制台怎么显示、记录检查查什么 | `references/board-format.md` |
| 新建项目 | `references/new-project.md` |
| 结束、放弃、暂停、恢复项目 | `references/close-or-pause-project.md` |
| 训练、评测等长任务；占用显卡；换服务器续跑 | `references/long-runs.md` |
| 写任务卡、开工、并行、合并、换新会话 | `references/task-sessions.md` |
| 下载模型、数据集或容器镜像 | `references/shared-resources.md` |
| 给用户讲项目、写简报、画图、请用户写预期 | `references/brief-and-map.md` |
| 改写、搬家或翻译规则和这些文档；查某条规矩由什么保证 | `references/rules.md` |

这些文件在本 skill 目录下（装在 `~/.claude/skills/research-board/` 时就是那里）。

## 配置
个人和实验室的设置都在一个文件里：`~/.config/research-board/config.json`，或者环境变量 `RESEARCH_BOARD_CONFIG` 指向的文件（示例见仓库的 `config.example.json`）。参考文档里说的「项目根目录」（`projects_root`）、「共享目录」（`shared_dir`）、「工具目录」（`tools_dir`）、「服务器」（`servers`）、「控制台所在的机器」（`home_server`）、「任务会话上限」（`max_task_sessions`），都指这个文件里的值。读不到配置时 board 命令会报错并说明缺什么。

## board 命令
用完整路径调用：`<本 skill 目录>/scripts/board`（非交互 ssh 下 PATH 里可能没有它）。
- `board brief [目录]`：项目的事实包（新会话开场要知道的事）。
- `board check [目录] [--file 文件]`：记录检查，有问题时退出码 2。
- `board task-begin <任务 id> --session <本会话 id>`：在项目文件夹里开的这个对话接手一张任务卡。
- `board task-start <项目> <任务 id>`、`board task-stop <项目> <任务 id>`：后台开、停任务会话（自动派活或用户明确要后台开时用）。
- `board reconcile`、`board dispatch`：收尾（关掉进程已不在的运行）和派活（处理勾了「直接处理」的回复），由控制台所在机器上的定时任务每 5 分钟运行。

boardrun 是 Python 记录模块，源码在 `scripts/boardrun/`，项目里按工具目录的副本安装（见 `long-runs.md`）。

## 几条不变的规矩
- `answers.jsonl`、`marks.jsonl` 只由控制台写，会话只读。
- 用户在控制台的回复，只在用户说「处理回复」时处理；勾了「直接处理」的由派活程序处理。会话开始时有未处理的回复，提一句有几条。
- `board/` 是私人记录，不放进团队共享的仓库或站点；在团队仓库上工作时，`board/` 放在克隆目录之外。
- 同一件事失败 3 次就停下，写进待办问用户，不要换着法子一直试。
