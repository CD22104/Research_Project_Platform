# 进度记录格式（board/）

各项目 `board/` 目录的正式格式，由「CD的研究控制台」读取展示。这是唯一的正式版本：各项目的 `board/FORMAT.md` 只写一行指向本文件，不再各自维护一份。

- 正式版：a100 上 `~/gpu-board/docs/board-format.md`，随控制台代码一起改。
- 共享盘副本：`/shared/ssd/dchen/_shared/BOARD_FORMAT.md`，由 `scripts/publish-format.sh` 生成，不要手改。

格式还在试点，会调整；调整时先改本文件，再改控制台代码。

## 最少要写什么

新项目只需要两样就能在控制台里出现并可用：

1. `board/project.json`（标题、问题、状态）
2. `board/attempts/` 里的尝试记录

`setup.json`（实验设置：目标、数据集、模型、指标、方法的定义）在建项目时就写，之后每次更新整体方案（`plan.md`）或定新版本时同步更新，不要等到有结果再补。

其余文件都是可选的，有了就显示，没有就不显示，不会报错。建议在项目推进中逐步补上，大致顺序：`tracks/` 和 `versions/` → `runs/` → `examples/` → `flow.json`、`todos.jsonl`。

## 目录

```
board/
  project.json        项目本身：标题、问题、结论、状态、当前版本；可选 display
  setup.json          实验设置：目标、数据集、模型、指标、方法的定义（论文里的「实验设置」）
  plan.md             整体方案
  flow.json           项目流程图
  todos.jsonl         留给用户的待办
  versions/<id>.md    版本：每次定版的方案
  tracks/<id>.md      支线：一个方向上的一串尝试
  attempts/<日期>-<名字>.md   尝试：一次具体的实验
  runs/<运行编号>/run.json    运行：一次训练、评测或数据处理；可选 metrics.jsonl、samples.jsonl、图片
  examples/walkthrough.md     「一个例子」：一条样例从头走到底
  examples/probe.jsonl        固定的观察样例
  docs/*.md                   其他说明文档
```

项目目录本身（`board/` 旁边）固定这样分：

```
<项目>/
  CLAUDE.md  NOTES.md  REPORT.md（可选）  pyproject.toml  uv.lock
  src/  scripts/  configs/        代码
  data/                           自建数据
  results/<运行编号>/              大输出：完整预测、日志、中间文件（控制台不显示，只给路径；boardrun 的 output_dir()）
  ckpt/<运行编号>/                 checkpoint
  board/                          控制台显示的一切：小而整理过的记录
```
放进 `board/` 的文件一分钟内出现在控制台上，不需要别的「上传」步骤；不想给控制台看的东西不要放进 `board/`。

另外，项目根目录（`board/` 旁边）的 `NOTES.md`（进度笔记）和 `REPORT.md`（总报告）会显示在「文档」页。控制台只读这两个固定文件名，不读根目录的其他文件。

控制台忽略 `board/` 里本文件没有提到的内容（例如 `FORMAT.md`、`legacy-site/`）。

## 层级

控制台按这条线组织项目：

```
实验设置（setup.json）
  └ 版本（versions/）：定版时的方案
      └ 支线（tracks/）：从某个版本分出来、或者并入某个版本
          └ 尝试（attempts/）：按时间先后
              ├ 运行（runs/）：数字都在这里
              └ 例子：一条真实样例
```

链接靠各条目的 `id`：尝试的 `track` 指向支线，支线的 `from_version` 和版本的 `merged_tracks` 把支线挂到版本上，尝试的 `runs` 和运行的 `attempt` 互相指向（控制台取两边的并集）。

## Markdown 记录文件（versions、tracks、attempts、plan、docs）

开头是一段字段区：第一行 `---`，若干行 `key: value`，再一行 `---`，之后是正文。值只有三种写法：纯文本、`[a, b]` 形式的列表、`null`。没有嵌套；行内「空格 + `#`」之后是注释。

