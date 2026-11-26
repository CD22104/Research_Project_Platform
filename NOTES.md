# 控制台开发：交接

## 拆成 skill 和 platform（2026-11-26，用户选了方案 1，代码部分做完，等切换）
- 提交 5c05927…00a8a81，测试 155 个左右全过（偶发的图解探测失败照旧）。设计见下面「方案」一节；实际做法和方案一致，几处细化：
  - `servers` 可以是空列表（只看记录、不轮询显卡），`home_server` 这时为空。
  - 导出用 Python（`scripts/export.py`），拷的是被 git 跟踪的文件的当前内容；扫描的词来自本机配置、项目根目录下的项目文件夹名、git 作者，另可选 `~/.config/research-board/private-words.txt`。
  - `board` 包装脚本用 `python3 -B`，装好的 skill 里不会多出 `__pycache__`。
  - `publish-board.sh` 停用：共享盘上那份 boardtool 要新配置，等 h100 的钩子改用装好的 skill 后就不需要了。
  - 使用说明（`platform/docs/guide.md`）和设计思路页一样算私人内容，导出时替换。
- a100 的 `~/.config/research-board/config.json` 研究服务台已放好（sha256 9c96a588…），控制台、crontab、钩子都已经在用它（经根目录的转接文件）。
- skill 当前版本：MANIFEST.sha256 的 sha256 = 1d96a6531f02c18fe995983ee222369dbe3fc19c756cba60883c02e2a3683a84（VERSION `2026-11-26 1d96a6531f02`）。之后改 skill 要重跑 make-manifest，版本号会变。
- **待切换**（清单已发研究服务台）：用户看 SKILL.md 和 references（研究服务台和新规则一起给用户对照）→ 同意后研究服务台装 skill 到 6 台、其余 5 台放配置、改 crontab 和 `~/bin/board` → 用户改两台的 `settings.json` 钩子 → 我把 tmux gpuboard 改成直接跑 `platform/server.py`、删转接文件 → 用户运行推送脚本。
- 偶发失败：图解探测（`fig_probes` 为空）到现在约 30 遍全套出现 4 次，越来越常见，切换完后查。

## 方案（用户已选方案 1）：整套做法做成 skill，仓库分 platform/ 和 skill/（2026-11-25，研究服务台转来）

需求原文：`~/desk/requests/2026-11-25-skill-and-repo.md`。用户已定：仓库选 A（`~/gpu-board` 里放 platform/ 和 skill/）；skill 通用、不含个人信息；个人设置集中在一个配置文件；全局规则只留原则和红线。下面是要用户选的方案，选定后再动手。

### 查到的事实
- 全局规则约 5,400 字（不算空白）。按步骤做的几节（项目进度记录 1,039、会话分工 619、项目与会话 333、新建项目 662、长任务 366、结束或暂停 393）约 3,400 字，占六成多，都可以搬进 skill。
- 仓库里的个人信息：`servers.json`（6 台主机全名）；`CLAUDE.md`、`NOTES.md`（项目名、路径、经历）；`design.js`（设计思路页，含真实项目和统计）；代码里写死的 `/shared/ssd/dchen` 和 `/home/dchen/bin/board`（boardtool.py 5 处、server.py、app.js、work.js、boardrun 各 1–2 处）；`docs/guide.md` 里的主机名；测试里的 xtra 主机名；页面标题「CD的研究控制台」。git 历史 90 多个提交里都有。
- 机器上是 Python 3.10，标准库读不了 TOML，所以配置用 JSON。
- monitor-bench 的 `pyproject.toml` 已经按路径依赖共享盘上发布的 boardrun（`_shared/tools/boardrun`），这个发布位置要保留。

### 三种方案（差别在：工具放哪、skill 拆几个）
- **方案 1：一个 skill，自带工具（推荐）。** `skill/research-board/` 里有 `SKILL.md`、`references/`（流程和格式）、`scripts/`（board 命令和 boardrun 的源码）。platform/ 只放控制台，复用 skill 里的 `projects.py`（一份代码，不复制）。各台装好 skill 后，钩子、`~/bin/board`、收尾和派活都指向 skill 里的 board；boardrun 照旧发布到配置里的共享工具目录，项目的依赖路径不变。
  - 好：别人装一个 skill 就能用（流程 + 格式 + 命令），不必装控制台；工具和文档同一处、同一个版本；h100 的钩子不再依赖共享盘上的副本。
  - 代价：platform 依赖 skill 的目录结构；skill 里有代码，分发时要连代码一起同步。
