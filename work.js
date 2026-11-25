// The work view and the pages built around it: the project head (question, adopted decisions, next tasks, latest
// evidence), 概况 (what changed since your last visit), 任务, 讨论与决定, 会话, 时间线, 记录维护, the global 会话 page,
// the 一页纸 box and term hints. Loaded before app.js; it uses app.js names (B, esc, plink, ctx, ...) only inside functions.

// ============================================================ last visit (kept in this browser only)
const SEEN_KEY = 'board.seen.v1';
const seenThisLoad = {};  // project -> the previous visit, fixed for this page load so "new" marks don't vanish on refresh
function readSeen() { try { return JSON.parse(localStorage.getItem(SEEN_KEY) || '{}') || {}; } catch (e) { return {}; } }
function prevSeen(name, now) {
  if (!(name in seenThisLoad)) {
    const all = readSeen();
    seenThisLoad[name] = typeof all[name] === 'number' ? all[name] : null;
    all[name] = now;
    try { localStorage.setItem(SEEN_KEY, JSON.stringify(all)); } catch (e) {}
  }
  return seenThisLoad[name];
}
const lastSeenOf = name => name in seenThisLoad ? seenThisLoad[name] : (readSeen()[name] ?? null);

// ============================================================ small pieces
const TASK_COLS = ['待开始', '进行中', '等运行', '等你', '可以继续'];
const TASK_TONE = {'待开始': 'other', '进行中': 'run', '等运行': 'run', '等你': 'todo', '可以继续': 'todo', '完成': 'ok', '暂停': 'off', '放弃': 'off'};
const taskPill = s => s ? `<span class="pill t-${TASK_TONE[s] || 'other'}">${esc(s)}</span>` : '';
const DEC_TONE = {'候选': 'todo', '采纳': 'ok', '不采纳': 'off', '已替代': 'off'};
const decPill = s => s ? `<span class="pill t-${DEC_TONE[s] || 'other'}">${esc(s)}</span>` : '';
const RUN_STATE = {'运行中': 'run', '状态未知': 'todo', '已结束': 'other', '已中断': 'bad'};
const RUN_TIP = {'运行中': '心跳是新的，或者在显卡上看到了它的进程', '状态未知': '心跳停了且机器连不上；或者是手写的记录（没有心跳），进程已经不在，但可能是跑完了没写结果。要会话核对后收尾',
  '已中断': '心跳停了，机器上也找不到它的进程；收尾程序会把记录改成被中断', '已结束': '记录写着已经结束'};
const statePill = s => s && s !== '已结束' ? `<span class="pill t-${RUN_STATE[s] || 'other'}" title="${esc(RUN_TIP[s] || '')}">${esc(s)}</span>` : '';
const isoT = iso => { if (typeof iso !== 'string' || !iso) return null; const t = Date.parse(iso.length === 10 ? iso + 'T00:00:00Z' : iso); return isNaN(t) ? null : t / 1000; };
const kTokens = n => n == null ? '—' : n >= 1e8 ? (n / 1e8).toFixed(1) + ' 亿' : n >= 1e4 ? Math.round(n / 1e4) + ' 万' : String(n);
const refHref = (d, ref) => typeof ref !== 'string' ? null : ref.startsWith('m:') ? plink(d.name, 'method', ref.slice(2))
  : ref.startsWith('k:') ? plink(d.name, 'tasks', ref.slice(2)) : ref.startsWith('d:') ? plink(d.name, 'decisions', ref.slice(2))
  : /^[vta]:/.test(ref) ? plink(d.name, 'flow', ref) : null;
const listOf = x => Array.isArray(x) ? x : x == null || x === '' ? [] : [x];

// per-project work lookups, once per fetched snapshot
function work(d) {
  if (d._work) return d._work;
  const states = d.decision_states || {};
  const tasks = (d.tasks || []).filter(t => typeof t.meta.id === 'string');
  const decisions = (d.decisions || []).filter(r => typeof r.meta.id === 'string').map(r => Object.assign(r, {state: states[r.meta.id] || r.meta.status || '候选'}));
  const replacedBy = new Map();
  for (const r of decisions) if (r.state === '采纳') for (const old of listOf(r.meta.replaces)) replacedBy.set(String(old), r);
  const runsOfTask = id => d.runs.filter(x => (x.run || {}).task === id || listOf((tasks.find(t => t.meta.id === id) || {meta: {}}).meta.runs).includes(runKey(x)));
  const minutes = docItems(d).filter(x => x.kind === '纪要');
  return d._work = {tasks, decisions, replacedBy, runsOfTask, minutes,
    task: id => tasks.find(t => t.meta.id === id), decision: id => decisions.find(r => r.meta.id === id)};
}
const taskTitle = t => t.meta.title || t.meta.id;
const decLine = r => B.firstSentence('## 决定\n' + plain((r.sections || {})['决定']), '决定') || plain((r.sections || {})['决定']).split('\n')[0] || '';