- **versions**：`id`, `date`, `title`, `parent`（上一个版本或 `null`）, `merged_tracks`（列表）。正文是这个版本的方案：和上一版相比改了什么、为什么。可以有一节「## 结论」：这个版本得出的结论；控制台用当前版本的这一节作为项目的当前结论（没有时才用 `project.json` 的 `conclusion`）。
- **tracks**：`id`, `title`, `status`（进行中 / 暂停 / 并入主线 / 放弃）, `from_version`, `started`, `ended`。正文二级标题：假设、现状、结局和原因。
- **attempts**：`id`, `track`, `date`, `status`, `runs`（列表）, `after`（可选，列表：这次尝试接在哪几次尝试之后，例如在它们的结果上改进；不写就按同一支线里的日期先后），`status` 为「待确认」时可加 `proposed`（建议的结论：成功 / 部分成功 / 失败 / 放弃，控制台标成「建议」）。正文二级标题依次为：假设、预期、改了什么、结果、例子、结论和原因。

attempts 的 `status`：
- 计划中：讨论方案时定下要做、还没开始。写好假设和预期，`date` 写计划日期或留空。控制台在流程图上画成虚线节点。
- 进行中：还在做，结果没出来。
- 成功：假设成立。
- 部分成功：假设在某些条件下成立、某些条件下不成立；「结论和原因」里分开写成立的条件和不成立的条件。
- 失败：假设不成立，或者没有跑成。
- 放弃：决定不再继续这个方向。这是决定，不是结果。
- 待确认：结果已有，结论等用户定。

开新支线、关闭支线、定新版本、给一次尝试下「成功 / 失败 / 放弃」的结论，都由用户确认；起草时用「待确认」。

### 「结果」一节：只写解读，不抄数字

数字只存在运行记录（`run.json` 的 `metrics`）里。控制台会在尝试详情里，按 `display` 和 `setup.json` 的定义，自动画出这次尝试关联运行的指标表和条形图。

所以「结果」一节：
- 先一两句话说发生了什么，例如「新策略条款上准确率明显下降，其余三个测试集持平」。
- 再写运行记录里没有的东西：和预期的对照、观察到的现象、跨运行的比较结论。
- 不要手抄指标表。正文里需要引用某个数时，写成引用：`{{run:<运行编号>/<分组>/<指标>}}`，例如 `{{run:eval-kev_fixed/authz_test_iid/accuracy}}`。控制台显示运行记录里的值并链接到这次运行；运行重跑或改了，正文不用改。
- 指标的定义写在 `setup.json` 的 `metrics` 里，不在每篇尝试里重复。

同样的规则适用于支线和版本的正文：控制台会在支线页和版本页自动给出「各次运行 × 各测试集」的主指标表，正文只写解读。

区分「测出来的」和「定下来的」：
- 测出来的结果（指标、用时、步数、见过的记录数、显存峰值等）只记在 `run.json`，正文不抄。
- 定下来的设计和设置（学习率、轮数、批大小、用哪个基座、各组之间有什么不同）可以写在正文里，读的人需要它们来理解这次尝试；它们不会在运行之后被重算。

### 「例子」一节

一条真实样例，说明这次尝试具体做了什么。放在「结果」之后、「结论和原因」之前。
- 依次写：原始记录的关键字段、模型看到的输入（可以节选）、模型的输出、怎么计分、判对还是判错。
- 用列表或小表格，几行就够，不要原样贴整条 JSON。
- 样例来自 `examples/probe.jsonl` 时，写明它的 id。
- 调试类或失败在半路的尝试没有合适样例时，写一句「没有可展示的样例」和原因。

## project.json