- **方案 2：skill 只放流程和格式，工具留在 platform/。** skill 里只有 `SKILL.md` 和 `references/`；board 命令、boardrun 留在 platform/，照现在的方式发布到共享盘；`SKILL.md` 写「board 命令在配置的 tools_dir」。
  - 好：改动最小，路径基本不变，控制台和工具一起测。
  - 代价：别人只装 skill 用不了，还得装 platform；skill 和工具可能不同步。
- **方案 3：两个 skill。** `research-workflow`（只有原则和流程，谁都能用，不依赖任何工具）+ `research-board`（board 格式和工具，配控制台用）。
  - 好：不写 board 记录的人也能用流程部分；格式文档（580 行，最大的一块）只在写记录时才加载。
  - 代价：两个 skill 的边界要维护；触发条件更难写准，漏加载一个就缺东西。方案 1 把格式放进 `references/`、用到时才读，也能拿到大部分好处。

### 不论选哪种都一样的设计
- **skill 名字**：`research-board`（方案 3 另有 `research-workflow`）。
- **加载**（研究服务台补充）：只有 a100 和 h100 有 SessionStart 钩子，所以瘦身后的全局规则里留一句「在有 board/ 的项目里工作、新建/结束/暂停项目、启动长任务时，先加载 skill research-board」；`SKILL.md` 的触发描述用同一句话，两边一致。
- **分发和核对**（研究服务台补充）：skill 搬走了规则的大部分，改 skill 等于改规则，所以走规则的老流程：各台装本地副本，研究服务台给用户看改动，用户同意后运行脚本分发、逐台核对；不用指向共享盘的符号链接（那样一改就在所有机器上生效，绕过了用户确认）。skill 目录里放 `MANIFEST.sha256`（每个文件一行，`sha256sum` 格式，按路径排序，由 `scripts/make-manifest` 生成）和 `VERSION`（日期 + 生成时的 commit）。`MANIFEST.sha256` 自己的 sha256 就是这份 skill 的版本号：distribute.sh 先核对目标机器上的这个值等于预期的旧值，分发后在目标机器上跑 `sha256sum -c MANIFEST.sha256`（每个文件都对）并核对新值。boardrun 在共享盘的副本是项目代码依赖的库，照旧由控制台开发测试通过后发布，不属于这条流程。
- **SKILL.md**（约 1–2 屏）：什么时候加载（在有 `board/` 的项目里工作、新建 / 结束 / 暂停项目、启动长任务、写或改记录、开工做任务卡）；总的做法（讨论 → 方案 → 任务卡 → 运行 → 记录 → 结论由用户确认 → 回写方案 → 简报）；遇到哪件事读哪份 reference；从配置文件读路径和服务器。
- **references/**（按需读）：`board-format.md`（现在的格式文档，去掉个人路径）、`new-project.md`、`close-or-pause-project.md`、`long-runs.md`（boardrun、tmux、checkpoint、换服务器）、`task-sessions.md`（任务卡、开工、并行、worktree、合并）、`shared-resources.md`（模型和数据集先查再下、SOURCE.json）、`brief-and-map.md`（简报、一页纸、怎么让用户看懂）。
- **事实包**：提醒里加一句「做法和格式见 skill research-board」，保证在项目里开的会话会加载它，不只靠模型自己判断。
- **配置文件**：`~/.config/research-board/config.json`（各台本地一份，由研究服务台随规则分发；也可以用环境变量 `RESEARCH_BOARD_CONFIG` 指到别处）。仓库里只放 `config.example.json`。项目：
  ```json
  {"projects_root": "/shared/<you>/projects",
   "shared_dir": "/shared/<you>/projects/_shared",
   "tools_dir": "/shared/<you>/projects/_shared/tools",
   "models_index": "_shared/models/MODELS.md", "datasets_index": "_shared/datasets/DATASETS.md",
   "servers": [{"name": "gpu1", "host": "gpu1.example.edu", "gpus": 8}],
   "home_server": "gpu1",
   "claude_bin": "~/.local/bin/claude",
   "secrets_dir": "~/.config/secrets",
   "max_task_sessions": 6,
   "console": {"name": "研究控制台", "socket": "~/research-console/run/board.sock", "port": 28765,
               "state_dir": "~/research-console/state", "interval_seconds": 30}}
  ```
  控制台、board 命令、boardrun 都读这一份；读不到时报错并指向示例，不再悄悄用某个人的路径。
- **全局规则怎么拆**（研究服务台据此起草；个人环境事实留在规则里，因为规则本来就是私人的，skill 要通用）：

  | 节 | 处理 |
  |---|---|
  | 协作方式（语言、核心代码、一次性代码、不确定时） | 留 |
  | 共享 GPU 服务器 | 留（红线：占卡先问、独占不超过一半） |
  | 服务器环境 | 拆：留原则（项目放共享盘、环境本地重建、checkpoint 可恢复且原子写、要推远程）和本机事实（哪几台、CUDA 上限、非交互 ssh 没有 PATH）；做法搬 skill |
  | 共享盘上的可复用资源 | 拆：留「先查再下、放共享目录、不放 home、大件删前问」；SOURCE.json、索引怎么写搬 skill |
  | 项目进度记录 | 搬 skill；留三句：状态以 board/ 为准；开支线、定版本、下结论由用户确认；board/ 私人，不进团队仓库 |
  | 会话分工 | 拆：留原则（三层、最多 6 个、不守着、失败 3 次停下问、同一项目一个会话改代码）；任务卡、开工、worktree、合并的做法搬 skill |
  | 项目与会话（常驻入口） | 搬 skill；主机名进配置 |
  | 新建项目 | 搬 skill（留一句：信任提问由用户答，不要绕过） |
  | 长任务的启动与记录 | 搬 skill；留一句：训练在 tmux 里、用 boardrun 启动 |
  | 结束或暂停项目 | 搬 skill |
  | 帮我理解项目 | 留（这是用户的偏好：先给样例、核心实验先写预期） |
  | 密钥和 token | 留（红线） |

  估计规则剩约 2,000 字，每个会话少带约 3,400 字。
- **platform/ 里的个人信息**：
  - `servers.json` → 移出仓库，改读配置文件；仓库里留 `config.example.json`。
  - 代码里写死的路径 → 一律读配置；页面上显示的路径和名字由服务端从配置里给。
  - `CLAUDE.md`、`NOTES.md`（控制台开发的交接）→ 留在私有仓库里，但移到 `private/`，导出时排除。
  - `design.js`（设计思路页）→ 页面框架留下，正文（真实项目、统计）改成从 `private/` 读，导出时换成一份通用的说明。
  - 测试里的主机名、项目名 → 改成 gpu1、demo 这类通用名字。
- **git 历史**：现在的仓库保持私有，照常用。要分享时用一个导出脚本（`scripts/export.sh`），把 platform/ 和 skill/ 拷成一个全新的仓库、只有一个提交，导出前扫一遍个人信息（主机名、路径、邮箱、项目名），扫到就停。不建议改写现有历史：会破坏已有的克隆，也容易漏。
- **目录搬动**：运行时文件（`run/`、`state/`、`server.log`）留在 `~/gpu-board` 根目录（gitignore），Mac 的隧道和日志位置不变。搬完后交给研究服务台的清单：crontab 三行、`~/bin/board`、`~/.claude/settings.json` 两个钩子、tmux gpuboard 的启动命令、发布脚本的源路径（具体路径取决于选哪个方案，选定后列出）。

## 交接（2026-11-24，从旧服务台会话拆出来）

### 今天做完的（都已提交，测试全过）
记录格式第 2 版（一页纸、任务卡、决定、纪要、术语、`{n}` 指标）→ 运行心跳和收尾 → 事实包和两个钩子 → 控制台重构（今天、项目头、研究 / 工作两组页签、会话页、时间线、记录维护）→ 任务会话和派活 → 使用说明 → 待办按处理进度分组 → 设计思路页（可导出单个 HTML）→ 收尾只关带心跳的运行 → 暂停、归档的项目不进「今天」。设计思路页（`#/design`）写了整套做法和取舍。

### 论文清单（2026-11-24 做完，提交 47e3a21…a90d231，格式和 boardtool 已发布，控制台已重启）
- 用户要的：方案讨论时每个项目有论文列表，显示在控制台上，读完了能打勾。用户选了方案 A（项目里一个 `board/papers.jsonl`，由会话追加），另两个放弃了：B 论文清单写成一篇文档、用勾选列表（条目靠解析，勾会错位，没法挂到步骤上）；C 全组共用一个论文库（控制台要写共享目录，几个会话会改同一个文件）。
- 数据：`papers.jsonl` 一篇一行，必写 id / title / url / why；同一个 id 以最后一行为准、整行替换；`"dropped": true` 去掉。`checked` 只在会话亲自打开链接、核对过标题后才写，没写的显示「未核实」（不算检查问题）。
- 读过：控制台在 `marks.jsonl` 追加 `{"paper", "mark": "读过" 或 null}`，和文档的 file 行分开读（`read_paper_marks`；`_Board.cached` 加了 `tag` 参数，同一个文件可以读两遍）。写接口 `POST /api/paper-mark`，只接受清单上的论文。
- 跨项目：`paper_key` 按 arXiv 号 / DOI / 去掉协议的链接认同一篇；`papers_read_elsewhere` 只提示「在 X 勾过」，不算读过。
- 页面：研究组「论文」页签（按 about 分组，组内未读、必读在前，`papers/<id>` 定位到那一行）；版本、支线、尝试、方法步骤、任务卡、决定的详情里有「这一步的论文」；项目头一行计数；实验页「项目概况」多一张统计卡。
- 顺手修了原来的小问题：文档的 `about` 现在也认 `k:` / `d:`（任务卡和决定下面能挂文档了）；文档页和论文页共用 `groupByPlace` 分组；`ensureDetail` 只有勾变了时也会重画。
- 事实包加了「论文」一节（读过哪些、必读未读哪些，最先被截短）和一句提醒；记录检查钩子现在也报 `papers.jsonl · <id>` 这类按行的问题，提示里带上 id。
- **还没做**：全局规则加一句，原文已交给用户，由用户决定是否转给研究服务台去分发（见下面「要接着做的」）。在那之前，事实包的提醒已经告诉各项目会话要记论文。
- 已知：项目页签多了一个，1200 px 宽的窗口里页签一行放不下，出现横向滚动条（原来就允许滚动）。

### 项目首页（简报 + 地图）、实验标题和方法、资料页签、数据集样例（2026-11-25，提交 c65236f…7d1c82f，格式和 boardtool 已发布，控制台已重启）
- 起因：用户离开一晚（monitor-bench 夜里 38 次提交、5 张卡、100 多个运行）后不知道从哪看、实验名看不懂。用户选了 C：会话写的简报 + 控制台从记录生成的地图。
- 首页（`home.js`，新文件，server 的 STATIC 里加了）：最上面最新的简报（docs 里 kind 简报，按 date）→ 项目地图（研究问题、方法骨架带实验数和「可能过时」、按支线分块的实验卡：标题/想回答/所以/改的是哪一步/数据/文档/运行链接，「新」按上次来的时间）→ 等你（卡着的 → 待确认结论 → 决定 → 不急）→ 正在进行。原来的概况页（变化流水、最近的一页纸）拿掉了。
- 记录：实验必须有 `title`（≤20 字）和 `method`（项目有 method/ 时），格式里以前没有这两项，28 个实验一个标题都没有。简报四节、≤1500 字。正文里 `{{a:|t:|v:|m:|k:|d:<id>}}` 显示成记录名和链接。
- 「可能过时」（`methodStale`）用文件修改时间近似：步骤关联的实验已下结论、且实验文件比步骤文件新。
- 页签：研究组 首页/方法/实验/运行/文档/资料；资料下三页 实验设置/数据与样例/论文，旧网址照用（`navTab`）。
- 数据集样例：`board/examples/data/<数据集 id>.<jsonl|csv|tsv|txt>`，≤10 条、≤1 MB，原样；`samples_from`、`samples_note`；缺样例会被记录检查列出。
- monitor-bench 已补齐标题、method、样例、测试集拆分（authgate_s200 / s80 / block350 / block175 / smoke{n}）、主指标（authgate 配对全对率）；第一篇简报已请它写。jev-authz（48 条检查）、jev-fraud（16 条）用户说先不管。
- **接下来（用户同意的顺序，P1）**：地图上直接确认结论（同意 / 有意见，可一次确认多个）；文档里能链接控制台视图（例如 `{{view:runs?att=…&view=pivot}}`），会话不再手写结果表。都要先给用户设计决定。
- 训练进度：用户说先不管。GitHub 推送：a100 上没有不依赖 VS Code 的凭据，要研究服务台配；在那之前提交都只在本地。

### 运行表和交叉表（2026-11-25，提交 74e9549…，格式已发布）
- 用户要的：运行页看结果不方便，想要一张能按列排序的表，再加一个更直接的显示方式（看 monitor-bench 时提的）。选了 A（通用大表）+ B（交叉表）都做，放弃了只修柱状图（C）。
- 起因（真实数据）：monitor-bench 没设主指标，柱状图画的是「条数」，104 根都是 700，把表格挤到两三屏以下；条件、策略、模型只在 config 里，表里看不到；项目会话只好手写 5 篇「…-tables.md」。
- `common.js` 纯函数：`settingColumns`（哪些 config 键做列）、`sortRows`、`pivot`、`scale`，单元测试在 `frontend_unit.html`。
- **挑设置列的规则改过一次**：先按「取值种类最少的 6 个」，在 monitor-bench 上挑出了 vllm_url、vllm_gpus（只有 Qwen 的运行才有，有/无正好两种）。现在是：八成以上运行都有、取值不同、大多数值不长、4 个以上运行时不是每个都不同（流水账），按记录顺序取前 6 个；`display.settings` 可以指定。这个修正已告诉用户。
- 页面状态全在网址里（`runsQuery` / `runsLink`）：view（'' / pivot / data）、g（测试集，`~` 表示顶层数字那一组）、sort/desc、where（`k:v;k:v`，各段 URI 编码）、att、track、st、all、rows/cols/metric。原来的 `ui.runView` 去掉了。
- 交叉表：行默认取值最多的设置，列取接下来两个（乘积不超过 12），格子默认主指标；同格多运行取平均（抽查不算，除非全是抽查），按方向着色，点一格跳回筛好的表格。
- 测试：`tests/test_frontend.py` 的 `RunsTable` 用 `make_grid` 在测试副本里建一个 10 个运行的 grid 项目（`start_server(..., prepare=)`），不放进 fixtures，免得影响别的测试的项目列表。
- 训练进度（boardrun 报进度、控制台显示）的方案已给用户（推荐 B：`run.progress`，写进心跳），用户还没选。

### Markdown 嵌套列表、boardrun 改动检查（2026-11-24 晚，提交 61f6026、d0eca96，boardrun 和格式已发布）
- 渲染器的列表原来是平的：所有列表行进一个列表、有序列表总从 1 编号。现在 `common.js` 的 `list()` 按缩进分层：一项拥有它下面缩进更深的行（子列表、续行，再交给 `md()`），嵌套列表里紧跟着的缩进行算续行；有序列表保留起始数字（`<ol start>`）。两位数编号不再伸出左边，任务卡正文加了内边距。起因是用户截图：monitor-bench 的 label-spec-pilot 卡。
- boardrun 检查未提交改动时只看代码，排除 `board`、`NOTES.md`、`REPORT.md`、`CLAUDE.md`（`NOT_CODE`，git pathspec）。

### 会话页（2026-11-24，提交 34aa477）
- 用户要的：已结束的会话不再标「上下文大，建议换新」，整行变灰（`tr.ended`，半透明）；开着的照旧标黄。

### 图解（2026-11-24 做完，提交 b08ee3b…0201395，格式和 boardtool 已发布，控制台已重启）
- 后端：`projects.read_figure_head` 只读 `board/figures/*.html` 开头 8 KB（到 `<body>` 或 `<svg>` 为止）取 title / about / question，扫描结果里单独一个 `figures` 列表；指向 `figures/` 之外的链接不列（和 `/files/` 一致）。检查：开头 8 KB 没有 title、about 指不到、超过 10 MB、链接指向别处。
- 服务：`/files/` 的 `.html` 只放行 `figures/` 下一层，用 `FIGURE_HEADERS` 的 CSP；`legacy-site/` 等旧 HTML 照旧 404。`post_mark` 接受 figures。
- 页面：文档页的一类「图解」，id 是文件名；详情是 `sandbox="allow-scripts"` 的 iframe（固定高度约一屏）加「在新窗口打开」。`setMain` 在每分钟重画时保留同一张图（同文件、同 mtime）的 iframe，图里的操作不丢。
- 测试夹具 `demo/board/figures/lr-warmup.html` 里有一段探测脚本，点击测试靠它验证读不到父页面、localStorage、API，且重画后只加载一次。

### 任务会话改在项目文件夹里开（2026-11-24 做完，提交 42c4650、4fbdd47，格式和 boardtool 已发布）
- 起因：App 按常驻入口分文件夹，`task-start` 开的会话落在「No folder」。用户要一个项目的对话都在项目文件夹里。
- 现在：项目会话写好卡，请用户在项目文件夹里新开对话说「做 <任务 id>」。事实包开头有「本会话 id」（SessionStart 钩子的 session_id），提醒里写着先运行 `board task-begin <任务 id> --session <id>`。
- `task_begin`：和 `task_start` 共用 `_check_start`（卡的状态、一张卡一个对话、最多 4 个、一个项目一张改代码的卡）和 `_prepare_worktree`，打印同一份 `task_prompt`（多一句：对话开在主目录，代码要在 worktree 里改）。不改任务卡。
- 数上限的 `busy_tasks`：tmux 的 task-*/reply-*，加上 `claude agents --json` 里活着、且被一张「进行中」的卡的 sessions（id 或名字）点名的对话；同一张卡只算一次。`task_start`、`dispatch` 也用它。卡已完成的闲置对话不算。
- 控制台任务卡详情有「开工」一行和复制「做 <任务 id>」的按钮。格式、使用说明、设计思路页都改了；全局规则「会话分工」那句的新原文已交给研究服务台（desk-bd），由它给用户看、再分发。
- `task-start` 保留：自动派活、或用户明确要后台开时用。