// ============================================================ terms and the 一页纸
function termsOf(d) {
  if (d._terms) return d._terms;
  const list = ((d.setup || {}).terms || []).filter(x => x && typeof x.term === 'string' && x.term.trim() && typeof x.about === 'string');
  return d._terms = list.map(x => ({term: x.term.trim(), about: x.about})).sort((a, b) => b.term.length - a.term.length);
}
// underline the first use of each project term in rendered Markdown (class "md") and 一页纸 values (class "opv");
// never in labels, links, code, buttons or headings
function withTerms(html, d) {
  const terms = termsOf(d);
  if (!terms.length || !html) return html;
  const used = new Set();
  let skip = 0, region = 0;  // region: open <div>s since entering a .md / .opv block (0 = outside)
  return html.split(/(<[^>]+>)/).map(part => {
    if (part.startsWith('<')) {
      if (/^<div\b/i.test(part)) { if (region) region++; else if (/class="[^"]*\b(md|opv)\b[^"]*"/.test(part)) region = 1; }
      else if (/^<\/div>/i.test(part) && region) region--;
      if (/^<(a|code|pre|button|textarea|abbr|h1|h2|h3|select)\b/i.test(part)) skip++;
      else if (/^<\/(a|code|pre|button|textarea|abbr|h1|h2|h3|select)>/i.test(part)) skip = Math.max(0, skip - 1);
      return part;
    }
    if (!region || skip || !part.trim()) return part;
    let out = '', rest = part;
    for (;;) {
      let best = null;
      for (const t of terms) {
        if (used.has(t.term)) continue;
        const i = rest.indexOf(esc(t.term));
        if (i >= 0 && (!best || i < best.i)) best = {i, t};
      }
      if (!best) break;
      used.add(best.t.term);
      const e = esc(best.t.term);
      out += rest.slice(0, best.i) + `<abbr class="term" title="${esc(best.t.about)}">${e}</abbr>`;
      rest = rest.slice(best.i + e.length);
    }
    return out + rest;
  }).join('');
}
const OP_LABEL = {'想回答': '想回答', '怎么做': '怎么做', '预期': '预期', '结果': '结果', '所以': '所以'};
function onepagerBox(a, opts = {}) {
  const op = a.onepager || {}, keys = Object.keys(OP_LABEL).filter(k => op[k]);
  if (!keys.length) return opts.missing ? `<div class="opmiss">这次尝试还没有「一页纸」（想回答 / 怎么做 / 预期 / 结果 / 所以）。在项目会话里说「补一页纸」。</div>` : '';
  return `<div class="onepager">${keys.map(k => `<div class="oprow op-${k}"><span class="opk">${OP_LABEL[k]}</span><div class="opv">` +
    `${B.inline(op[k].text)}${op[k].extra ? `<div class="md opx">${B.md(op[k].extra)}</div>` : ''}</div></div>`).join('')}</div>`;
}