```
{"name", "title", "question", "conclusion", "status", "current_version", "updated", "display"}
```
- `status`：进行中 / 暂停 / 已归档。
- `question`：研究问题；`conclusion`：当前一句话结论。
- `display`（可选）只影响控制台怎么显示，不影响数据：
  - `default_group`（字符串）：运行页默认选中的分组。
  - `group_order`（字符串列表）：分组按钮的顺序，列出的排前面，其余按运行数从多到少排。最多显示 6 个按钮，其余进下拉框。
  - `primary_metrics`（字符串列表）：默认显示的列，列名相对于分组，嵌套的用点号连接。其余列收进「显示全部列」。
  - `ratio`（`{显示列: [分子列, 分母列]}`）：分子分母都有值时，单元格显示成「0.5997 (361/602)」，默认隐藏分子列和分母列。写之前核对该显示列确实等于分子 / 分母。
  - `headline`（`{"group", "metric", "better"}`）：概览页「最好的主指标」卡片取哪个数，`better` 取 `"higher"` 或 `"lower"`。`kind` 为 data 或 smoke 的运行、以及同组里 `n` 小于 `of` 的抽查运行不参与。
  - 不认识的字段、类型不对的值会被忽略，并在页面上提示。

## setup.json

论文里的「实验设置」一节，显示在项目概览页；运行页的分组名和表头的指标名会链接到这里的定义。

```
{"goal": "一两句：想弄清楚什么、做成什么样算回答了",
 "datasets": [{"id", "name", "role", "size", "about", "source"}],
 "models":   [{"id", "name", "version", "about"}],
 "metrics":  [{"id", "name", "better", "about"}],
 "methods":  [{"id", "name", "track", "runs", "about"}]}
```
- `datasets[].id` 必须和 `run.json` 里 `metrics` 的第一层键（分组名）一模一样。`role` 取 `train`、`dev`、`calib`、`test`、`other`。
- `metrics[].id` 必须和分组下的列名一样。`better` 取 `"higher"`、`"lower"` 或 `null`（不分好坏，例如条数）。
- `models[].version` 写到能复现的程度，例如仓库名加 revision。
- `methods[].track`（可选）把方法和支线连起来；`methods[].runs`（可选）列出属于这个方法或对照组的运行编号，控制台在表格和图里用方法名代替运行编号。方法名用论文里会写的说法，12 个字以内。
- 除了 `id` 和 `name`，其他字段都可以不写。`about` 一两句，不写长段落，不贴原始字典。
- 研究问题本身写在 `project.json` 的 `question` 里。

## run.json

**由运行自己写，不手写。** 训练、评测、数据处理脚本用记录模块 `boardrun`（共享盘 `/shared/ssd/dchen/_shared/tools/boardrun`，项目里 `uv add --editable /shared/ssd/dchen/_shared/tools/boardrun`）：

```python
import boardrun
with boardrun.Run(attempt="<尝试 id>", kind="train", config=cfg) as run:
    run.log(step, loss=loss)                  # 曲线 -> metrics.jsonl
    run.result("<分组>", accuracy=acc, n=n)   # 最终指标 -> metrics[分组]
    run.sample(id=..., output=..., score=...) # 样例 -> samples.jsonl
    run.artifact("curve.png")                 # 小图复制到运行目录
    out = run.output_dir()                    # results/<运行编号>/：大输出，控制台不显示
```
正常结束记「完成」，抛异常记「失败」并写明错误，Ctrl-C 或 SIGTERM 记「被中断」。服务器、卡号、进程号、tmux 会话名、commit（有未提交改动时带 `-dirty`）自动填。不是 Python 的命令：`python -m boardrun run --attempt <尝试 id> --kind eval -- <命令>`，按退出码记状态。续跑用 `resumed_from=<上一次的运行编号>`，会得到新编号。

运行记录了 `attempt`，尝试的 `runs` 列表就可以不写（控制台两边都认）。下面是字段说明；没接入模块的旧运行照这个格式手写。

