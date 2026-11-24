// 设计思路: how the research projects are run with Claude agents, written for discussing with others.
// A page of the console (#/design) with live numbers, plus an export to one self-contained HTML file.
// Loaded before app.js; uses app.js names (S, B, esc, plink, ensureSessions) only inside functions.

const DZ_TOC = [
  ['why', '为什么要做'], ['principles', '设计原则'], ['objects', '四件事：项目、任务、会话、运行'], ['views', '两个视图：研究和工作'],
  ['arch', '整体结构：服务器和程序'], ['roles', 'Agent 分工'], ['life', '一个项目从头到尾'], ['memory', '不忘事：事实包和决定'],
  ['trust', '状态可信：心跳和收尾'], ['loop', '回复和决定的闭环'], ['read', '让人看得懂：一页纸、时间线、术语'],
  ['tokens', 'Token 花在哪、怎么省'], ['parallel', '并行和合并'], ['borrow', '借鉴了什么、没照搬什么'], ['rejected', '考虑过、没采用的'],
  ['parts', '组成部分和代码'], ['now', '现在的状态'], ['next', '还没解决的和下一步'], ['discuss', '想一起讨论的问题']];

function dzLive() {
  const pj = S.projects, ss = S.sessions;
  if (!pj) return '<div class="small">正在读取…</div>';
  const ps = pj.projects.filter(p => p.status !== '已归档');
  const sum = f => ps.reduce((n, p) => n + f(p), 0);
  const cells = [
    ['进行中的项目', ps.length, ps.map(p => esc(p.title)).join('、')],
    ['进行中的任务卡', sum(p => (p.tasks_open || []).length), '各项目 board/tasks/'],
    ['等你采纳的决定', sum(p => (p.decisions_candidate || []).filter(x => !x.answer).length), '会话只能写「候选」'],
    ['已采纳的决定', sum(p => (p.decisions_adopted || []).length), '写进每个新会话的事实包'],
    ['等你回复的待办', sum(p => (p.todos || []).filter(t => !t.answer).length + (p.pending || []).filter(a => !a.answer).length), '含等你确认的结论'],
    ['正在跑的运行', sum(p => (p.active_runs || []).length), '心跳 + 显卡轮询判断'],
    ['开着的 Claude 会话', ss ? ss.sessions.filter(s => s.live).length : '—', 'a100 上，claude agents'],
    ['今天读入的 token', ss ? kTokens(ss.sessions.reduce((n, s) => n + (s.tokens_today || 0), 0)) : '—', '含每次调用重读的上下文']];
  return `<div class="dz-stats">${cells.map(([k, v, s]) => `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${s}</div></div>`).join('')}</div>
    <div class="small">这一节的数字是打开页面时从控制台读的实时数据（${B.when(new Date(pj.now * 1000).toISOString())}）。</div>`;
}