// ============================================================ project head: question, adopted decisions, next tasks, latest evidence
function projectHero(d) {
  const p = d.project || {}, W = work(d), name = d.name;
  const adopted = W.decisions.filter(r => r.state === '采纳').sort((a, b) => String(b.meta.date || '').localeCompare(String(a.meta.date || '')));
  const order = {'可以继续': 0, '等你': 1, '进行中': 2, '等运行': 3, '待开始': 4};
  const open = W.tasks.filter(t => t.meta.status in order).sort((a, b) => order[a.meta.status] - order[b.meta.status]);
  const latest = [...d.attempts].filter(a => ((a.onepager || {})['所以'] || {}).text).sort((a, b) => dateKey(b).localeCompare(dateKey(a)))[0];
  const cell = (k, href, body, more) => `<div class="hcell"><div class="hk">${href ? `<a href="${href}">${k}</a>` : k}</div>${body}${more || ''}</div>`;
  const q = p.question ? `<div class="hq">${esc(p.question.length > 150 ? p.question.slice(0, 149) + '…' : p.question)}</div>` : '<div class="faint">project.json 里还没写研究问题</div>';
  const dec = adopted.length ? `<ul class="hl">${adopted.slice(0, 3).map(r => `<li><a href="${plink(name, 'decisions', r.meta.id)}">${esc(r.meta.title || r.meta.id)}</a></li>`).join('')}</ul>`
    : '<div class="faint">还没有采纳的决定</div>';
  const nxt = open.length ? `<ul class="hl">${open.slice(0, 3).map(t => `<li>${taskPill(t.meta.status)} <a href="${plink(name, 'tasks', t.meta.id)}">${esc(taskTitle(t))}</a></li>`).join('')}</ul>`
    : '<div class="faint">没有进行中的任务卡</div>';
  const ev = latest ? `<div><a href="${plink(name, 'flow', 'a:' + attKey(latest))}">${esc(attName(latest))}</a> ${B.pill(latest.meta.status)}</div>
      <div class="small">${B.inline(latest.onepager['所以'].text)}</div>` : '<div class="faint">还没有写了「一页纸」的结论</div>';
  return `<div class="hero">
    ${cell('现在要回答的问题', null, q)}
    ${cell('有效的决定', plink(name, 'decisions'), dec, adopted.length > 3 ? `<div class="small">还有 ${adopted.length - 3} 条</div>` : '')}
    ${cell('下一步', plink(name, 'tasks'), nxt, open.length > 3 ? `<div class="small">共 ${open.length} 张进行中的卡</div>` : '')}
    ${cell('最新证据', plink(name, 'flow'), ev)}</div>`;
}

// ============================================================ timeline: everything that happened, newest first
function timelineOf(d) {
  if (d._tl) return d._tl;
  const ev = [], name = d.name, W = work(d);
  const add = (t, view, text, href) => { if (t) ev.push({t, view, text, href}); };
  for (const v of d.versions) add(isoT(v.meta.date), '研究', `定版 ${idOf(v)}${v.meta.title ? ' · ' + v.meta.title : ''}`, plink(name, 'flow', 'v:' + idOf(v)));
  for (const t of d.tracks) {
    add(isoT(t.meta.started), '研究', `开支线「${t.meta.title || idOf(t)}」`, plink(name, 'flow', 't:' + idOf(t)));
    if (t.meta.ended) add(isoT(t.meta.ended), '研究', `支线「${t.meta.title || idOf(t)}」结束：${statusOf(t)}`, plink(name, 'flow', 't:' + idOf(t)));
  }
  for (const a of d.attempts) {
    const t0 = isoT(a.meta.date) || isoT(attDate(a));
    add(t0, '研究', `${a.meta.status === '计划中' ? '计划' : '尝试'}「${attName(a)}」${a.meta.status ? '（' + a.meta.status + '）' : ''}`, plink(name, 'flow', 'a:' + attKey(a)));
    if (a.mtime && (!t0 || a.mtime - t0 > 3600)) add(a.mtime, '研究', `更新了尝试「${attName(a)}」${a.meta.status ? '：' + a.meta.status : ''}`, plink(name, 'flow', 'a:' + attKey(a)));
  }
  for (const r of d.runs) {
    const x = r.run || {};
    add(isoT(x.started), '研究', `开始运行 ${runKey(r)}${x.server ? ' @' + shortHost(x.server) : ''}${gpusText(x)}`, plink(name, 'runs', runKey(r)));
    if (x.ended) add(isoT(x.ended), '研究', `运行 ${runKey(r)} ${x.status || '结束'}${x.closed_by ? '（收尾程序判定）' : ''}`, plink(name, 'runs', runKey(r)));
  }
  for (const x of docItems(d)) add(x.mtime, '研究', `${x.kind === '纪要' ? '讨论纪要' : '文档'}「${x.title}」${x.kind && x.kind !== '纪要' ? '（' + x.kind + '）' : ''}更新`,
    x.kind === '纪要' ? plink(name, 'decisions', 'doc:' + x.id) : plink(name, 'docs', x.id));
  for (const t of W.tasks) {
    add(isoT(t.meta.created), '工作', `新任务「${taskTitle(t)}」`, plink(name, 'tasks', t.meta.id));
    const u = isoT(t.meta.updated);
    if (u && u !== isoT(t.meta.created)) add(u, '工作', `任务「${taskTitle(t)}」：${t.meta.status || ''}`, plink(name, 'tasks', t.meta.id));
  }
  for (const r of W.decisions) add(isoT(r.meta.date), '工作', `${r.meta.status === '采纳' ? '决定' : '候选决定'}「${r.meta.title || r.meta.id}」`, plink(name, 'decisions', r.meta.id));
  for (const t of d.todos || []) {
    add(isoT(t.created), '工作', `待办：${t.text}`, '#/todo');
    if (t.done) add(isoT(t.done), '工作', `办完：${t.text}${t.result ? '（' + t.result + '）' : ''}`, '#/todo');
  }
  for (const [id, a] of Object.entries(d.answers || {})) add(isoT(a.at), '工作', `你回复了 ${id.startsWith('d:') ? '决定 ' + id.slice(2) : id.startsWith('a:') ? '尝试 ' + id.slice(2) : '待办 ' + id}：${[a.answer, a.text].filter(Boolean).join(' ')}`,
    id.startsWith('d:') ? plink(name, 'decisions', id.slice(2)) : id.startsWith('a:') ? plink(name, 'flow', id) : '#/todo');
  ev.sort((a, b) => b.t - a.t);
  return d._tl = ev;
}
const dayOf = t => { const x = new Date(t * 1000), p = n => String(n).padStart(2, '0'); return `${x.getFullYear()}-${p(x.getMonth() + 1)}-${p(x.getDate())}`; };
const hm = t => { const x = new Date(t * 1000), p = n => String(n).padStart(2, '0'); return `${p(x.getHours())}:${p(x.getMinutes())}`; };
function eventList(evs, since, now) {
  let day = null, out = '';
  for (const e of evs) {
    const dd = dayOf(e.t);
    if (dd !== day) { out += `<li class="day">${dd}</li>`; day = dd; }
    out += `<li class="${since != null && e.t > since ? 'new' : ''}"><span class="when">${hm(e.t)}</span><span class="tag2">${e.view}</span>` +
      `<a href="${e.href}">${esc(e.text.length > 120 ? e.text.slice(0, 119) + '…' : e.text)}</a></li>`;
  }
  return out ? `<ul class="tline">${out}</ul>` : '<div class="empty">还没有记录。</div>';
}
function tabTimeline(d, r) {
  const since = lastSeenOf(d.name), v = (r.query || {}).v || '';
  const all = timelineOf(d), evs = all.filter(e => !v || e.view === v);
  const chip = (label, val) => `<a class="chip${v === val ? ' on' : ''}" href="${plink(d.name, 'timeline', null, {v: val})}">${label}</a>`;
  return `<div class="card"><h2>时间线 <span class="meta">研究和工作上发生的事，最新的在上面${since ? ' · 蓝色是你上次来（' + B.ago(since, d.now) + '）之后的' : ''}</span></h2>
    <div class="pad"><div class="chips">${chip('全部 ' + all.length, '')}${chip('研究', '研究')}${chip('工作', '工作')}</div>${eventList(evs.slice(0, 300), since, d.now)}
    ${evs.length > 300 ? `<div class="small">只显示最近 300 条</div>` : ''}</div></div>`;
}