### 入口合并（2026-11-24，用户确认）
- 只保留 desk 入口：控制台开发的对话也在 App 的 desk 文件夹里开，开场先读 ~/gpu-board/CLAUDE.md 和本文件，代码只在 ~/gpu-board 里改。`rc-gpu-board` 由研究服务台停掉、并从启动名单和规则里去掉。

### 要接着做的
1. **训练进度**（用户 2026-11-24 同意方向：任务卡不做进度清单，只在训练时做）：boardrun 记总步数和每次 log 的时间，控制台显示步数、百分比、预计剩余。前提是各项目的训练改用 boardrun 启动：2026-11-24 查过，三个项目 128 个运行里带心跳的 0 个、写了曲线的 2 个（jev-authz）；用外部框架的（jev-fraud）要在回调里调 `run.log`，由项目会话改、研究服务台协调。控制台和 boardrun 这部分是核心代码，先给用户方案。
2. 全局规则加论文清单那一句：用户 2026-11-24 同意加，原文已发给研究服务台（desk-bd），由它在自己的对话里给用户看改动、分发。monitor-bench 的 54 篇论文已由项目会话搬进清单（5922454）。
3. 以后可以做的（设计思路页第 18 节）：会话页看到其他服务器的会话、手机上看进度、自动回收闲置会话、记录维护的定时轮次、模型分级、迁移辅助。都要先和用户确认。

### 踩过的坑
- 全套测试的偶发失败（2026-11-24 抓到了，11-24 到 11-25 跑了十几遍全套出现 2 次）：`test_frontend.Interactions.test_clicks` 里 `fig_probes` 为空，也就是图解框里的探测脚本偶尔没把消息发回来（`fig_kept` 等其他项都正常）。单独重跑都能过；大约十几次出现一次。原因没查。跑全套时把输出存到文件（不要只看 `| tail`），再遇到可以从这里查：是框没加载完、还是消息被丢了。
- 测试串 `| tail` 后面接 `&& git commit` 时，要先 `set -o pipefail`，否则失败也会提交（今天发生过两次，后来补了提交）。
- h100、h200 拒绝频繁 ssh：收尾程序每台每轮只连一次。
- 手写的运行记录「进程不在」不等于被中断：今天收尾程序误标了 jev-fraud 的 4 个运行，其中 3 个其实跑完了，后来已改成只关带心跳的运行。
