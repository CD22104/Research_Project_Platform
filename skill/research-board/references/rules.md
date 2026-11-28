# 规矩清单：每条硬规矩由什么保证

改写、搬家或翻译规则和文档时，对照这份清单，别把规矩弄丢。每条写明：
- **来源**：全局规则（用户的 `~/.claude/CLAUDE.md`）里的原句。
- **文档**：skill 里写着它的地方和原句。
- **代码**、**测试**：用代码把关的，写明哪个函数和哪个测试。

仓库里的 `tests/test_rules.py` 会逐条核对：文件在不在、函数在不在、原句还在不在。比较时不计空白、反引号和加粗记号。只要有一条对不上，测试就失败；那时要么把规矩补回去，要么同时改这份清单。
路径的写法：`tests/`、`platform/` 开头的相对于仓库根目录，其余相对于本 skill 目录。

### R01 新会话先加载 skill
- 来源：全局规则「在有 board/ 的项目里工作、新建/结束/暂停项目、启动长任务时，先加载 skill research-board」
- 文档：`SKILL.md`「在有 board/ 的项目里工作、新建/结束/暂停项目、启动长任务时，先加载 skill research-board。」
- 代码：`scripts/boardtool.py` 的 `hook_brief`（开场钩子注入事实包）
- 测试：`tests/test_board_cli.py` 的 `test_session_start`；`tests/test_skill.py` 的 `test_skill_md`

### R02 状态以 board/ 为准，NOTES.md 只放交接
- 来源：全局规则「项目状态以项目里的 `board/` 为准」
- 文档：`SKILL.md`「状态以 `board/` 为准。项目根目录的 `NOTES.md` 只放换会话时的交接和环境说明」
- 代码：`scripts/boardtool.py` 的 `notes_stop_reason`（改了 board/ 而 NOTES.md 久未更新时，收尾检查拦一次）
- 测试：`tests/test_stop.py` 的 `test_board_edited_and_notes_hours_old`

### R03 收尾写交接
- 来源：全局规则「收尾时更新尝试记录和 `NOTES.md` 的交接」
- 文档：`references/task-sessions.md`「写交接：做了什么、证据在哪（文件、运行编号、提交号）、没做完的、下一步、要你决定的」
- 代码：`scripts/boardtool.py` 的 `task_stop_reason`、`snapshot_handoff`
- 测试：`tests/test_stop.py` 的 `test_card_moved_on_with_an_old_handoff`、`test_work_done_with_an_untouched_card`

### R04 失败的尝试也要记
- 来源：全局规则「失败的尝试也要记，并写明原因」
- 文档：`references/long-runs.md`「失败也要记：运行失败或被中断后，把经过和原因写进它所属实验」
- 代码：`scripts/boardtool.py` 的 `failure_todos`（收尾程序写待办）
- 测试：`tests/test_reconcile.py` 的 `test_one_to_do_per_attempt_for_its_latest_failure`、`test_updating_the_attempt_closes_it`

### R05 下结论由用户确认
- 来源：全局规则「开新支线、关闭支线、定新版本、给一次尝试下结论，都由我确认，你只起草」
- 文档：`SKILL.md`「开新支线、关闭支线、定新版本、给实验下结论，都由用户确认，会话只起草」；`references/board-format.md`「都由用户确认；起草时用」

### R06 长期有效的话记成候选决定
- 来源：全局规则「我说的长期有效的话（要求、偏好、定下的做法），在项目里当场记成候选决定并告诉我」
- 文档：`SKILL.md`「用户说的长期有效的话，当场记成 `decisions/` 里的」

### R07 进度记录是私人的
- 来源：全局规则「我的进度记录是私人的：不要放进团队共享的仓库或站点」
- 文档：`SKILL.md`「`board/` 是私人记录，不放进团队共享的仓库或站点；在团队仓库上工作时，`board/` 放在克隆目录之外」

### R08 占显卡之前先问
- 来源：全局规则「启动训练或任何占用显卡的任务前，先问我，并说明打算用哪几张卡」
- 文档：`references/long-runs.md`「启动训练或任何占用显卡的任务前，先问用户，说明打算用哪台服务器、哪几张卡」

### R09 已批准的卡数范围内可以直接用
- 来源：全局规则「已采纳的决定里批准过的卡数范围内可以直接用，超出先问」
- 文档：`references/long-runs.md`「用户已经批准的范围」「之内可以直接用，超出就写待办问用户」

### R10 给别人留卡
- 来源：全局规则「独占的卡同时不超过该服务器总卡数的一半」
- 文档：`references/long-runs.md`「给别人留卡：独占的卡同时不超过该服务器总卡数的一半」

### R11 长任务在 tmux 里用 boardrun 启动
- 来源：全局规则「训练等长任务在目标服务器的 `tmux` 里用 boardrun 启动，不挂在 ssh 前台」
- 文档：`references/long-runs.md`「一律在目标服务器的 tmux 里启动」「用 boardrun 启动」

### R12 能从 checkpoint 恢复，checkpoint 先写临时文件
- 来源：全局规则「训练脚本必须支持从 checkpoint 恢复；checkpoint 先写临时文件再改名」
- 文档：`references/long-runs.md`「训练脚本必须支持从 checkpoint 恢复」「checkpoint 先写临时文件再改名」