// ============================================================ 概况: since your last visit, what is going on, what waits for you
function waitingItems(d) {
  const p = d.project || {}, name = d.name, answers = d.answers || {}, proj = {name, title: p.title};
  const todos = openTodos(d.todos).sort((a, b) => kindRank(a) - kindRank(b)).map(t => ({...t, answer: answers[String(t.id)] || null}));
  const pending = d.attempts.filter(a => pendingOf(a).includes('结论待确认') && typeof a.meta.id === 'string');
  const cands = work(d).decisions.filter(r => r.meta.status === '候选' || r.meta.status == null);
  return [
    ...cands.map(r => decisionItem(proj, {id: r.meta.id, title: r.meta.title || r.meta.id, decision: decLine(r), answer: answers['d:' + r.meta.id] || null, state: r.state}, d.now)),
    ...pending.map(a => confirmItem(proj, {id: a.meta.id, title: a.meta.title || attName(a), proposed: a.meta.proposed,
      conclusion: ((a.sections || {})['结论和原因'] || '').slice(0, 600), answer: answers['a:' + a.meta.id] || null}, d.now)),
    ...todos.map(t => todoItem(t, proj, d.now))];
}
const handoffNext = t => { const m = String((t.sections || {})['交接'] || '').match(/^\s*[-*]?\s*下一步\s*[:：]\s*(.+)$/m); return m ? m[1].trim() : ''; };