```
{"id", "kind", "attempt", "status", "server", "gpus", "pid", "tmux", "started", "ended", "commit", "resumed_from", "config", "metrics", "artifacts"}
```
- `kind`：train / eval / data / smoke / 其他。`kind` 为 data 的运行在运行页单独成表。
- `status`：运行中 / 完成 / 失败 / 被中断。记录写着运行中、但目标机器上查不到进程时，控制台会标「可能已中断」。失败和被中断时可以有 `error`（一句话的原因）。
- `commit`：代码的提交号；末尾 `-dirty` 表示运行时有未提交的改动。
- `metrics` 按「分组 → 指标」组织，分组通常是测试集，例如 `{"authz_test_iid": {"accuracy": 0.6, "correct": 361, "n": 602}}`。分组名和指标名要在 `setup.json` 里有定义。
- `config`、`artifacts` 是对象，控制台显示成两列的键值表。
- `metrics.jsonl`（可选）：每行一个 JSON，含 `step` 和若干数值；只追加写，最后一行没写完的会被跳过。
- `samples.jsonl`（可选）：样本输出，控制台显示前 20 行。
- 图片（可选）：`png`、`jpg`、`svg`，放在运行目录下，单个不超过 10 MB。

## examples/

- `walkthrough.md`：「一个例子」，一条样例从头走到底。开头放精简版，之后用一行 `---` 和一级标题「完整原文」隔开放完整原文；控制台默认只显示精简版。
- `probe.jsonl`：固定的观察样例，每行一个 JSON。

## flow.json（可选）

项目流程图，控制台画成从左到右的方框加箭头。

```
{"nodes": [{"id", "title", "subtitle", "group", "link"}], "edges": [["起点 id", "终点 id"]], "groups": {"组 id": "图例文字"}}
```
- 最多 8 个节点；`title` 不超过 8 个汉字，`subtitle` 不超过 12 个汉字。
- `group` 区分方框属于哪一类（例如「我们的代码」和「模型」），图例文字写在 `groups` 里。
- `link` 是控制台内部的位置，写法见下文「控制台内部链接」。

## todos.jsonl

留给用户的待办，让用户不用自己记要做什么。每行一个 JSON：

```
{"id", "text", "kind", "where", "created", "done", "by", "link", "context", "options", "recommend", "result"}
```
- `text`：一句话的问题或要做的事。
- `kind`：`"blocking"`（卡着进度）、`"decision"`（等用户做决定）、`"whenever"`（不急）。
- `context`：可选，回复需要知道的背景（Markdown，两三行）：现在的情况、每个选项的后果和代价。让用户在控制台上不用翻别的记录就能回复。
- `options`：可选，回复的选项，字符串列表，每项一句短话，例如 `["现在补（1 张卡约 20 分钟）", "先不补"]`。有选项时控制台显示成按钮。
- `recommend`：可选，建议选哪一项，必须和 `options` 里的某一项完全相同。
- `where`：可选，不在控制台回复时去哪里做，例如「h100 会话」。
- `created`、`done`：ISO 8601 UTC；`done` 为 `null` 表示没做完。
- `result`：标记完成时写一句做了什么，例如「已在 h200 卡 2 启动 r012」「按回复不补，记进尝试 focal」。
- `by`：写这条的会话；`link`：可选，控制台内部链接。
- 同一个 `id` 出现多行时以最后一行为准。标记完成只需追加一行 `done` 不为 `null` 的记录（带上 `result`），不改写旧行。
- 跨项目的待办放在 `/shared/ssd/dchen/_shared/todos.jsonl`，格式相同。

任何会话给用户留下一件要做或要决定的事，都在对应项目的 `todos.jsonl` 里追加一条；能给选项的就给选项和建议。

## answers.jsonl（用户在控制台上的回复）

用户在控制台的待办页或项目概览上回复待办、确认尝试的结论。回复追加到项目的 `board/answers.jsonl`（跨项目待办的回复在 `_shared/answers.jsonl`）。**这个文件只由控制台写，会话只读不改。**