### R13 每次运行绑定一个提交
- 文档：`references/long-runs.md`「代码要先提交：有未提交的代码改动时 boardrun 拒绝启动」
- 代码：`scripts/boardrun/boardrun/__init__.py` 的 `DirtyCode`、`_commit`
- 测试：`tests/test_boardrun.py` 的 `test_dirty_or_uncommitted_code_refuses_to_start`

### R14 核心实验先写预期
- 来源：全局规则「核心实验（会影响方案取舍或结论的尝试）开始前，先请我写下预期」
- 文档：`references/brief-and-map.md`「核心实验（会影响方案取舍或结论的尝试）开始前，先请用户写下预期」；`references/board-format.md`「不要替用户写预期，也不要为了启动去掉」
- 代码：`scripts/boardrun/boardrun/__init__.py` 的 `expectation_problem`、`NoExpectation`；`scripts/projects.py` 的 `core_problems`
- 测试：`tests/test_boardrun.py` 的 `test_no_expectation_is_refused_with_the_steps`、`test_a_skip_needs_a_reason`、`test_record_check_tells_before_the_run`

### R15 任务会话的上限
- 来源：全局规则「所有项目合计同时最多 6 个任务会话」
- 文档：`references/task-sessions.md`「所有项目合计同时最多」
- 代码：`scripts/boardtool.py` 的 `_check_start`、`busy_tasks`
- 测试：`tests/test_tasks.py` 的 `test_5_limit_counts_every_kind_of_session_once`

### R16 同一个项目同时只有一个会话改代码，在 worktree 里改
- 来源：全局规则「同一个项目同时只有一个会话改代码，且在自己的 worktree 里改」
- 文档：`references/task-sessions.md`「同一个项目同时只有一个会话改代码，且在自己的 worktree 里改」
- 代码：`scripts/boardtool.py` 的 `_prepare_worktree`、`CODE_KINDS`
- 测试：`tests/test_tasks.py` 的 `test_1_code_card_gets_a_worktree`、`test_4_one_code_card_per_project`

### R17 任务会话不再开任务会话，一个会话一张卡
- 来源：全局规则「任务会话不再开任务会话」
- 文档：`references/task-sessions.md`「任务会话不再开任务会话，一个会话只做一张卡」
- 代码：`scripts/boardtool.py` 的 `calling_task`
- 测试：`tests/test_tasks.py` 的 `test_a_started_task_session_cannot_start_another`、`test_one_card_per_conversation`

### R18 不守着
- 来源：全局规则「不守着：会话不定时唤醒自己去查进度」
- 文档：`references/task-sessions.md`「不守着：不定时唤醒自己去查进度」；`references/long-runs.md`「不要定时唤醒自己去查进度」
- 另外：各台的权限设置禁用了定时唤醒类工具，由研究服务台维护（不在本仓库）。

### R19 同一件事失败 3 次就停下
- 来源：全局规则「同一件事失败 3 次就停下，写进待办问我」
- 文档：`SKILL.md`「同一件事失败 3 次就停下，写进待办问用户」

### R20 停在权限提问上要让用户看见
- 代码：`scripts/boardtool.py` 的 `hook_waiting`、`list_waiting`；`platform/server.py` 的 `parse_waiting`
- 测试：`tests/test_waiting.py` 的 `test_permission_prompt_is_recorded_without_the_command`；`tests/test_server.py` 的 `test_waiting_part_of_the_poll`

### R21 共享资源先查再下载
- 来源：全局规则「模型、外部数据集、容器镜像先查」
- 文档：`references/shared-resources.md`「先查再下载，下载了就放共享目录、登记好」

### R22 删大件之前先问
- 来源：全局规则「删除任何大件前先问我」
- 文档：`references/shared-resources.md`「删除任何大件前先问用户」；`references/close-or-pause-project.md`「删除任何大件前都先问」

### R23 讲项目先给真实样例
- 来源：全局规则「先给一条真实样例的原文」
- 文档：`references/brief-and-map.md`「真实样例的原文（原始记录、模型看到的输入、输出、怎么计分），再讲抽象结论」

### R24 写记录就检查
- 文档：`references/board-format.md`「控制台会检查什么」
- 代码：`scripts/boardtool.py` 的 `hook_check`
- 测试：`tests/test_board_cli.py` 的 `test_post_edit_check`

### R25 用户的回复和标记只由控制台写
- 文档：`SKILL.md`「`answers.jsonl`、`marks.jsonl` 只由控制台写，会话只读」
- 代码：`platform/server.py` 的 `write_refused`
- 测试：`tests/test_server.py` 的 `test_reply_rejected`

### R26 不在 shell 环境里设置 ANTHROPIC_API_KEY
- 来源：全局规则「特别是不要在 shell 环境里设置 `ANTHROPIC_API_KEY`」
- 代码：`scripts/boardtool.py` 的 `task_start`（起会话前 unset ANTHROPIC_API_KEY）
- 测试：`tests/test_tasks.py` 的 `test_the_started_session_knows_its_card`

### R27 token 的值不出现在任何地方
- 来源：全局规则「不要把 token 的值打印出来，也不要写进对话、日志、代码、项目规则、进度记录或提交信息」
- 代码：`scripts/boardtool.py` 的 `waiting_what`（等批准的记录不存 Bash 命令本身）
- 测试：`tests/test_waiting.py` 的 `test_permission_prompt_is_recorded_without_the_command`