// ============================================================ 任务: the board of task cards
function tabTasks(d, r) {
  const W = work(d), name = d.name;
  if (!W.tasks.length) return `<div class="card"><h2>任务</h2><div class="empty">还没有任务卡。项目会话把一件具体的事（讨论、执行、写作、分析、审查、调试）写成 board/tasks/&lt;id&gt;.md，
    一张卡由一个会话从头做到尾，做完写交接；这里按状态排成看板。格式见 BOARD_FORMAT.md 的「tasks/」一节。</div></div>`;
  const sel = r.item ? W.task(r.item) : null;
  const card = t => `<a class="tcard${sel === t ? ' on' : ''}" href="${plink(name, 'tasks', t.meta.id)}">
      <div class="tc1"><span class="tag2">${esc(t.meta.kind || '?')}</span><b>${esc(taskTitle(t))}</b></div>
      ${handoffNext(t) ? `<div class="small">下一步：${esc(handoffNext(t))}</div>` : ''}
      <div class="small faint">${esc(t.meta.id)}${t.meta.updated ? ' · ' + isoAgo(t.meta.updated, d.now) + '更新' : ''}${listOf(t.meta.sessions).length ? ' · ' + esc(listOf(t.meta.sessions).slice(-1)[0]) : ''}</div></a>`;
  const cols = TASK_COLS.map(st => { const ts = W.tasks.filter(t => t.meta.status === st);
    return `<div class="kcol"><div class="kh">${taskPill(st)} <span class="small">${ts.length}</span></div>${ts.map(card).join('') || '<div class="small faint">—</div>'}</div>`; }).join('');
  const closed = W.tasks.filter(t => !TASK_COLS.includes(t.meta.status));
  const done = closed.length ? `<details class="box" id="tasks-closed"${sel && closed.includes(sel) ? ' open' : ''}><summary>完成、暂停、放弃 ${closed.length} 张</summary>
    <div class="kdone">${closed.map(t => `<a class="li${sel === t ? ' on' : ''}" href="${plink(name, 'tasks', t.meta.id)}">${taskPill(t.meta.status)} ${esc(taskTitle(t))}
      <span class="small">${t.meta.updated ? isoAgo(t.meta.updated, d.now) : ''}</span></a>`).join('')}</div></details>` : '';
  return `<div class="kanban">${cols}</div>${done}${sel ? taskDetail(d, sel) : r.item ? `<div class="card"><div class="empty">找不到任务「${esc(r.item)}」。</div></div>` : ''}`;
}
// How to start a card: the user opens a conversation in the project's folder in the App (the folder is named after
// the project directory) and says 「做 <id>」; that conversation runs board task-begin. Only for cards that can start.
const TASK_STARTABLE = ['待开始', '可以继续', '进行中'];
function taskBeginHint(d, t) {
  const m = t.meta;
  if (!TASK_STARTABLE.includes(m.status) || typeof m.id !== 'string') return '';
  const say = '做 ' + m.id;
  return `在 App 的「${esc(d.name)}」文件夹里新开一个对话，说「<code>${esc(say)}</code>」<button class="copy" data-c="${esc(say)}">复制</button>` +
    (m.status === '进行中' ? '<div class="small">已经有对话在做的话，在那个对话里接着说。</div>' : '');
}
function taskDetail(d, t) {
  const m = t.meta, name = d.name, W = work(d), sec = t.sections || {};
  const sessions = listOf(m.sessions).map(String), live = (S.sessions || {sessions: []}).sessions;
  const sessHtml = sessions.map(s => { const x = live.find(y => y.id === s || y.name === s);
    return `<span class="chip">${esc(s)}${x ? ` · ${x.live ? (x.status === 'busy' ? '在工作' : '空闲') : '已结束，可接上'}` : ''}</span>`; }).join('');
  const runs = W.runsOfTask(m.id);
  const kv = (k, v) => v ? `<div class="kv2"><span>${k}</span><div>${v}</div></div>` : '';
  const order = ['要做什么', '完成标准', '交接', '结果'];
  const body = bodySections(t.body, order);
  return `<div class="card taskd"><h2>${esc(taskTitle(t))} ${taskPill(m.status)} <span class="tag2">${esc(m.kind || '?')}</span>
      <span class="meta right">${esc(t.file)}</span></h2><div class="pad">
    ${kv('开工', taskBeginHint(d, t))}
    ${kv('挂在', m.about ? (refHref(d, m.about) ? `<a href="${refHref(d, m.about)}">${esc(placeLabel(d, m.about) || m.about)}</a>` : esc(m.about)) : '')}
    ${kv('允许改', listOf(m.scope).map(x => `<code>${esc(x)}</code>`).join(' '))}
    ${kv('交付物', listOf(m.deliver).map(esc).join('；'))}
    ${kv('预算', esc(m.budget || ''))}
    ${kv('等这些做完', listOf(m.after).map(x => W.task(x) ? `<a href="${plink(name, 'tasks', x)}">${esc(taskTitle(W.task(x)))}</a>` : esc(x)).join('、'))}
    ${kv('做过它的会话', sessHtml)}
    ${kv('时间', [m.created ? '建于 ' + esc(B.day(m.created) || m.created) : '', m.updated ? isoAgo(m.updated, d.now) + '更新' : ''].filter(Boolean).join(' · '))}
    ${runs.length ? kv('它的运行', runs.map(x => `<a href="${plink(name, 'runs', runKey(x))}">${esc(runKey(x))}</a> ${B.pill((x.run || {}).status)} ${statePill(x.state)}`).join('<br>')) : ''}
    </div><div class="pad taskbody">${refs(body, d)}${stepRefs(d, 'k:' + m.id)}</div></div>`;
}