```
{"id", "answer", "text", "at", "by"}
```
- `id`：待办的 `id`；确认尝试结论时是 `a:<尝试 id>`。
- `answer`：选中的选项（待办的某个 `options`；尝试是 成功 / 部分成功 / 失败 / 放弃），没选为 `null`。
- `text`：用户的补充说明，可以为空。
- 同一个 `id` 有多行时以最后一行为准（用户可以在会话处理前改回复）。

**处理回复**：只在用户说「处理回复」时处理，不要自己开始。会话开始时如果有未处理的回复，提一句有几条。处理时：
1. 读 `answers.jsonl`，只看还没处理的：对应待办在 `todos.jsonl` 里 `done` 还是 `null`，或对应尝试还是「待确认」。
2. 逐条先复述你理解的回复和打算怎么做；回复有歧义、或和当前情况冲突时，先问用户。
3. 待办做完后，在 `todos.jsonl` 追加这条的完成行，写 `done` 和 `result`。要很久才能做完的（例如启动了训练），启动后就标完成，`result` 写启动了什么。
4. 尝试的结论：把 `status` 改成用户选的结论，用户的补充写进「结论和原因」；之后如果还留着与这次确认有关的待办，一并标完成。

## 控制台内部链接（flow.json、todos.jsonl 的 link）

写成「分页[/条目][?筛选]」：
- `overview`：项目概览（含实验设置）
- `roadmap`：路线。条目写 `v:<版本 id>`、`t:<支线 id>` 或 `a:<尝试 id>`，例如 `roadmap/a:2026-11-10-kev-fixed`
- `runs`：运行，条目是运行编号，例如 `runs/eval-kev_fixed`
- `examples`：样例
- `docs`：文档，条目 `plan` 是整体方案

旧写法继续可用：`experiments/<尝试 id>` 等于 `roadmap/a:<尝试 id>`，`experiments?track=<支线 id>` 等于 `roadmap/t:<支线 id>`。

## 控制台会检查什么

项目概览页底部的「记录检查」列出写记录的会话可以自己修的问题：
- 尝试的 `track` 指向不存在的支线；`runs` 里有不存在的运行；运行的 `attempt` 指向不存在的尝试。
- 支线没有挂到任何版本上（没有有效的 `from_version`，也不在任何版本的 `merged_tracks` 里）；`merged_tracks` 里有不存在的支线。
- 运行里出现的分组或指标没有在 `setup.json` 里定义；`setup.json` 的方法引用了不存在的支线或运行。
- 尝试没有字段区，或者没有「例子」一节。
- `display.headline` 指向的分组和指标在运行记录里找不到。
- 读不了的文件（JSON 写坏、待办行缺 `id` 或 `text` 等）。

## 正文怎么写

只管表达形式，不改变事实。
1. 每个二级标题下先用一两句话说结论性的事实，再放表格和细节。
2. 不把程序里的原始字典或 JSON 贴进正文；写成人能读的话或小表格。
3. 表格不超过 5 列，超过就拆开；数字列写「分子/分母」，可带比率。
4. 并列的几件事用列表，一条一件；一段话不超过三四行。
5. 术语在 `setup.json` 里定义一次，正文保持同一种叫法。
6. 用户确认过的结论保留确认标注（谁、哪天、经谁转达）；回填的记录在「预期」里写明是回填。
7. 可用的 Markdown：标题、表格、列表、行内代码、加粗、代码块、引用、分隔线。

## 通用约定

- 时间用 ISO 8601 UTC。
- 除 `metrics.jsonl` 和 `todos.jsonl` 这类只追加的文件外，先写临时文件再改名（共享盘是 soft 挂载）。
- 只写有出处的事实；查不到的字段填 `null`。
- `board/` 是私人记录，不放进团队共享的仓库或站点。