// ------------------------------------------------------------ diagrams (inline SVG, colours from the page's CSS variables)
function dzArch() {
  const box = (x, y, w, h, title, lines, cls = '') => `<g class="dg ${cls}"><rect x="${x}" y="${y}" width="${w}" height="${h}" rx="10"/>` +
    `<text class="dt" x="${x + 12}" y="${y + 22}">${title}</text>` + lines.map((l, i) => `<text class="dl" x="${x + 12}" y="${y + 44 + i * 19}">${l}</text>`).join('') + '</g>';
  const arrow = (d, label, lx, ly, anchor) => `<path class="da" d="${d}" marker-end="url(#dz-arr)"/>` +
    (label ? `<text class="dal" x="${lx}" y="${ly}"${anchor ? ` text-anchor="${anchor}"` : ''}>${label}</text>` : '');
  return `<svg class="dz-svg" viewBox="0 0 1000 580" role="img" aria-label="整体结构图">
    <defs><marker id="dz-arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" class="dam"/></marker></defs>
    ${box(20, 70, 270, 110, '你', ['Mac 浏览器：控制台（ssh 隧道）', '手机 / claude.ai：和会话说话', '只做决定：回复、采纳、确认、批卡'], 'you')}
    <g class="dg zone"><rect x="330" y="50" width="650" height="310" rx="14"/></g>
    <text class="dgt" x="346" y="350">xtraa100：所有会话和管理程序（只用 CPU）</text>
    ${box(350, 70, 290, 125, '控制台 server.py', ['读各项目 board/，每分钟扫一遍', '轮询 6 台服务器的显卡', '读会话的元数据（不读对话）', '只写：回复、文档标记、显示名'], 'core')}
    ${box(660, 70, 300, 125, 'Claude 会话', ['项目会话：rc-<项目>（常驻入口）', '任务会话：task-<项目>-<任务>', '研究服务台：服务器和账号', '开场读事实包，改记录当场检查'], 'agent')}
    ${box(350, 205, 290, 125, '例行程序（不花 token）', ['收尾 reconcile：每 5 分钟', '　停掉的运行标「被中断」', '派活 dispatch：每 5 分钟', '　勾了「直接处理」的回复起会话'], 'prog')}
    ${box(660, 205, 300, 125, '钩子（Claude Code hooks）', ['SessionStart：注入事实包', '　开会话 / 恢复 / 压缩之后', 'PostToolUse：改 board/ 文件后', '　当场跑记录检查，有错报给会话'], 'prog')}
    ${box(330, 400, 310, 160, '共享盘 /shared/ssd/dchen', ['<项目>/board/：记录（控制台只读这里）', '<项目>/ 代码、数据、checkpoint', '_shared/：格式说明、boardrun、工具', '各台路径一致：在哪台跑都一样'], 'disk')}
    ${box(700, 400, 280, 160, 'GPU 服务器', ['h100、h200、6k01/03/04、a100', 'tmux 里用 boardrun 启动训练', '每分钟写心跳，结束写退出码', '控制台重启不影响训练'], 'gpu')}
    ${arrow('M290 125 L346 125', '看、回复', 318, 117, 'middle')}
    ${arrow('M200 70 C 260 10, 760 6, 805 66', '和会话说话（App / 网页）', 500, 22, 'middle')}
    ${arrow('M495 360 L495 396', '读写 board/', 503, 384)}
    ${arrow('M840 360 L840 396', '启动训练（ssh + tmux）', 850, 384)}
    ${arrow('M700 480 L644 480', '心跳、记录', 672, 470, 'middle')}
  </svg>`;
}
function dzViews() {
  const chip = (x, y, w, t, cls) => `<g class="dg ${cls}"><rect x="${x}" y="${y}" width="${w}" height="40" rx="8"/><text class="dt c" x="${x + w / 2}" y="${y + 25}">${t}</text></g>`;
  const ar = (x1, y1, x2, y2) => `<path class="da" d="M${x1} ${y1} L${x2} ${y2}" marker-end="url(#dz-arr2)"/>`;
  return `<svg class="dz-svg" viewBox="0 0 1000 290" role="img" aria-label="研究视图和工作视图">
    <defs><marker id="dz-arr2" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" class="dam"/></marker></defs>
    <text class="dgt" x="20" y="30">研究视图：研究本身（论文里会写的东西）</text>
    ${chip(20, 45, 130, '方法 method/', 'core')}${chip(190, 45, 130, '版本 versions/', 'core')}${chip(360, 45, 130, '支线 tracks/', 'core')}
    ${chip(530, 45, 150, '尝试 attempts/', 'core')}${chip(720, 45, 130, '运行 runs/', 'core')}${chip(870, 45, 110, '文档 docs/', 'core')}
    ${ar(150, 65, 186, 65)}${ar(320, 65, 356, 65)}${ar(490, 65, 526, 65)}${ar(680, 65, 716, 65)}
    <text class="dl" x="572" y="106">每个尝试最上面是「一页纸」</text>
    <path class="da dash" d="M300 188 C 300 130, 548 140, 548 90" marker-end="url(#dz-arr2)"/>
    <text class="dal" x="30" y="128">任务挂在研究的某一步上（about）</text>
    <path class="da dash" d="M505 188 C 505 150, 772 150, 772 90" marker-end="url(#dz-arr2)"/>
    <text class="dal" x="782" y="140">执行任务产生运行（task）</text>
    <text class="dgt" x="20" y="178">工作视图：谁在做什么（agent 的工作）</text>
    ${chip(20, 190, 150, '决定 decisions/', 'agent')}${chip(190, 190, 150, '任务卡 tasks/', 'agent')}${chip(400, 190, 130, '会话', 'agent')}
    ${chip(570, 190, 130, '交接', 'agent')}${chip(740, 190, 110, '时间线', 'agent')}${chip(870, 190, 110, '记录维护', 'agent')}
    ${ar(340, 210, 396, 210)}${ar(530, 210, 566, 210)}
    <text class="dl" x="20" y="256">你采纳的决定 → 写进每个新会话开场读到的事实包</text>
    <text class="dl" x="400" y="256">一张卡一个会话，做完写交接，下一个会话从交接接着做</text>
  </svg>`;
}
// ------------------------------------------------------------ the page
function dzTable(head, rows) {
  return `<div class="scroll"><table class="mt dz-t"><tr>${head.map(h => `<th>${h}</th>`).join('')}</tr>${rows.map(r => `<tr>${r.map(c => `<td>${c}</td>`).join('')}</tr>`).join('')}</table></div>`;
}
const dzSec = (id, title, body) => `<section class="dz-sec" id="dz-${id}"><h2><span class="dz-n">${DZ_TOC.findIndex(x => x[0] === id) + 1}</span>${title}</h2>${body}</section>`;