// ============================================================ 讨论与决定
function decisionItem(p, x, now) {
  return `<li><div><span class="pill t-todo">采纳决定</span> <span class="ttext">${esc(x.title)}</span>
      <a class="more" href="${plink(p.name, 'decisions', x.id)}">看这条决定 →</a></div>
    ${x.decision ? `<div class="ctx">${esc(x.decision)}</div>` : ''}
    ${replyBox(p.name, 'd:' + x.id, ['采纳', '不采纳'], null, x.answer, now)}</li>`;
}
function tabDecisions(d, r) {
  const W = work(d), name = d.name, item = r.item || '';
  const minutes = W.minutes;
  if (!W.decisions.length && !minutes.length) return `<div class="card"><h2>讨论与决定</h2><div class="empty">还没有决定和讨论纪要。
    你在对话里定下的事，会话会写成 board/decisions/ 里的「候选」，你在这里点「采纳」才生效；一段讨论告一段落时会写一篇纪要（docs/，kind: 纪要）。</div></div>`;
  const groups = [['候选', '等你采纳'], ['采纳', '有效'], ['不采纳', '没采纳'], ['已替代', '已被替代']];
  const selDec = !item.startsWith('doc:') ? W.decision(item) : null, selDoc = item.startsWith('doc:') ? minutes.find(x => x.id === item.slice(4)) : null;
  const li = (href, on, html) => `<a class="li${on ? ' on' : ''}" href="${href}">${html}</a>`;
  const list = groups.map(([st, label]) => { const xs = W.decisions.filter(x => x.state === st);
    return xs.length ? `<div class="tgroup">${label} · ${xs.length}</div>` + xs.map(x => li(plink(name, 'decisions', x.meta.id), x === selDec,
      `<div class="row1">${decPill(x.state)}<span class="name">${esc(x.meta.title || x.meta.id)}</span></div><div class="row2"><span>${esc(B.day(x.meta.date) || '')}</span>${x.state === '已替代' && W.replacedBy.get(x.meta.id) ? `<span>被「${esc(W.replacedBy.get(x.meta.id).meta.title || W.replacedBy.get(x.meta.id).meta.id)}」替代</span>` : ''}</div>`)).join('') : ''; }).join('') +
    (minutes.length ? `<div class="tgroup">讨论纪要 · ${minutes.length}</div>` + minutes.map(x => li(plink(name, 'decisions', 'doc:' + x.id), x === selDoc,
      `<div class="row1">${docBadge(x)}<span class="name">${esc(x.title)}</span></div><div class="row2"><span>${B.ago(x.mtime, d.now)}更新</span></div>`)).join('') : '');
  let detail;
  if (selDoc) {
    detail = `<div class="detail"><h2>${esc(selDoc.title)} <span class="tag2">纪要</span>${docBadge(selDoc)}</h2>
      <div class="dmeta"><span>属于：${selDoc.about ? esc(placeLabel(d, selDoc.about) || selDoc.about) : '整个项目'}</span><span>${esc(selDoc.file)}</span>
        <span><a href="${plink(name, 'docs', selDoc.id)}">在文档页打开（可以标记看过 / 确认）→</a></span></div>
      <div class="md">${refs(B.md(selDoc.body), d)}</div></div>`;
  } else if (selDec) {
    const m = selDec.meta, by = W.replacedBy.get(m.id), ans = (d.answers || {})['d:' + m.id];
    const rep = listOf(m.replaces).map(x => W.decision(String(x)) ? `<a href="${plink(name, 'decisions', x)}">${esc(W.decision(String(x)).meta.title || x)}</a>` : esc(x)).join('、');
    const src = m.source ? (String(m.source).startsWith('docs/') ? `<a href="${plink(name, 'decisions', 'doc:' + String(m.source).slice(5).replace(/\.md$/, '') + '.md')}">${esc(m.source)}</a>` : esc(m.source)) : '';
    detail = `<div class="detail"><h2>${esc(m.title || m.id)} ${decPill(selDec.state)}</h2>
      <div class="dmeta"><span>${esc(B.day(m.date) || '没有日期')}</span>${m.about ? `<span>属于：${refHref(d, m.about) ? `<a href="${refHref(d, m.about)}">${esc(placeLabel(d, m.about) || m.about)}</a>` : esc(m.about)}</span>` : ''}
        ${src ? `<span>出处：${src}</span>` : ''}${rep ? `<span>替代了：${rep}</span>` : ''}${by ? `<span>被 <a href="${plink(name, 'decisions', by.meta.id)}">${esc(by.meta.title || by.meta.id)}</a> 替代</span>` : ''}<span>${esc(selDec.file)}</span></div>
      ${(m.status === '候选' || m.status == null) ? `<div class="sec first"><h3>采纳这条决定吗</h3>${replyBox(name, 'd:' + m.id, ['采纳', '不采纳'], null, ans || null, d.now)}
        <div class="small">点了「采纳」立即生效：之后每个新会话的事实包里都会有它。</div></div>` : ''}
      ${refs(bodySections(selDec.body, ['决定', '理由', '影响']), d)}${stepRefs(d, 'd:' + m.id)}</div>`;
  } else {
    detail = `<div class="detail"><h2>讨论与决定</h2><div class="pad md"><p>左边是这个项目定下的事。会话只能写「候选」；你点「采纳」才生效，生效的决定会写进每个新会话开头读到的事实包，所以不会被忘掉。</p>
      <p>新决定替代旧决定时，旧的会标成「已被替代」，文件保留。讨论纪要记的是怎么讨论到这个决定的。</p></div></div>`;
  }
  return `<div class="md2"><div class="list"><div class="items">${list}</div></div>${detail}</div>`;
}

// ============================================================ 会话 (sessions of one project, or all of them)
const SESSION_BIG = 150000, SESSION_IDLE = 3 * 86400;
function sessionRows(list, now, showProject) {
  if (!list.length) return '<div class="empty">没有会话。</div>';
  const rows = list.map(s => {
    const flags = [], big = s.live && s.context > SESSION_BIG;   // an ended session needs no advice: it is greyed instead
    if (big) flags.push('<span class="pill t-todo" title="每次调用都要把整段对话读一遍；换一个新会话，从文件里接上">上下文大，建议换新</span>');
    if (!s.live && s.last_active && now - s.last_active > SESSION_IDLE) flags.push('<span class="pill t-off">闲置超过 3 天</span>');
    if (s.project && !s.task) flags.push('<span class="pill t-other" title="没有挂在任何任务卡上">临时</span>');
    const st = s.live ? (s.status === 'busy' ? '<span class="pill t-run">在工作</span>' : '<span class="pill t-ok">空闲</span>') : '<span class="pill t-other">已结束</span>';
    const pct = s.context ? Math.min(100, Math.round(100 * s.context / 400000)) : 0;
    return `<tr class="${!s.live ? 'ended' : big ? 'ctxbig' : ''}"><td><b>${esc(s.title || s.name)}</b>${s.title && s.name !== s.title ? `<div class="small">${esc(s.name)}</div>` : ''}
        <div class="small faint">${esc(s.id.slice(0, 8))}${s.kind ? ' · ' + esc(s.kind) : ''}</div></td>
      <td>${st} ${flags.join(' ')}</td>
      ${showProject ? `<td>${s.project ? `<a href="${plink(s.project)}">${esc(s.project)}</a>` : `<span class="small">${esc(s.cwd || '')}</span>`}</td>` : ''}
      <td>${s.task && s.project ? `<a href="${plink(s.project, 'tasks', s.task)}">${esc(s.task)}</a>` : '<span class="faint">—</span>'}</td>
      <td class="n">${kTokens(s.context)}<div class="bar"><i style="width:${pct}%"></i></div>${s.compactions ? `<div class="small">压缩过 ${s.compactions} 次</div>` : ''}</td>
      <td class="n">${kTokens(s.tokens_today)}<div class="small">7 天 ${kTokens(s.tokens_7d)}</div></td>
      <td>${s.last_active ? B.ago(s.last_active, now) : '—'}</td>
      <td class="small">${s.live ? '在 App 里点它' : s.resumable ? `<code>claude --resume ${esc(s.id.slice(0, 8))}…</code>` : ''}</td></tr>`;
  }).join('');
  return `<div class="scroll"><table class="mt sess"><tr><th>会话</th><th>状态</th>${showProject ? '<th>项目</th>' : ''}<th>任务卡</th><th>上下文</th><th>今天的 token</th><th>最后活动</th><th>接上</th></tr>${rows}</table></div>`;
}
function ensureSessions(force) {
  const fresh = S.sessions && Date.now() - (S.sessionsAt || 0) < 30000;
  if ((fresh && !force) || S.sessionsLoading) return;
  S.sessionsLoading = true;
  getJSON('/api/sessions').then(x => { S.sessions = x; }).catch(() => {}).finally(() => {
    S.sessionsLoading = false; S.sessionsAt = Date.now();
    const r = route();
    if ((r.page === 'sessions' || (r.page === 'project' && ['sessions', 'tasks'].includes(r.tab))) && !busy()) render();
  });
}
const SESSION_NOTE = `<div class="pad small">只读会话的元数据（名字、所在目录、上下文大小、token 数），不读对话内容。上下文 = 最近一次调用读入的 token 数：对话越长，每次调用越贵，
  开着的会话超过 15 万标黄，建议让它写好交接、换一个新会话；已结束的会话整行灰色，不再提示。数据来自 a100 上的 <code>claude agents</code> 和会话记录，每分钟更新；其他服务器上的会话（例如 h100）暂时不在这里。</div>`;