function pageDesign() {
  ensureSessions();
  const S1 = dzSec('why', '为什么要做', `
    <p class="dz-lead">我是博士生，同时推进几个研究项目，每个项目交给 Claude Code 的 agent 去执行，训练跑在 6 台共享 GPU 服务器上。原来的做法是<b>一个项目一个长对话</b>，用了几周后出现四个问题：</p>
    ${dzTable(['问题', '实际数据（2026-11-24 统计）'], [
      ['<b>会忘记之前的指令</b>', 'jev-fraud 的项目对话 3 天里被自动压缩了 7 次；Agent Safety Atlas 两个月被压缩了 44 次。每压缩一次，前面的细节只剩摘要。另外这个对话开在项目的上一级目录，启动时根本读不到项目规则。'],
      ['<b>任务混在一起</b>', 'jev-fraud 里我发的 44 条消息：环境、账号、仓库约 10 条，讨论和解释约 9 条，拍板约 7 条，写作约 7 条，问进度约 6 条，真正下达执行的只有约 4 条。'],
      ['<b>token 流失快</b>', '同一个对话里模型被调用了 2597 次，每次都要把整段对话重读一遍，单次读入的中位数约 25 万 token，3 天累计读入约 6.9 亿。等训练、查进度这类调用只占约 6%，大头是对话太长。Atlas 两个月累计约 142 亿。'],
      ['<b>不知道实验现在干了什么</b>', '实验记录写得很全，但一个实验 60 行、满是术语，最要紧的结论在最后。控制台上 4 个运行显示「运行中」，进程其实早没了；一个项目积了 45 条格式问题；回复了 47 小时还没被处理。']])}
    <p>还有临时开的 agent 没人管、多个 agent 同时改一个目录会冲突。我的目标是：<b>做项目时任何时候都能透明、清楚地知道它在干什么、为什么这么干</b>，同时少花 token、agent 不会忘事。</p>`);
  const S2 = dzSec('principles', '设计原则', `<ol class="dz-ol">
    <li><b>状态在文件里，不在对话里。</b>方案、决定、实验、运行、任务都写在项目的 <code>board/</code> 里。对话随时可以丢、可以换新的，记录不能丢。</li>
    <li><b>人只做决定。</b>我回复待办、采纳决定、确认结论、批准用卡；其余由 agent 和程序做。问进度不用问 agent，看控制台。</li>
    <li><b>靠机制，不靠记性。</b>该记住的东西由钩子在会话开场注入；该遵守的格式由程序当场检查。文档只是建议，检查才是约束。</li>
    <li><b>例行的事交给程序，不花 token。</b>判断运行死活、推进任务状态、派发回复，都是几十行脚本，不是 agent 的对话轮次。</li>
    <li><b>研究和工作分开看。</b>研究视图回答「这个研究发现了什么」，工作视图回答「谁在做什么、做到哪了」。</li>
    <li><b>先讲清楚，再动手。</b>每个实验开始前写「想回答什么、怎么做、预期」，结束后写「结果、所以」，用大白话，带一个具体例子。</li>
    <li><b>短会话、职责单一。</b>一个会话只做一件事，做完写交接就结束；新会话从文件接上。</li></ol>`);
  const S3 = dzSec('objects', '四件事：项目、任务、会话、运行', `
    <p>先把四件经常被混在一起的东西分开，各自有自己的存放位置和寿命：</p>
    ${dzTable(['对象', '存在哪', '谁写', '寿命'], [
      ['<b>项目</b>：研究目标、长期规则、方案、决定', '<code>CLAUDE.md</code>、<code>board/plan.md</code>、<code>board/decisions/</code>、<code>project.json</code>', '项目会话', '直到我归档'],
      ['<b>任务</b>：一件有交付物的具体事', '<code>board/tasks/&lt;id&gt;.md</code>，挂在研究视图的某一步上', '项目会话建卡，任务会话更新', '完成、暂停或放弃'],
      ['<b>会话</b>：做某个任务时的推理和操作', 'Claude 自己的会话记录；任务卡里登记会话名', '—', '按需开，做完就结束，之后还能接上'],
      ['<b>运行</b>：一次训练、评测或数据处理', '<code>board/runs/&lt;编号&gt;/</code>：记录、指标、心跳、退出码', 'boardrun 自动写', '记录永久保留']])}
    <p>例子：「验证小环境能否代替完整训练」是一个研究上的<b>尝试</b>；下面可以有三张<b>任务卡</b>：设计比较方案、跑实验、写分析；跑实验那张卡由一个<b>会话</b>做，它启动了 7 个<b>运行</b>。</p>`);
  const S4 = dzSec('views', '两个视图：研究和工作', dzViews() + `
    ${dzTable(['视图', '页签', '回答的问题'], [
      ['研究', '概况 · 方法 · 实验 · 运行 · 样例 · 实验设置 · 文档', '研究问题是什么，方法怎么做，试过什么、结果如何、为什么'],
      ['工作', '任务 · 讨论与决定 · 会话 · 时间线 · 记录维护', '谁在做什么、做到哪了、定过什么、花了多少 token、记录有没有写错']])}
    <p>每个项目页顶部固定四格：<b>现在要回答的问题、有效的决定、下一步、最新证据</b>。不管在哪个页签，都能一眼看到这个项目的现状。</p>`);
  const S5 = dzSec('arch', '整体结构：服务器和程序', dzArch() + `
    <ul class="dz-ul">
      <li><b>会话都开在 a100</b>：每个项目一个常驻入口（tmux 里的 Remote Control 服务），手机和网页都能点开；任务会话也在 a100。管理程序只用 CPU。</li>
      <li><b>训练在有空卡的机器上跑</b>：用记录模块 boardrun 在目标机器的 tmux 里启动，它把记录、心跳、退出码写到共享盘。控制台和管理程序重启都不影响训练。</li>
      <li><b>共享盘就是总线</b>：没有在各服务器上另建执行服务、也不开端口；谁都通过共享盘上的文件交换状态。</li>
      <li><b>控制台几乎只读</b>：只写三类东西：我的回复、文档标记、显示名。它不能启动任何程序，网页上的写操作都要通过同源检查。</li></ul>`);
  const S6 = dzSec('roles', 'Agent 分工', `
    ${dzTable(['角色', '做什么', '什么时候存在'], [
      ['<b>项目会话</b>', '和我讨论方向，维护方案和决定，拆任务卡、开任务会话、看结果；长时间的执行不在这里做', '每个项目一个；上下文超过 15 万或定新版本时，写好交接换新的'],
      ['<b>任务会话</b>', '做一张卡：讨论 / 执行 / 写作 / 分析 / 审查 / 调试；做完写交接', '<code>board task-start</code> 开，做完结束'],
      ['<b>研究服务台</b>', '服务器、账号、密钥、ssh、控制台本身；跨项目的事', '常驻一个'],
      ['<b>子 agent</b>', '会话里读大量日志、论文、历史，只把摘要交回来', '用完即走'],
      ['<b>审查会话</b>', '核心结论在我确认之前，独立核对数字和运行记录', '只在验收核心实验时，由项目会话开（还没有自动化）'],
      ['<b>程序</b>', '收尾、派活、记录检查、事实包、心跳', '常驻，不花 token']])}
    <p><b>约束</b>：同时最多 4 个任务会话；同一个项目同时只有一个会话改代码；同一件事失败 3 次就停下写待办问我。服务器、账号这类杂事不进项目会话，统一去研究服务台。</p>`);
  const S7 = dzSec('life', '一个项目从头到尾', dzTable(['步骤', '我做的', '会话和程序做的'], [
    ['1. 新建', '说「新建项目 名字」，确认设定，运行两行命令', '建目录、记录骨架、实验设置、方法步骤'],
    ['2. 定方案', '在 App 里和项目会话讨论', '写方案；我拍板的事记成「候选决定」挂到控制台；讨论告一段落写纪要'],
    ['3. 采纳决定', '在控制台点「采纳」', '采纳的决定从此出现在每个新会话的事实包里'],
    ['4. 拆任务', '看一眼任务看板', '项目会话写任务卡，一张卡开一个任务会话'],
    ['5. 要用卡', '批准用卡、写预期（猜不到就从选项里选一个）', '任务会话用 boardrun 启动训练，卡改「等运行」，会话结束'],
    ['6. 训练期间', '想看进度就看控制台', '运行每分钟写心跳；收尾程序每 5 分钟检查'],
    ['7. 出结果', '在实验的「一页纸」上对照预期看结果，给结论', '卡变「可以继续」；项目会话开分析会话写一页纸和报告，核心结论另开审查会话核对'],
    ['8. 下一轮', '回到第 2 步', '项目会话太长时换新，说「接着来」就从文件接上'],
    ['9. 结束', '说「结束项目」', '起草结论、释放显卡、问 checkpoint 去留、提交、归档、关入口']]));
  const S8 = dzSec('memory', '不忘事：事实包和决定', `
    <p>agent 忘事的根源是：指令只活在对话里，对话一压缩就没了。所以把要记住的东西都落到文件，再用机制在需要时塞回会话：</p>
    ${dzTable(['内容', '放在哪', '怎么维护'], [
      ['长期约束', '项目 <code>CLAUDE.md</code>（启动时自动读入）', '能检查的改成程序检查'],
      ['决定', '<code>board/decisions/</code>：候选 / 采纳 / 不采纳 / 已替代', '会话只能写「候选」；我在控制台点「采纳」立即生效；新决定写明替代哪条'],
      ['当前状态', '从 <code>board/</code> 生成，和控制台用同一份代码', '不用人维护'],
      ['当前任务', '任务卡', '任务会话更新，结束写交接']])}
    <ul class="dz-ul">
      <li><b>事实包</b>：a100 和 h100 的 Claude 都装了 SessionStart 钩子。在项目目录开会话、恢复会话、或对话被压缩之后，钩子运行 <code>board brief</code>，把「已采纳的决定、进行中的任务、在跑的运行、等我的事、最近的结论」注入会话，不超过 6000 字。</li>
      <li><b>指令落盘</b>：我在对话里说的长期有效的话，会话当场写成候选决定并告诉我「已记下」。讨论中的想法只是候选，不会被当成永久指令。</li>
      <li><b>写入就检查</b>：PostToolUse 钩子在会话改了 <code>board/</code> 里的文件后立刻跑记录检查，有问题把错误交给会话（退出码 2），会话当场改掉，而不是攒成几十条等人修。</li></ul>`);
  const S9 = dzSec('trust', '状态可信：心跳和收尾', `
    <p>以前判断运行死活靠「显卡上能不能看到进程」，看不到就标「可能已中断」，结果 4 个早已停掉的运行一直挂着「运行中」。现在：</p>
    <ul class="dz-ul">
      <li><b>运行自己报告</b>：boardrun 每分钟写一次心跳文件，结束时写状态和退出码（正常结束「完成」，报错「失败」，SIGTERM「被中断」）。</li>
      <li><b>绑定代码版本</b>：代码有未提交的改动、或还没有提交，boardrun 拒绝启动（调试可以显式放开），记录里有提交号；从 worktree 或项目里的另一个仓库启动时，记的是那个仓库。</li>
      <li><b>只在有证据时下结论</b>：</li></ul>
    ${dzTable(['显示', '条件'], [
      ['运行中', '心跳是新的（5 分钟内），或者在机器上看到了它的进程'],
      ['状态未知', '心跳停了，但机器连不上或看不到进程：先不下结论，显示最后观测时间'],
      ['已中断', '心跳停了，机器也回答了「进程不在」'],
      ['已结束', '记录里已经写了结束']])}
    <p><b>收尾程序</b>（cron 每 5 分钟）对心跳停了的运行去机器上查进程（每台机器一轮只连一次 ssh，h100/h200 会拒绝频繁连接），<b>连续两轮</b>都查不到才把记录改成「被中断」，并写明是程序判定的；任务卡「等运行」的运行都结束后，改成「可以继续」。上线当天它就自动关掉了那 4 个僵尸运行。</p>`);
  const S10 = dzSec('loop', '回复和决定的闭环', `
    <p>需要我决定的事，会话写进待办（带背景、选项、建议），我在控制台点选项回复。回复的处理有两种方式：</p>
    ${dzTable(['方式', '怎么触发', '适合'], [
      ['默认：等我说', '我在项目会话里说「处理回复」，会话逐条复述理解、执行、标记完成', '需要我在场看着的'],
      ['直接处理', '回复时勾上；派活程序（cron 每 5 分钟）为这个项目起一个无界面的会话，只处理勾了的那几条，每条只领一次', '意思明确、不用我盯的']])}
    <p>每条回复的进度显示为：<b>已回复 → 会话已领取 → 完成</b>，或者<b>受阻</b>（写明原因，并另留一条待办问我）。待办页把我回复过的集中在一起，按「受阻 / 正在处理 / 自动处理中 / 等我说处理回复」分类。决定、要我确认的实验结论也走同一个通道。</p>`);
  const S11 = dzSec('read', '让人看得懂：一页纸、时间线、术语', `
    <p><b>一页纸</b>：每个实验最上面固定五行，用大白话，记录检查会检查缺项和长度（每行不超过 150 字）。下面是 jev-authz 里一个真实实验的一页纸：</p>
    <div class="onepager">
      <div class="oprow"><span class="opk">想回答</span><div class="opv">开源复现的 Kev-4B 不训练、只用通用提示词，能不能代替官方 Jev 来做实验？</div></div>
      <div class="oprow"><span class="opk">怎么做</span><div class="opv">用同一份通用提示词，让 Kev-4B 判四个自建测试集和四个外部测试集，和官方 Jev 的结果比。</div></div>
      <div class="oprow"><span class="opk">预期</span><div class="opv">用户没写（回填记录）。</div></div>
      <div class="oprow op-结果"><span class="opk">结果</span><div class="opv">不能代替。自建测试集上它比官方 Jev 低一大截，该拦没拦和该放没放都多；只有外部的 JAB 上接近。</div></div>
      <div class="oprow op-所以"><span class="opk">所以</span><div class="opv">Kev-4B 只当「不训练的基线」用；在它身上得到的结论不能直接套到官方 Jev 上。</div></div></div>
    <ul class="dz-ul">
      <li><b>预期是理解的检验</b>：核心实验开始前我先写预期。我说「猜不到」时，会话给几个结果选项让我选；选不出来，说明实验还没讲清楚，先讲清楚再跑。</li>
      <li><b>你上次来之后</b>：控制台记住我上次打开每个项目的时间（只存在浏览器里），概况页和首页列出这之后的变化；时间线把定版、开支线、实验、运行、任务、决定、回复按时间串起来。</li>
      <li><b>术语</b>：每个项目在实验设置里给常用说法写一句白话解释，正文里第一次出现时加虚下划线，鼠标放上去就看到。</li>
      <li><b>首页「今天」</b>：等我决定的事（一条一行）、卡住的事（运行状态存疑、任务等我、回复没人处理）、正在推进的项目。</li></ul>`);
  const S12 = dzSec('tokens', 'Token 花在哪、怎么省', `
    <p>关键认识：<b>每次模型调用都要把整段对话重读一遍</b>，所以 token 主要花在对话长度上，而不是做了多少事。上下文从 25 万降到 5–8 万，同样的工作量读入的 token 粗算能降到三分之一到五分之一。</p>
    <ul class="dz-ul">
      <li>短会话：一张卡一个会话，做完就结束；项目会话超过 15 万就换新（会话页会标黄提醒）。</li>
      <li>等训练不守着：靠心跳和收尾程序，会话不轮询。</li>
      <li>问进度看控制台，不问 agent。</li>
      <li>读大量材料交给子 agent，主会话只拿摘要。</li>
      <li>事实包有长度上限，交接只写目标、证据位置和下一步。</li>
      <li>会话页按会话显示上下文大小、今天和 7 天的 token、压缩次数，看得到钱花在哪。</li></ul>
    <p class="small">需要注意：拆成多个 agent 并不会自动省钱。子 agent 和任务会话也用同一个账户的额度，并行越多短时间消耗越快。省下来的是每次调用带的上下文，前提是每个会话短而专一。</p>`);
  const S13 = dzSec('parallel', '并行和合并', `
    ${dzTable(['文件', '怎么避免冲突', '要不要合并'], [
      ['代码', '每张改代码的卡一个 git worktree（<code>.worktrees/&lt;任务&gt;</code>，分支 <code>task/&lt;任务&gt;</code>）', '用 git 合并'],
      ['任务产物', '每张卡一个产物目录', '不用'],
      ['自己的记录', '任务卡、尝试、运行、新文档本来就是一个文件一件事', '不用'],
      ['共用文件', '方案、实验设置、方法、决定、项目规则只由项目会话改；任务会话在交接里写「建议改动」', '项目会话采纳时写入'],
      ['待办和回复', '只追加新行，最后一行为准', '不用']])}
    <p>不设专门的「合并 agent」：大部分合并 git 自己就能完成；真冲突说明两张卡碰了同一块逻辑，这是设计问题。所以收尾时任务会话先 rebase、跑测试；没动核心代码且没冲突就自己合并，<b>动了核心代码或有冲突就交给我决定</b>（和师兄仓库里「改到护栏文件由人合并」同一个思路）。更重要的是预防：卡上写明允许修改的范围，范围重叠的卡不同时做。</p>`);
  const S14 = dzSec('borrow', '借鉴了什么、没照搬什么', `
    <p>参考了师兄的 <a href="https://github.com/Junyi-Agent/skills" target="_blank" rel="noopener">Junyi-Agent/skills</a>（维护代码仓库用的一组 skill），也让 GPT 提了一版建议，批判地合并：</p>
    ${dzTable(['来源', '借鉴的', '在这里变成了'], [
      ['jy-repo-maintain', '每次只跑一轮，跑完退出；issue 是工作单位；人只打标签、合并；进度写在 maintain-log', '短会话 + 任务卡；我只回复、采纳、确认；首页「今天」和时间线代替问进度'],
      ['jy-repo-maintain', '限流（每轮最多 2 个 PR），同一个文件连修 3 次就停下问人', '同时最多 4 个任务会话；同一件事失败 3 次就停下写待办'],
      ['jy-repo-codify', '文档是建议，检查才是约束；同一个纠正说两次就变成检查', '写记录时当场检查的钩子；格式问题从我的待办里分出去，进「记录维护」'],
      ['ADR 的状态', '提议 → 采纳 → 已验证，agent 最多改到「采纳」', '决定：会话只能写「候选」，只有我能采纳'],
      ['jy-skills-update', '读历史的重活交给子 agent，主会话只拿摘要', '会话分工里的子 agent'],
      ['GPT', '项目 / 任务 / 会话 / 运行分开；事实包；决定分候选和采纳；交接五项；运行自己报告状态；回复的处理进度；状态分开显示', '基本照用']])}
    ${dzTable(['没照搬的', '原因'], [
      ['全自动的定时维护循环', '代码仓库有 CI 判对错、任务明确；研究项目大部分活要和我来回讨论，对错也要我确认。只把「确定的例行事」交给程序'],
      ['自动选服务器、自动调度', '和「启动训练前先问我」冲突；只做推荐，由我批'],
      ['各服务器上另建执行服务、systemd 守护', '共享盘 + tmux + 心跳已经够用，不多开端口和常驻进程'],
      ['回复后一律自动处理', '默认仍等我说「处理回复」；勾「直接处理」才自动']])}`);
  const S15 = dzSec('rejected', '考虑过、没采用的', dzTable(['方案', '为什么没选'], [
    ['A. 方案会话 + 任务卡（采用）', '每个会话短、职责单一；我能直接和任何一个任务会话说话；全都在控制台上看得见'],
    ['B. 一个主管会话派子 agent', '入口唯一最省事，但主管的对话还是越来越长，忘事只是减轻；子 agent 我看不见、不能直接说话；并行时 token 烧得更快'],
    ['C. 不留长会话，定时巡检全自动', '最省 token、最不会忘，但研究需要判断的地方多，全自动容易跑偏，出错排查慢'],
    ['专门的合并 agent', '它不知道每处改动的原因，真冲突时只能猜；预防冲突比合并更重要'],
    ['DCGM Exporter 等指标服务', '现有显卡监控够用；项目归属本来就要靠运行记录关联']]));
  const S16 = dzSec('parts', '组成部分和代码', `
    ${dzTable(['部分', '位置', '做什么'], [
      ['记录格式', '<code>_shared/BOARD_FORMAT.md</code>（约 480 行）', '各项目 board/ 怎么写：一页纸、任务卡、决定、纪要、术语、运行记录……'],
      ['全局规则', '各服务器 <code>~/.claude/CLAUDE.md</code>（a100 为准，分发到 6 台）', '会话分工、记录、启动、理解项目的规则'],
      ['控制台', 'a100 <code>~/gpu-board</code>：server.py、projects.py、sessions.py、app.js、work.js', '读记录、显卡、会话；网页'],
      ['board 工具', '<code>boardtool.py</code>（<code>board</code> 命令）', 'brief 事实包、check 记录检查、reconcile 收尾、task-start 开任务会话、dispatch 派活'],
      ['记录模块', '<code>_shared/tools/boardrun</code>', '运行自己写记录、心跳、退出码，绑定提交'],
      ['钩子', 'a100、h100 的 <code>~/.claude/settings.json</code>', 'SessionStart 注入事实包；PostToolUse 改记录当场检查'],
      ['定时任务', 'a100 crontab', '控制台开机启动、各项目入口开机拉起、收尾和派活每 5 分钟'],
      ['常驻入口', '<code>~/bin/rc-start</code>、<code>rc-loop</code>', '每个项目一个 Remote Control 服务，掉线自动重启']])}
    <p class="small">控制台和工具共约 5200 行，全部只用 Python 标准库和原生 JavaScript；98 个测试（解析、记录检查、收尾、事实包和钩子、任务会话、HTTP 接口、无头浏览器里的页面和点击）。</p>`);
  const S17 = dzSec('now', '现在的状态', dzLive() + `
    <ul class="dz-ul"><li>jev-authz：15 个实验都补上了一页纸，32 个术语，6 条候选决定，10 张任务卡，记录检查 0 条，进度笔记精简成「交接 / 环境说明 / 踩过的坑」。</li>
      <li>jev-fraud：对话已换成项目目录里的标准入口，4 个僵尸运行已自动关闭，正在按新格式补记录。</li></ul>`);
  const S18 = dzSec('next', '还没解决的和下一步', `<ul class="dz-ul">
    <li><b>试点验收</b>：用 jev-fraud 走一遍完整流程：网页上做实验决定 → 任务会话接着做 → 批卡 → 训练 → 自动出分析；中途关掉会话再接上，任务、有效决定、实验状态都能找回。</li>
    <li><b>别的服务器上的会话</b>：会话页目前只看 a100；h100 上的会话还看不到。</li>
    <li><b>手机上看进度</b>：控制台只在 Mac 上能开（ssh 隧道），手机上只能问 agent。考虑定时把只读版本推到私有页面。</li>
    <li><b>自动回收</b>：做完的任务会话、闲置超过 3 天的会话，现在只提示，不自动关。</li>
    <li><b>记录维护的定时轮次</b>：确定的格式问题（缺定义、链接错）可以由一个限量的维护轮次自动修，留修改记录。</li>
    <li><b>模型分级</b>：例行的执行和写作用便宜的模型，讨论用强模型；等有了按任务的 token 统计再定。</li>
    <li><b>迁移辅助</b>：从 checkpoint 换机器续跑，现在靠规则和手工；可以做成命令。</li>
    <li><b>理解检验</b>：试用师兄的 jy-repo-quiz，让 agent 出题考我对核心代码的理解。</li></ul>`);
  const S19 = dzSec('discuss', '想一起讨论的问题', `<ol class="dz-ol">
    <li>研究项目里，哪些事情可以像代码仓库那样「一轮一轮自动维护」，哪些必须人来判断？边界画在哪里？</li>
    <li>决定只分「候选 / 采纳」够不够？要不要像 ADR 一样加「已验证」（人测过、确认有效）这一档？</li>
    <li>记录格式的检查越来越多，怎么避免检查本身变成负担（例如这次按步编号的指标被误报）？</li>
    <li>用什么指标衡量这套工作流是否变好了？比如每个结论花的 token、从提出问题到拿到结论的时间、每周压缩次数、我回复待办的平均等待时间。</li>
    <li>任务会话和子 agent 的边界：什么时候值得开一个我能直接对话的任务会话，什么时候用看不见的子 agent 就够？</li>
    <li>团队协作时，这套私人的 board/ 怎么和团队仓库、issue 流程对接，又不把私人记录放进团队仓库？</li></ol>`);
  const toc = `<nav class="dz-toc">${DZ_TOC.map(([id, t], i) => `<a href="#/design" data-goto="dz-${id}"><span>${i + 1}</span>${t}</a>`).join('')}</nav>`;
  return `<div class="dz" id="dz-root"><div class="dz-head"><h1 class="pt">设计思路</h1>
      <div class="dz-sub">用 Claude agent 推进研究项目：怎么分工、怎么不忘事、怎么省 token、怎么让我随时看得懂</div>
      <div class="dz-act"><button id="dz-export" title="把这一页存成一个独立的 HTML 文件，可以直接发给别人；里面有 jev-authz 的一个实验例子和本页的实时数字">导出成单个 HTML 文件</button>
        <span class="small">更新于 2026-11-24 · 在控制台里打开时，第 17 节的数字是实时的</span></div></div>
    <div class="dz-body">${toc}<div class="dz-main">${S1}${S2}${S3}${S4}${S5}${S6}${S7}${S8}${S9}${S10}${S11}${S12}${S13}${S14}${S15}${S16}${S17}${S18}${S19}</div></div></div>`;
}

// one self-contained file: the page's own styles plus this page's content, no scripts
function exportDesign() {
  const css = [...document.querySelectorAll('style')].map(s => s.textContent).join('\n');
  const root = document.getElementById('dz-root');
  if (!root) return;
  const clone = root.cloneNode(true);
  clone.querySelectorAll('.dz-act').forEach(x => x.remove());
  clone.querySelectorAll('.dz-toc a').forEach(a => { a.setAttribute('href', '#' + a.dataset.goto); a.removeAttribute('data-goto'); });
  const html = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>设计思路 · 用 Claude agent 推进研究项目</title><style>${css}
body{background:var(--bg)} .main{padding:24px 28px 48px} .dz-toc{position:static}</style></head>
<body><main class="main"><div class="page">${clone.outerHTML}
<p class="small">导出自 CD 的研究控制台，${new Date().toLocaleString('zh-CN')}。</p></div></main></body></html>`;
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([html], {type: 'text/html;charset=utf-8'}));
  a.download = '研究项目管理-设计思路.html';
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}
document.addEventListener('click', e => { if (e.target.closest('#dz-export')) exportDesign(); });