function tabSessions(d) {
  ensureSessions();
  const x = S.sessions;
  if (!x) return '<div class="empty">正在读取会话…</div>';
  const mine = x.sessions.filter(s => s.project === d.name);
  const tok = mine.reduce((n, s) => n + (s.tokens_7d || 0), 0);
  return `<div class="card"><h2>这个项目的会话 <span class="meta">${mine.length} 个 · 7 天共 ${kTokens(tok)} token</span></h2>${sessionRows(mine, x.now, false)}${SESSION_NOTE}</div>`;
}
function pageSessions() {
  ensureSessions();
  const x = S.sessions;
  if (!x) return '<h1 class="pt">会话</h1><div class="empty">正在读取会话…</div>';
  const live = x.sessions.filter(s => s.live), tok = x.sessions.reduce((n, s) => n + (s.tokens_today || 0), 0);
  const big = x.sessions.filter(s => s.live && s.context > SESSION_BIG).length, temp = x.sessions.filter(s => s.project && !s.task).length;
  return `<h1 class="pt">会话</h1>
    <div class="summary"><div class="stat"><div class="k">开着的会话</div><div class="v">${live.length}</div><div class="s">在工作 ${live.filter(s => s.status === 'busy').length}</div></div>
      <div class="stat"><div class="k">今天用掉的 token</div><div class="v">${kTokens(tok)}</div><div class="s">含重复读入的上下文</div></div>
      <div class="stat"><div class="k">上下文太大</div><div class="v">${big}</div><div class="s">超过 15 万，建议换新</div></div>
      <div class="stat"><div class="k">临时会话</div><div class="v">${temp}</div><div class="s">在项目里但没挂任务卡</div></div></div>
    ${x.error ? `<div class="notice">${esc(x.error)}</div>` : ''}
    <div class="card"><h2>所有会话 <span class="meta">最近 14 天</span></h2>${sessionRows(x.sessions, x.now, true)}${SESSION_NOTE}</div>`;
}

// ============================================================ 记录维护: record problems, kept out of your inbox
function tabUpkeep(d) {
  const items = [...(d.checks || []).map(x => [x.where, x.what]), ...d.errors.map(e => [e.file, e.error])];
  if (!items.length) return '<div class="card"><h2>记录维护</h2><div class="empty">记录检查没有发现问题。</div></div>';
  const by = new Map();
  for (const [w, t] of items) { if (!by.has(w)) by.set(w, []); by.get(w).push(t); }
  const rows = [...by].sort((a, b) => b[1].length - a[1].length).map(([w, ts]) => `<tr><td><code>${esc(w)}</code><div class="small">${ts.length} 条</div></td>
    <td style="white-space:normal"><ul class="plain">${ts.map(t => `<li>${esc(t)}</li>`).join('')}</ul></td></tr>`).join('');
  return `<div class="card"><h2>记录维护 <span class="meta">${items.length} 条，${by.size} 个文件</span></h2>
    <div class="pad small">这些是记录格式上的问题，由写记录的会话修，不需要你决定。会话改 board/ 里的文件时会当场看到这些提示；
      想一次修完，在项目会话里说「修记录检查」。格式见 /shared/ssd/dchen/_shared/BOARD_FORMAT.md。</div>
    <div class="scroll"><table class="mt" style="margin:4px 8px 8px">${rows}</table></div></div>`;
}
