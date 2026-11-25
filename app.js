// The console: routing, sidebar and pages. Needs common.js (Board); no other dependencies.
// Routes (hash):  #/overview (今天)  #/sessions  #/gpu  #/todo  #/new  #/p/<project>/<tab>[/<item>][?filters]
//   project tabs, research view: home (概况), method, flow, runs, examples, setup, docs
//                 work view: tasks, decisions, sessions, timeline, upkeep (see work.js)
const B = Board, esc = B.esc, $ = id => document.getElementById(id);

// ============================================================ data
const S = {status: null, projects: null, guide: null, detail: {}, fetchedAt: {}, loading: {}, missing: {}, offline: false};
const ui = {exampleOpen: false, attGroup: {}, runAllCols: false,  // view state kept across refreshes
  pick: {}, draft: {}, editing: {}, err: {}, direct: {}, renaming: false};                   // replies being written, the rename box
// the console's only writes: replies (board/answers.jsonl), marks and paper ticks (board/marks.jsonl) and display names;
// the server checks they come from this page
const postJSON = (url, body) => fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Board-Write': '1'},
  body: JSON.stringify(body)}).then(async r => { const d = await r.json().catch(() => ({})); if (!r.ok) throw new Error(d.error || 'HTTP ' + r.status); return d; });
const getJSON = url => fetch(url).then(r => r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status)));

// ============================================================ routing
const RESEARCH_TABS = [['home', '概况'], ['method', '方法'], ['flow', '实验'], ['runs', '运行'], ['examples', '样例'], ['setup', '实验设置'], ['docs', '文档'], ['papers', '论文']];
const WORK_TABS = [['tasks', '任务'], ['decisions', '讨论与决定'], ['sessions', '会话'], ['timeline', '时间线'], ['upkeep', '记录维护']];
const TABS = [...RESEARCH_TABS, ...WORK_TABS];
const PAGES = ['overview', 'sessions', 'gpu', 'todo', 'guide', 'design', 'new'];

function route() {
  const raw = location.hash.replace(/^#/, '');
  const [path, q = ''] = raw.split('?');
  const parts = path.split('/').filter(Boolean).map(s => { try { return decodeURIComponent(s); } catch (e) { return s; } });
  const query = Object.fromEntries(new URLSearchParams(q));
  if (parts[0] === 'p' && parts[1]) {
    let tab = parts[2], item = parts[3] ?? null;
    if (tab === 'experiments') {  // old addresses: the experiments tab became the roadmap, the roadmap became the flow
      tab = 'flow';
      if (item != null) item = 'a:' + item;
      else if (query.track) { item = 't:' + query.track; delete query.track; }
    }
    if (tab === 'roadmap') tab = 'flow';
    if (tab === 'overview') tab = 'home';
    return {page: 'project', name: parts[1], tab: TABS.some(t => t[0] === tab) ? tab : 'home', item, query};
  }
  return {page: PAGES.includes(parts[0]) ? parts[0] : 'overview', query};
}
const plink = (name, tab = 'home', item = null, query = null) => {
  const q = query && Object.entries(query).filter(([, v]) => v != null && v !== '');
  return B.projectUrl(name, tab, item) + (q && q.length ? '?' + new URLSearchParams(q) : '');
};
// a link written in records (flow.json, todos.jsonl): "tab[/item][?q]"; route() maps the old forms
function linkFor(name, link) {
  if (typeof link !== 'string' || !link.trim()) return null;
  const [path, q = ''] = link.trim().split('?');
  const [tab, ...rest] = path.split('/').filter(Boolean);
  return '#/p/' + encodeURIComponent(name) + '/' + encodeURIComponent(tab || 'overview') +
    (rest.length ? '/' + encodeURIComponent(rest.join('/')) : '') + (q ? '?' + q : '');
}
// old addresses: /#projects, /#gpu (and project.html?p= redirects here)
if (location.hash === '#projects' || location.hash === '' || location.hash === '#') history.replaceState(null, '', '#/overview');
else if (location.hash === '#gpu') history.replaceState(null, '', '#/gpu');

// ============================================================ helpers
const attKey = a => String(a.meta.id || a.file.split('/').pop().replace(/\.md$/, ''));
const attName = a => attKey(a).replace(/^\d{4}-\d{2}-\d{2}-/, '');
const attDate = a => B.day(a.meta.date) || (attKey(a).match(/^\d{4}-\d{2}-\d{2}/) || [''])[0];
const runKey = r => String((r.run || {}).id || r.id);
const idOf = x => typeof x.meta.id === 'string' ? x.meta.id : x.file;
const pendingOf = a => (a.needs_input || []).filter(x => x !== '预期');
const fileUrl = (name, rel) => '/files/' + encodeURIComponent(name) + '/' + rel.split('/').map(encodeURIComponent).join('/');
const dateKey = a => String(a.meta.date || a.meta.started || attDate(a) || '');
const gpusText = x => Array.isArray(x.gpus) && x.gpus.length ? ' 卡 ' + x.gpus.join(',') : '';
const statusOf = r => typeof r.meta.status === 'string' && r.meta.status ? r.meta.status : '未写';
function statusCounts(records) {  // [[status, n]], most frequent first
  const m = new Map();
  for (const r of records) m.set(statusOf(r), (m.get(statusOf(r)) || 0) + 1);
  return [...m].sort((a, b) => b[1] - a[1]);
}
const countLine = pairs => pairs.map(([s, n]) => `${esc(s)} <b>${n}</b>`).join(' · ');
const needsLabel = needs => needs.includes('结论待确认') ? '待你确认结论' : needs.includes('结论和原因') ? '待你填写结论和原因' : needs.join('、');
const ROLE = {train: '训练', dev: '开发', calib: '校准', test: '测试', other: '其他'};
const BETTER = {higher: '越高越好', lower: '越低越好'};
const KIND = {blocking: '卡着进度', decision: '等你决定', whenever: '不急'};
const openTodos = list => (list || []).filter(t => !t.done);

// markdown body split into the text before the first "## " and [[title, text]] sections
function sectionsOf(body) {
  const parts = String(body || '').split(/^## /m), out = [];
  for (const p of parts.slice(1)) { const nl = p.indexOf('\n'); out.push([(nl < 0 ? p : p.slice(0, nl)).trim(), nl < 0 ? '' : p.slice(nl + 1).trim()]); }
  return {pre: parts[0].trim(), list: out};
}

// per-project lookups, computed once per fetched snapshot
function ctx(d) {
  if (d._ctx) return d._ctx;
  const setup = B.setupIndex(d.setup), disp = B.parseDisplay((d.project || {}).display);
  const runById = new Map(d.runs.map(r => [runKey(r), r]));
  const attById = new Map(d.attempts.map(a => [attKey(a), a]));
  const trackById = new Map(d.tracks.filter(t => typeof t.meta.id === 'string').map(t => [t.meta.id, t]));
  const verById = new Map(d.versions.filter(v => typeof v.meta.id === 'string').map(v => [v.meta.id, v]));
  const tree = B.roadmapTree(d);
  // runs of an attempt: its runs list plus runs that name it as their attempt
  const runsOf = a => {
    const ids = new Set((Array.isArray(a.meta.runs) ? a.meta.runs : []).map(String));
    for (const r of d.runs) if ((r.run || {}).attempt === attKey(a)) ids.add(runKey(r));
    return [...ids].map(id => runById.get(id) || {missing: id});
  };
  const attemptOf = r => attById.get((r.run || {}).attempt) || d.attempts.find(a => Array.isArray(a.meta.runs) && a.meta.runs.map(String).includes(runKey(r))) || null;
  const homeOf = new Map();  // track id -> version id it hangs under
  for (const v of tree.versions) for (const n of v.tracks) homeOf.set(idOf(n.track), idOf(v.version));
  const label = r => (setup.runMethod.get(runKey(r)) || {}).name || runKey(r);
  const groupName = g => !g ? '其他指标' : (setup.datasets.get(g) || {}).name || g;
  const metricName = c => (setup.metrics.get(c) || {}).name || c;
  return d._ctx = {setup, disp, runById, attById, trackById, verById, tree, runsOf, attemptOf, homeOf, label, groupName, metricName};
}
// "版本 v1 › 支线 X › 尝试 Y" with links; any part may be missing
function crumbs(d, {version, track, attempt}) {
  const n = d.name, out = [];
  if (version) out.push(`<a href="${plink(n, 'flow', 'v:' + idOf(version))}">版本 ${esc(idOf(version))}</a>`);
  if (track) out.push(`<a href="${plink(n, 'flow', 't:' + idOf(track))}">${esc(track.meta.title || idOf(track))}</a>`);
  if (attempt) out.push(`<a href="${plink(n, 'flow', 'a:' + attKey(attempt))}">${esc(attName(attempt))}</a>`);
  return out.length ? `<div class="crumbs">${out.join(' <span class="small">›</span> ')}</div>` : '';
}
// labels for a list of runs: the method name, with the run id added where two runs would read the same
function labelsFor(c, runs) {
  const n = new Map();
  for (const r of runs) n.set(c.label(r), (n.get(c.label(r)) || 0) + 1);
  return r => n.get(c.label(r)) > 1 && c.label(r) !== runKey(r) ? `${c.label(r)} · ${runKey(r)}` : c.label(r);
}
// where a run sits in the roadmap
function placeOfRun(d, r) {
  const c = ctx(d), a = c.attemptOf(r), t = a && c.trackById.get(a.meta.track), v = t && c.verById.get(c.homeOf.get(idOf(t)));
  return {attempt: a, track: t, version: v};
}

// ============================================================ sidebar
function todoCount() {
  const pj = S.projects;
  if (!pj) return null;
  const unanswered = xs => (xs || []).filter(x => !x.answer).length;  // what still needs your reply
  return pj.projects.filter(p => stageOf(p) === 0)  // paused and archived projects are set aside
    .reduce((n, p) => n + unanswered(p.pending) + unanswered(p.todos) + unanswered(p.decisions_candidate), 0) + unanswered(openTodos(pj.todos));
}
// projects by status: 进行中 first, then 暂停, then 已归档 (anything else counts as 进行中)
const STAGE = {'暂停': 1, '已归档': 2};
const stageOf = p => STAGE[p.status] ?? 0;
function renderSide(r) {
  const pj = S.projects ? S.projects.projects : null;
  const free = S.status ? B.freeCount(S.status) : null, todo = todoCount();
  const archOpen = !!($('side-arch') && $('side-arch').open);
  const item = (href, label, cnt, on) => `<a class="sitem${on ? ' on' : ''}" href="${href}"><span class="lbl">${label}</span>` +
    (cnt != null ? `<span class="cnt">${cnt}</span>` : '') + '</a>';
  const proj = p => `<a class="sitem${r.page === 'project' && r.name === p.name ? ' on' : ''}" href="${plink(p.name)}" ` +
    `title="${esc(p.title)}${p.status ? ' · ' + esc(p.status) : ''}"><span class="dot d-${B.tone(p.status)}"></span><span class="lbl">${esc(p.title)}</span></a>`;
  const list = pj ? [...pj].sort((a, b) => String(a.title).localeCompare(String(b.title))) : [];
  const by = k => list.filter(p => stageOf(p) === k);
  const arch = by(2), current = arch.some(p => r.page === 'project' && r.name === p.name);
  $('side').innerHTML = `<a class="brand" href="#/overview">${esc(B.APP_NAME)}</a>` +
    item('#/overview', '今天', null, r.page === 'overview') +
    item('#/sessions', '会话', S.sessions ? S.sessions.sessions.filter(s => s.live).length + ' 个' : null, r.page === 'sessions') +
    item('#/gpu', 'GPU', free != null ? free + ' 空闲' : null, r.page === 'gpu') +
    item('#/todo', '待办', todo || null, r.page === 'todo') +
    item('#/guide', '使用说明', null, r.page === 'guide') +
    item('#/design', '设计思路', null, r.page === 'design') +
    '<div class="shead">项目</div>' +
    (list.length ? by(0).map(proj).join('') : `<div class="faint" style="padding:4px 10px">${pj ? '还没有项目' : '加载中…'}</div>`) +
    (by(1).length ? '<div class="shead">暂停</div>' + by(1).map(proj).join('') : '') +
    (arch.length ? `<details class="sarch" id="side-arch"${archOpen || current ? ' open' : ''}><summary class="shead">已归档 ${arch.length}</summary>${arch.map(proj).join('')}</details>` : '') +
    `<a class="sitem new${r.page === 'new' ? ' on' : ''}" href="#/new"><span class="lbl">＋ 新建项目</span></a>`;
}

// ============================================================ main
let lastHash = null, lastView = null, copying = false;
function render() {
  const r = route();
  renderSide(r);
  const open = [...document.querySelectorAll('#main details[open][id]')].map(x => x.id);  // keep what the user expanded
  const scrolls = [...document.querySelectorAll('#main .items')].map(x => x.scrollTop);   // and how far each list is scrolled
  const sameView = [r.page, r.name, r.tab].join('|') === lastView;
  let html;
  if (r.page === 'gpu') html = pageGpu();
  else if (r.page === 'sessions') html = pageSessions();
  else if (r.page === 'todo') html = pageTodo();
  else if (r.page === 'new') html = pageNew();
  else if (r.page === 'guide') html = pageGuide();
  else if (r.page === 'design') html = pageDesign();
  else if (r.page === 'project') html = pageProject(r);
  else html = pageOverview();
  setMain(html);
  for (const id of open) { const el = document.getElementById(id); if (el) el.open = true; }
  if (sameView) document.querySelectorAll('#main .items').forEach((x, i) => { if (scrolls[i]) x.scrollTop = scrolls[i]; });
  fitExample();
  // a linked setup entry or paper is highlighted on every render and scrolled to once; other pages start at the top
  const anchor = r.page === 'project' && (r.tab === 'setup' || r.tab === 'papers') && r.item != null && document.querySelector(`[data-anchor="${CSS.escape(r.item)}"]`);
  if (anchor) anchor.classList.add('flash');
  if (location.hash !== lastHash && !(r.page === 'project' && !S.detail[r.name] && !S.missing[r.name])) {  // wait for the data before scrolling
    lastHash = location.hash;
    // another page or tab starts at the top; picking another item in the same tab keeps the list where it is
    lastView = [r.page, r.name, r.tab].join('|');
    const det = document.querySelector('#main .detail');
    if (anchor) anchor.scrollIntoView({block: 'center'});
    else if (!sameView) window.scrollTo(0, 0);
    else if (det && det.getBoundingClientRect().top < 0) det.scrollIntoView({block: 'start'});
  }
  document.title = titleFor(r);
}
// Put html into #main without reloading an open figure: when the new page shows the same figure (same file,
// same mtime), everything around its frame is replaced level by level and the frame itself stays where it is,
// so whatever the user did inside it survives the minutely refresh. Otherwise the page is simply replaced.
function setMain(html) {
  const main = $('main'), old = main.querySelector('iframe.figframe');
  if (!old) { main.innerHTML = html; return; }
  const tpl = document.createElement('div');
  tpl.innerHTML = html;
  const neu = tpl.querySelector('iframe.figframe');
  const path = (el, top) => { const p = []; for (; el && el !== top; el = el.parentNode) p.unshift(el); return p; };
  const po = path(old, main), pn = neu ? path(neu, tpl) : [];
  if (!neu || neu.dataset.key !== old.dataset.key || po.length !== pn.length || po.some((x, i) => x.tagName !== pn[i].tagName)) {
    main.innerHTML = html; return;
  }
  let po_ = main, pn_ = tpl;
  po.forEach((keep, i) => {
    const from = pn[i], kids = [...pn_.childNodes], at = kids.indexOf(from);
    for (const c of [...po_.childNodes]) if (c !== keep) c.remove();
    keep.before(...kids.slice(0, at));
    keep.after(...kids.slice(at + 1));
    if (keep !== old) {  // an ancestor of the frame takes the new attributes; the frame keeps its own (same src)
      for (const a of [...keep.attributes]) if (!from.hasAttribute(a.name)) keep.removeAttribute(a.name);
      for (const a of [...from.attributes]) keep.setAttribute(a.name, a.value);
    }
    po_ = keep; pn_ = from;
  });
}
function titleFor(r) {
  const app = B.APP_NAME;
  if (r.page === 'gpu') return (S.status ? `(${B.freeCount(S.status)} 空) ` : '') + app;
  if (r.page === 'todo') return `待办 · ${app}`;
  if (r.page === 'sessions') return `会话 · ${app}`;
  if (r.page === 'new') return `新建项目 · ${app}`;
  if (r.page === 'guide') return `使用说明 · ${app}`;
  if (r.page === 'design') return `设计思路 · ${app}`;
  if (r.page === 'project') { const d = S.detail[r.name]; return `${(d && d.project && d.project.title) || r.name} · ${app}`; }
  return `今天 · ${app}`;
}

// ============================================================ 今天: what waits for you, what is stuck, what is moving
const UPDATE_KIND = {plan: '方案', versions: '版本', tracks: '支线', attempts: '实验', runs: '运行', docs: '文档', figures: '图解', todos: '待办', setup: '实验设置', method: '方法', tasks: '任务', decisions: '决定', papers: '论文'};
function projectCard(p, now) {
  const act = p.active_runs || [], tasks = p.tasks_open || [];
  const seen = lastSeenOf(p.name), upd = Object.entries(p.updated_kinds || {}).filter(([, t]) => seen != null && t > seen).map(([k]) => UPDATE_KIND[k]).filter(Boolean);
  const bad = act.filter(r => r.state === '已中断' || r.state === '状态未知').length;
  const latest = (p.latest_onepager || [])[0];
  return `<a class="pcard" href="${plink(p.name)}">
    <div class="t"><b>${esc(p.title)}</b>${B.pill(p.status)}</div>
    <div class="small">${esc(p.name)}${p.current_version ? ' · 当前版本 ' + esc(p.current_version) : ''} · 常驻入口${hasEntry(p.name) ? '在运行' : '未运行'}</div>
    ${p.question ? `<div class="c q2">${esc(p.question.length > 90 ? p.question.slice(0, 89) + '…' : p.question)}</div>` : ''}
    ${latest ? `<div class="small"><b>最新：</b>${esc(latest.so)}</div>` : p.conclusion ? `<div class="small"><b>结论：</b>${esc(p.conclusion)}</div>` : ''}
    <div class="n"><span>进行中的任务 <b>${tasks.length}</b></span><span>正在跑 <b>${act.length}</b>${bad ? ` <span class="pill t-todo">${bad} 个状态存疑</span>` : ''}</span>
      <span>实验 <b>${p.n_attempts ?? 0}</b></span></div>
    ${tasks.length ? `<ul class="hl small">${tasks.slice(0, 3).map(t => `<li>${taskPill(t.status)} ${esc(t.title)}</li>`).join('')}</ul>` : ''}
    ${p.stale || p.n_errors ? `<div class="alerts">${p.stale ? `<span class="w">数据是 ${B.ago(p.scanned_at, now)}的，最近一次读取失败</span>` : ''}
      ${p.n_errors ? `<span class="w">${p.n_errors} 个记录文件读取有问题</span>` : ''}</div>` : ''}
    <div class="small foot">${seen == null ? '还没在这个浏览器打开过' : upd.length ? `你上次来之后：${esc(upd.join('、'))}有更新` : '你上次来之后没有新变化'} · 最近动态 ${B.ago(p.mtime, now)}</div></a>`;
}
// what waits for you across projects, most urgent first: blocking to-dos, candidate decisions, conclusions to confirm,
// decision to-dos, the rest. One line each: the reply itself happens where the item lives.
function inboxItems(pj, now) {
  const out = [];
  const line = (p, pill, text, href, go) => `<li><span class="ip" title="${esc(p ? p.title : '跨项目')}">${esc(p ? p.title : '跨项目')}</span>` +
    `<span class="it">${pill} ${esc(text)}</span><a class="ig" href="${href}">${go} →</a></li>`;
  for (const p of pj.projects) {
    if (stageOf(p) > 0) continue;  // paused or archived: set aside until resumed
    for (const x of p.decisions_candidate || [])
      out.push({rank: x.answer ? 5 : 1, html: line(p, '<span class="pill t-todo">采纳决定</span>', x.title, plink(p.name, 'decisions', x.id), '采纳或不采纳')});
    for (const a of (p.pending || []).filter(x => x.needs.includes('结论待确认') && typeof x.id === 'string'))
      out.push({rank: a.answer ? 5 : 2, html: line(p, '<span class="pill t-todo">确认结论</span>', `尝试「${a.title || a.id}」${a.proposed ? '，建议：' + a.proposed : ''}`,
        plink(p.name, 'flow', 'a:' + a.id), '看一页纸再确认')});
    for (const t of p.todos || [])
      out.push({rank: t.answer ? 5 : t.kind === 'blocking' ? 0 : t.kind === 'decision' ? 3 : 4,
        html: line(p, `<span class="pill t-${t.kind === 'whenever' ? 'other' : 'todo'}">${KIND[t.kind] || '待办'}</span>`, t.text, '#/todo', '回复')});
  }
  for (const t of openTodos(pj.todos)) out.push({rank: t.answer ? 5 : t.kind === 'blocking' ? 0 : 3,
    html: line(null, `<span class="pill t-${t.kind === 'whenever' ? 'other' : 'todo'}">${KIND[t.kind] || '待办'}</span>`, t.text, '#/todo', '回复')});
  return out.sort((a, b) => a.rank - b.rank);
}
function stuckItems(pj, now) {
  const out = [];
  for (const p of pj.projects) {
    const aside = stageOf(p) > 0;  // a paused or archived project only shows runs that still claim to be running
    for (const r of p.active_runs || []) {
      if (r.state === '已中断') out.push(`<li>${statePill(r.state)} <a href="${plink(p.name, 'runs', r.id)}">${esc(p.title)} · 运行 ${esc(r.id)}</a>
        <span class="small">机器上找不到它的进程，收尾程序会把记录改成被中断</span></li>`);
      else if (r.state === '状态未知') out.push(`<li>${statePill(r.state)} <a href="${plink(p.name, 'runs', r.id)}">${esc(p.title)} · 运行 ${esc(r.id)}</a>
        <span class="small">${!r.heartbeat && r.liveness === 'absent' ? '手写的记录，进程已经不在，可能跑完了没写结果：让项目会话核对后收尾'
          : (r.heartbeat ? '心跳 ' + B.ago(r.heartbeat, now) : '没有心跳') + '，' + esc(shortHost(r.server || '?')) + ' 上看不到进程或连不上'}</span></li>`);
    }
    for (const t of aside ? [] : p.tasks_open || []) {
      if (t.status === '可以继续') out.push(`<li>${taskPill(t.status)} <a href="${plink(p.name, 'tasks', t.id)}">${esc(p.title)} · ${esc(t.title)}</a>
        <span class="small">等的东西到了，开一个会话说「做 ${esc(t.id)}」</span></li>`);
      else if (t.status === '等你') out.push(`<li>${taskPill(t.status)} <a href="${plink(p.name, 'tasks', t.id)}">${esc(p.title)} · ${esc(t.title)}</a>
        <span class="small">${t.next ? esc(t.next) : '卡在你这里，看任务卡的交接'}</span></li>`);
    }
    if (!aside && (p.unprocessed || []).length) out.push(`<li><span class="pill t-todo">回复未处理</span> <a href="#/todo">${esc(p.title)} · ${p.unprocessed.length} 条回复</a>
      <span class="small">在项目会话里说「处理回复」</span></li>`);
    if (p.stale) out.push(`<li><span class="pill t-bad">读取失败</span> <a href="${plink(p.name)}">${esc(p.title)}</a> <span class="small">数据是 ${B.ago(p.scanned_at, now)}的</span></li>`);
  }
  return out;
}
function pageOverview() {
  const pj = S.projects;
  if (!pj) return '<div class="empty">加载中…</div>';
  const now = pj.now;
  const list = [...pj.projects].sort((a, b) => stageOf(a) - stageOf(b) || (b.mtime || 0) - (a.mtime || 0));
  const free = S.status ? B.freeCount(S.status) : null;
  const running = list.reduce((n, p) => n + (p.active_runs || []).length, 0);
  const sc = pj.scan || {}, notes = [];
  if (sc.running_since && now - sc.running_since > 5) notes.push(`正在扫描共享盘，已 ${B.span(now - sc.running_since)}；下面是上一次的结果`);
  if (sc.error) notes.push(`最近一次扫描失败（${esc(sc.error)}），下面是 ${B.ago(sc.finished, now)}的结果`);
  const group = k => list.filter(p => stageOf(p) === k);
  const cards = ps => `<div class="pcards">${ps.map(p => projectCard(p, now)).join('')}</div>`;
  const active = group(0), paused = group(1), arch = group(2);
  const inbox = inboxItems(pj, now), open = inbox.filter(x => x.rank < 5), stuck = stuckItems(pj, now);
  const SHOW = 12;
  const inboxHtml = `<div class="card"><h2>等你决定 <span class="meta">${open.length} 条还没回复${inbox.length > open.length ? ` · ${inbox.length - open.length} 条已回复、等会话处理` : ''}</span>
      <a class="meta right" href="#/todo">全部待办 →</a></h2>
    ${open.length ? `<ul class="inbox">${open.slice(0, SHOW).map(x => x.html).join('')}</ul>` +
      (open.length > SHOW ? `<div class="pad small"><a href="#/todo">还有 ${open.length - SHOW} 条 →</a></div>` : '') : '<div class="empty">没有等你决定的事。</div>'}</div>`;
  const stuckHtml = `<div class="card"><h2>卡住了 <span class="meta">${stuck.length} 条</span></h2>${stuck.length ? `<ul class="tlist">${stuck.join('')}</ul>` : '<div class="empty">没有卡住的事。</div>'}</div>`;
  const body = !list.length
    ? `<div class="card"><div class="empty">还没有项目。在 ${esc(sc.root || '共享盘')}/&lt;项目名&gt;/board/ 下写好 project.json，一分钟内就会出现在这里。</div></div>`
    : `<h3 class="gh">正在推进</h3>` + (active.length ? cards(active) : '<div class="card"><div class="empty">没有进行中的项目。</div></div>') +
      (paused.length ? `<h3 class="gh">暂停 ${paused.length} 个 <span class="faint">恢复方法见「使用说明」</span></h3>` + cards(paused) : '') +
      (arch.length ? `<details class="box ovarch" id="ov-arch"><summary>已归档 ${arch.length} 个 <span class="faint">结束或放弃的项目，记录都还在</span></summary>${cards(arch)}</details>` : '');
  return `<h1 class="pt">今天</h1>
    <div class="summary">
      <a class="stat" href="#/todo"><div class="k">等你决定</div><div class="v">${open.length}</div><div class="s">所有项目合计</div></a>
      <div class="stat"><div class="k">卡住了</div><div class="v">${stuck.length}</div><div class="s">运行状态存疑、任务等人</div></div>
      <div class="stat"><div class="k">正在跑</div><div class="v">${running}</div><div class="s">按运行记录</div></div>
      <a class="stat" href="#/gpu"><div class="k">空闲的卡</div><div class="v">${free ?? '—'}</div><div class="s">所有服务器合计</div></a>
    </div>` + notes.map(t => `<div class="notice">${t}</div>`).join('') + `<div class="homegrid"><div>${inboxHtml}</div><div>${stuckHtml}</div></div>` + body;
}

// ============================================================ todo
const KIND_ORDER = {blocking: 0, decision: 1};
const kindRank = t => KIND_ORDER[t.kind] ?? 2;
const isoAgo = (iso, now) => iso ? B.ago(Date.parse(iso) / 1000, now) : '';
// the reply part of a to-do or of an attempt waiting for confirmation
function replyBox(project, id, choices, recommend, answer, now, progress) {
  const key = project + '|' + id;
  if (answer && !ui.editing[key]) {
    const stage = progress && progress.state === '已领取' ? '<span class="pill t-run">会话已领取，正在处理</span>'
      : progress && progress.state === '受阻' ? `<span class="pill t-bad">受阻</span> ${esc(progress.result || '')}`
      : answer.mode === '直接处理' ? '<span class="pill t-run" title="a100 上的派活程序每 5 分钟看一次，会起一个会话专门处理这条">几分钟内自动处理</span>' : '';
    return `<div class="ans"><span class="pill t-run">已回复</span> ${answer.answer ? `<b>${esc(answer.answer)}</b>` : ''}` +
      `${answer.text ? `<span class="atext">${answer.answer ? '　' : ''}${esc(answer.text)}</span>` : ''}${stage ? ' ' + stage : ''}
      <div class="small">${isoAgo(answer.at, now)}回复 · ${stage ? '' : '等会话处理：在这个项目的会话里说「处理回复」 · '}<a href="#" data-edit="${esc(key)}">改回复</a></div></div>`;
  }
  const pick = key in ui.pick ? ui.pick[key] : answer ? answer.answer : null;
  const draft = key in ui.draft ? ui.draft[key] : answer ? answer.text || '' : '';
  return `<div class="reply" data-key="${esc(key)}" data-p="${esc(project)}" data-id="${esc(id)}">
    ${choices.length ? `<div class="opts">${choices.map(o => `<button class="opt${pick === o ? ' on' : ''}" data-opt="${esc(o)}">${esc(o)}` +
      `${o === recommend ? '<span class="rec">建议</span>' : ''}</button>`).join('')}</div>` : ''}
    <textarea rows="2" data-draft="${esc(key)}" placeholder="${choices.length ? '补充说明（可选）' : '写下你的回复'}">${esc(draft)}</textarea>
    <div class="ract"><button class="send" data-send="${esc(key)}">提交回复</button>
      ${project !== '_shared' ? `<label class="direct" title="不勾：等你在项目会话里说「处理回复」。勾上：几分钟内由一个会话自动处理这条，结果写回待办"><input type="checkbox" data-direct="${esc(key)}"${ui.direct[key] ? ' checked' : ''}> 直接处理</label>` : ''}
      ${answer ? `<a href="#" data-cancel="${esc(key)}">取消</a>` : ''}<span class="rerr">${esc(ui.err[key] || '')}</span></div></div>`;
}
function todoItem(t, project, now) {
  const pname = project ? project.name : '_shared';
  const href = project ? linkFor(project.name, t.link) : null;
  const opts = Array.isArray(t.options) ? t.options.filter(o => typeof o === 'string' && o.trim()) : [];
  return `<li><div>${t.kind && KIND[t.kind] ? `<span class="pill t-${t.kind === 'whenever' ? 'other' : 'todo'}">${KIND[t.kind]}</span> ` : ''}` +
    `<span class="ttext">${esc(t.text)}</span>` + (href ? ` <a class="more" href="${href}">相关记录 →</a>` : '') + '</div>' +
    (t.context ? `<div class="ctx md">${B.md(String(t.context))}</div>` : '') +
    replyBox(pname, String(t.id), opts, t.recommend, t.answer, now, t) +
    `<div class="small">${t.where ? esc(t.where) + ' · ' : ''}${isoAgo(t.created, now)}记下${t.by ? ' · 由 ' + esc(t.by) : ''}</div></li>`;
}
// an attempt whose conclusion waits for the user: the drafted conclusion and the four choices
function confirmItem(p, a, now) {
  return `<li><div><span class="pill t-todo">确认结论</span> <span class="ttext">尝试「${esc(a.title || a.id)}」</span>
      <a class="more" href="${plink(p.name, 'flow', 'a:' + a.id)}">看这次尝试 →</a></div>
    ${a.conclusion ? `<div class="ctx md">${B.md(a.conclusion)}</div>` : ''}
    ${replyBox(p.name, 'a:' + a.id, ['成功', '部分成功', '失败', '放弃'], a.proposed, a.answer, now)}</li>`;
}
// every item that waits for (or got) a reply: to-dos, conclusions to confirm, candidate decisions
function todoEntries(pj) {
  const out = [];
  const add = (p, type, id, text, extra) => out.push({p, type, id, key: (p ? p.name : '_shared') + '|' + id, text, ...extra});
  for (const p of pj.projects) {
    for (const t of p.todos || []) add(p, 'todo', String(t.id), t.text, {t, answer: t.answer, rank: kindRank(t), href: linkFor(p.name, t.link)});
    for (const a of (p.pending || []).filter(x => x.needs.includes('结论待确认') && typeof x.id === 'string'))
      add(p, 'confirm', 'a:' + a.id, `尝试「${a.title || a.id}」的结论`, {a, answer: a.answer, rank: 1, href: plink(p.name, 'flow', 'a:' + a.id)});
    for (const x of p.decisions_candidate || [])
      add(p, 'decision', 'd:' + x.id, `决定「${x.title}」`, {x, answer: x.answer, rank: 1, href: plink(p.name, 'decisions', x.id)});
  }
  for (const t of openTodos(pj.todos)) add(null, 'todo', String(t.id), t.text, {t, answer: t.answer, rank: kindRank(t), href: null});
  return out;
}
const ENTRY_LABEL = {confirm: ['确认结论', 'todo'], decision: ['采纳决定', 'todo']};
function entryPill(e) {
  if (e.type !== 'todo') return `<span class="pill t-${ENTRY_LABEL[e.type][1]}">${ENTRY_LABEL[e.type][0]}</span>`;
  return e.t.kind && KIND[e.t.kind] ? `<span class="pill t-${e.t.kind === 'whenever' ? 'other' : 'todo'}">${KIND[e.t.kind]}</span>` : '<span class="pill t-other">待办</span>';
}
// where an answered item stands: 受阻 / 会话已领取 / 自动处理 / waiting for "处理回复"
function entryStage(e) {
  const st = e.type === 'todo' ? e.t.state : null;
  if (st === '受阻') return {k: 'blocked', html: `<span class="pill t-bad">受阻</span> <span class="small">${esc(e.t.result || '')}</span>`};
  if (st === '已领取') return {k: 'taken', html: '<span class="pill t-run">会话已领取，正在处理</span>'};
  if (e.answer && e.answer.mode === '直接处理') return {k: 'auto', html: '<span class="pill t-run">几分钟内自动处理</span>'};
  return {k: 'wait', html: '<span class="pill t-other">等你说「处理回复」</span>'};
}
const STAGES = [['blocked', '受阻'], ['taken', '会话正在处理'], ['auto', '自动处理中'], ['wait', '等你说「处理回复」']];
function entryFull(e, now) {
  if (e.type === 'confirm') return confirmItem(e.p, e.a, now);
  if (e.type === 'decision') return decisionItem(e.p, e.x, now);
  return todoItem(e.t, e.p, now);
}
function answeredRow(e, now) {
  if (ui.editing[e.key]) return entryFull(e, now);
  const a = e.answer, st = entryStage(e);
  return `<li class="arow"><div>${entryPill(e)} <span class="ttext">${esc(e.text)}</span>${e.href ? ` <a class="more" href="${e.href}">相关记录 →</a>` : ''}</div>
    <div class="ans">你的回复：${a.answer ? `<b>${esc(a.answer)}</b>` : ''}${a.text ? `${a.answer ? '　' : ''}${esc(a.text)}` : ''}
      <span class="small">· ${isoAgo(a.at, now)}回复 · <a href="#" data-edit="${esc(e.key)}">改回复</a></span> ${st.html}</div></li>`;
}
function pageTodo() {
  const pj = S.projects;
  if (!pj) return '<div class="empty">加载中…</div>';
  const now = pj.now, q = route().query || {}, f = q.s || '';
  const every = todoEntries(pj), setAside = every.filter(e => e.p && stageOf(e.p) > 0);
  const all = every.filter(e => !(e.p && stageOf(e.p) > 0));  // paused and archived projects are folded away below
  const open = all.filter(e => !e.answer || ui.editing[e.key]), answered = all.filter(e => e.answer && !ui.editing[e.key]);
  const tally = {blocking: 0, decision: 0, whenever: 0};
  for (const e of open) tally[e.type !== 'todo' ? 'decision' : e.t.kind in tally ? e.t.kind : 'whenever']++;
  const byStage = k => answered.filter(e => entryStage(e).k === k);
  const doneList = [...pj.projects.flatMap(p => (p.todos_done || []).map(t => ({t, p}))), ...(pj.todos || []).filter(t => t.done).map(t => ({t, p: null}))]
    .filter(x => Date.parse(x.t.done) / 1000 >= now - 7 * 86400).sort((a, b) => String(b.t.done).localeCompare(String(a.t.done)));
  const chip = (label, val, n) => `<a class="chip${f === val ? ' on' : ''}" href="#/todo${val ? '?s=' + val : ''}">${label}${n != null ? ' ' + n : ''}</a>`;
  const chips = `<div class="chips filters2">${chip('全部', '', null)}${chip('还没回复', 'open', open.length)}${chip('已回复', 'answered', answered.length)}` +
    STAGES.map(([k, l]) => byStage(k).length ? chip(l, k, byStage(k).length) : '').join('') + chip('最近 7 天办完', 'done', doneList.length) + '</div>';
  // 1. still waiting for your reply, by project, most urgent first
  const groups = [];
  for (const p of [...pj.projects.filter(x => stageOf(x) === 0), null]) {
    const items = open.filter(e => e.p === p).sort((a, b) => a.rank - b.rank);
    const missing = p ? (p.pending || []).filter(x => !x.needs.includes('结论待确认')).length : 0;
    if (!items.length && !missing) continue;
    const html = items.map(e => entryFull(e, now));
    if (missing) html.push(`<li><span class="pill t-other">缺结论</span> <a href="${plink(p.name, 'flow', null, {todo: 1})}">${missing} 条尝试缺「结论和原因」</a>
      <div class="where2">这要由会话补写，在项目会话里说「补写结论」</div></li>`);
    groups.push({p, html, top: items.length ? items[0].rank : 9});
  }
  groups.sort((a, b) => (a.p === null) - (b.p === null) || a.top - b.top || b.html.length - a.html.length);
  const card = (title, meta, body) => `<div class="card"><h2>${title} <span class="meta">${meta}</span></h2>${body}</div>`;
  const openHtml = groups.length ? groups.map(g => card(g.p ? `<a href="${plink(g.p.name)}">${esc(g.p.title)}</a>` : '跨项目', `${g.html.length} 条`,
    `<ul class="tlist">${g.html.join('')}</ul>`)).join('') : '<div class="card"><div class="empty">没有等你回复的事。</div></div>';
  // 2. answered, waiting for a session: by project, then by where each one stands
  const ansGroups = [...pj.projects, null].map(p => ({p, items: answered.filter(e => e.p === p && (!STAGES.some(([k]) => k === f) || entryStage(e).k === f))})).filter(g => g.items.length);
  const ansHtml = ansGroups.length ? ansGroups.map(g => {
    const rows = STAGES.map(([k, l]) => { const xs = g.items.filter(e => entryStage(e).k === k);
      return xs.length ? `<li class="sub">${l} · ${xs.length}</li>` + xs.map(e => answeredRow(e, now)).join('') : ''; }).join('');
    const waiting = g.items.filter(e => entryStage(e).k === 'wait').length;
    return card(g.p ? `<a href="${plink(g.p.name)}">${esc(g.p.title)}</a>` : '跨项目', `${g.items.length} 条已回复` +
      (waiting && g.p ? ` · 在这个项目的会话里说「处理回复」处理 ${waiting} 条` : ''), `<ul class="tlist">${rows}</ul>`);
  }).join('') : '<div class="card"><div class="empty">没有已回复、等会话处理的事。</div></div>';
  // 3. done in the last 7 days, newest first
  const doneHtml = doneList.length ? `<div class="card"><ul class="tlist">${doneList.map(({t, p}) => `<li><div><span class="small faint">${p ? esc(p.title) : '跨项目'}</span>　<s>${esc(t.text)}</s></div>` +
    `${t.answer ? `<div class="small">你的回复：${esc([t.answer.answer, t.answer.text].filter(Boolean).join('　'))}</div>` : ''}` +
    `${t.result ? `<div class="res">结果：${esc(t.result)}</div>` : ''}<div class="small">${isoAgo(t.done, now)}办完</div></li>`).join('')}</ul></div>`
    : '<div class="card"><div class="empty">最近 7 天没有办完的待办。</div></div>';
  const show = k => !f || f === k || (k === 'answered' && STAGES.some(([s]) => s === f));
  return `<h1 class="pt">待办</h1>
    <div class="summary">${[['blocking', '卡着进度'], ['decision', '等你决定'], ['whenever', '不急']].map(([k, l]) =>
      `<div class="stat"><div class="k">${l}</div><div class="v">${tally[k]}</div><div class="s">还没回复</div></div>`).join('')}
      <a class="stat" href="#/todo?s=answered"><div class="k">已回复、等会话处理</div><div class="v">${answered.length}</div>
        <div class="s">${STAGES.filter(([k]) => byStage(k).length).map(([k, l]) => `${l} <b>${byStage(k).length}</b>`).join(' · ') || '—'}</div></a></div>
    ${chips}
    ${show('open') ? `<h3 class="gh">还没回复 · ${open.length}</h3>${openHtml}` : ''}
    ${show('answered') ? `<h3 class="gh">已回复，等会话处理 · ${answered.length}</h3>${ansHtml}` : ''}
    ${show('done') ? `<h3 class="gh">最近 7 天办完 · ${doneList.length}</h3>${doneHtml}` : ''}
    ${setAside.length && !f ? `<details class="box" id="todo-aside"><summary>暂停或归档的项目 · ${setAside.length} 条 <span class="faint">先放着，恢复项目后再处理；不算在上面的数字里</span></summary>` +
      [...new Set(setAside.map(e => e.p))].map(p => card(`<a href="${plink(p.name)}">${esc(p.title)}</a> ${B.pill(p.status)}`, `${setAside.filter(e => e.p === p).length} 条`,
        `<ul class="tlist">${setAside.filter(e => e.p === p).map(e => e.answer && !ui.editing[e.key] ? answeredRow(e, now) : entryFull(e, now)).join('')}</ul>`)).join('') + '</details>' : ''}
    <p class="small">回复存在项目的 board/answers.jsonl。不勾「直接处理」的，等你在项目会话里说「处理回复」；勾了的，a100 的派活程序几分钟内起一个会话处理。处理完的移到「最近 7 天办完」并写明结果。</p>`;
}

function pageNew() {
  return `<h1 class="pt">新建项目</h1><div class="card"><div class="pad md">
    <p>控制台只读，不在页面上建项目。请在任意一台服务器上的 Claude 会话里说：</p>
    <pre><code>新建项目 &lt;名字&gt;</code></pre>
    <p>会话会在共享盘 <code>/shared/ssd/dchen/&lt;名字&gt;/</code> 建好目录和 <code>board/</code>，一分钟内左侧栏就会出现这个项目。记录格式见 <code>/shared/ssd/dchen/_shared/BOARD_FORMAT.md</code>。</p></div></div>`;
}

// ============================================================ resident entries and the guide
const hasEntry = name => !!(S.projects && (S.projects.entries || []).includes(name));
function entryBadge(name) {
  if (!S.projects) return '';
  return hasEntry(name) ? `<span class="pill t-ok" title="a100 上的 tmux 会话 rc-${esc(name)} 在运行；在 Claude App 或 claude.ai/code 里点「${esc(name)}」开会话">常驻入口在运行</span>`
    : `<a class="pill t-other" href="#/guide" title="在 a100 会话里输入：! ~/bin/rc-start ${esc(name)}">常驻入口未运行</a>`;
}
function pageGuide() {
  if (S.guide == null) {
    fetch('/guide.md').then(r => r.ok ? r.text() : '（读不到 docs/guide.md）').then(t => { S.guide = t; if (route().page === 'guide') render(); });
    S.guide = '';
  }
  const list = S.projects ? S.projects.projects : [];
  const rows = list.map(p => `<tr><td><a href="${plink(p.name)}">${esc(p.title)}</a><div class="small">${esc(p.name)}</div></td>
    <td>${entryBadge(p.name)}</td><td>${hasEntry(p.name) ? `<span class="small">在 App 或 claude.ai/code 里点「${esc(p.name)}」</span>`
      : `<code>! ~/bin/rc-start ${esc(p.name)}</code>`}</td></tr>`).join('');
  return `<h1 class="pt">使用说明</h1>
    <div class="card"><h2>各项目的常驻入口 <span class="meta">每 5 秒刷新</span></h2>
      ${rows ? `<div class="scroll"><table class="mt" style="margin:6px 8px 8px"><tr><th>项目</th><th>入口</th><th>怎么用</th></tr>${rows}</table></div>` : '<div class="empty">还没有项目。</div>'}</div>
    <div class="card"><div class="pad md guide">${S.guide ? B.md(S.guide.replace(/^# .*\n/, '')) : '加载中…'}</div></div>`;
}

// ============================================================ GPU page
const GPU_LABEL = {free: '空闲', busy: '有人在用', full: '显存将满'};
const gpuState = g => B.isFree(g) ? 'free' : g.mem_total && g.mem_used / g.mem_total > 0.9 ? 'full' : 'busy';
const errText = e => /退出码 3/.test(e || '') ? 'nvidia-smi 无法运行（显卡驱动没有加载），需要管理员处理' : e;
const gb = mb => mb == null ? '–' : (mb / 1024).toFixed(1);
const dur = s => s == null ? '' : s < 3600 ? Math.round(s / 60) + ' 分钟' : s < 86400 ? (s / 3600).toFixed(1) + ' 小时' : (s / 86400).toFixed(1) + ' 天';
const shortHost = name => String(name).replace(/^xtra/, '');
function activeRuns() {
  return S.projects ? S.projects.projects.flatMap(p => (p.active_runs || []).map(r => ({...r, project: p.name, title: p.title}))) : [];
}
const modelsOf = gpus => [...new Set(gpus.map(g => `${g.name} · ${Math.round(g.mem_total / 1024)} GB`))].join(' / ');
// one card of the overview grid: free memory in large type, a usage bar, who is on it
function gpuCell(s, g, me, live) {
  const st = gpuState(g), users = [...new Set(g.procs.map(p => p.user))];
  const mine = users.includes(me), pct = g.mem_total ? Math.round(100 * g.mem_used / g.mem_total) : 0;
  const who = users.length ? users.map(u => u === me ? '<b class="me">我</b>' : esc(u)).join('、')
    : st === 'free' ? '没人用' : '看不到进程';
  const tip = `${s.name} 卡 ${g.index} · 已用 ${gb(g.mem_used)} / ${gb(g.mem_total)} GB · 利用率 ${g.util ?? '–'}% · ${g.temp ?? '–'}°C` +
    (users.length ? ' · ' + users.join('、') : '');
  return `<td><div class="gcell ${live ? st : 'off'}${mine ? ' mine' : ''}" title="${esc(tip)}">
    <div class="gc1"><span>卡 ${esc(g.index)}</span><span>${g.util ?? '–'}%</span></div>
    <div class="gc2"><b>${gb(g.mem_total - g.mem_used)}</b> GB 空</div>
    <div class="gbar"><i style="width:${pct}%"></i></div>
    <div class="gc3">${who}</div></div></td>`;
}
function gpuGrid(servers, me) {
  const width = Math.max(1, ...servers.map(s => (s.gpus || []).length));
  const rows = servers.map(s => {
    const gpus = s.gpus || [], live = s.status === 'ok', nFree = gpus.filter(g => gpuState(g) === 'free').length;
    const head = `<th class="gsrv"><a href="#/gpu" data-goto="srv-${esc(s.name)}">${esc(shortHost(s.name))}</a>
      <div class="small">${gpus.length ? esc(modelsOf(gpus)) : ''}</div></th>`;
    if (!live && !gpus.length) return `<tr>${head}<td class="small" colspan="${width + 1}">${s.status === 'pending' ? '正在连接…' : '读不到：' + esc(errText(s.error))}</td></tr>`;
    const free = live ? `<td class="gfree"><b class="${nFree ? 'okc' : ''}">${nFree}</b><span class="small"> / ${gpus.length} 空闲</span></td>`
      : `<td class="gfree small">读不到<br>（上次的数据）</td>`;
    return `<tr>${head}${free}${gpus.map(g => gpuCell(s, g, me, live)).join('')}${'<td></td>'.repeat(width - gpus.length)}</tr>`;
  }).join('');
  return `<section class="card"><h2>显卡一览 <span class="meta">大字是剩余显存；绿 = 空闲，橙 = 有人在用，红 = 显存用了九成以上；蓝框 = 有你的任务。点服务器名看明细</span></h2>
    <div class="scroll"><table class="ggrid">${rows}</table></div></section>`;
}
function pageGpu() {
  const d = S.status;
  if (!d) return S.offline ? '<div class="empty">连不上控制台：请确认 Mac 能连到 xtraa100（校园网 / VPN）</div>' : '<div class="empty">加载中…</div>';
  const runs = activeRuns(), me = d.me, servers = B.sortServers(d.servers);
  let total = 0, okServers = 0, html = '';
  const users = {}, freeBy = [];
  for (const s of servers) {
    const gpus = s.gpus || [], live = s.status === 'ok', freeCards = [];
    if (live) okServers++;
    let rows = '';
    for (const g of gpus) {
      const st = gpuState(g);
      if (live) { total++; if (st === 'free') freeCards.push(g); }
      const pct = g.mem_total ? Math.round(100 * g.mem_used / g.mem_total) : 0;
      const procs = g.procs.map(p => {
        const u = users[p.user] = users[p.user] || {mem: 0, gpus: new Set(), where: new Set()};
        u.mem += p.mem || 0; u.gpus.add(s.name + '#' + g.index); u.where.add(shortHost(s.name));
        return `<div class="proc"><span class="user${p.user === me ? ' me' : ''}">${esc(p.user)}</span>
          <span class="small">${gb(p.mem)} GB · ${dur(p.elapsed)} · PID ${esc(p.pid)}</span>
          <span class="cmd" title="${esc(p.cmd)}">${esc(p.cmd)}</span></div>`;
      }).join('') || (st === 'free' ? '<span class="small">—</span>' : '<span class="small">有显存占用但看不到进程（可能在容器内）</span>');
      const tags = B.runsOnGpu(runs, s.name, g).map(r => {
        const lost = r.state === '已中断' || r.state === '状态未知';
        return `<a class="tag${lost ? ' warn' : ''}" href="${plink(r.project, 'runs', r.id)}" title="tmux ${esc(r.tmux || '?')} · PID ${esc(r.pid ?? '?')}${r.heartbeat ? ' · 心跳 ' + B.ago(r.heartbeat, d.now) : ''}">` +
          `${esc(r.title)} · ${esc(r.id)}${r.state && r.state !== '运行中' ? '（' + esc(r.state) + '）' : ''}</a>`;
      }).join('');
      rows += `<tr><td class="n">${esc(g.index)}</td><td><span class="pill ${st}">${GPU_LABEL[st]}</span></td>
        <td class="n">${gb(g.mem_used)} / ${gb(g.mem_total)} GB<div class="bar"><i style="width:${pct}%;background:var(--${st})"></i></div></td>
        <td class="n">${g.util ?? '–'}%</td><td class="n">${g.temp ?? '–'}°C</td><td>${tags ? `<div>${tags}</div>` : ''}${procs}</td></tr>`;
    }
    if (freeCards.length) freeBy.push({s, cards: freeCards});
    const color = live ? 'var(--free)' : s.status === 'pending' ? 'var(--busy)' : 'var(--full)';
    html += `<section class="card" id="srv-${esc(s.name)}"><h2><span class="dotc" style="background:${color}"></span>${esc(s.name)}
      <span class="meta">${gpus.length ? esc(modelsOf(gpus)) : esc(s.host)}</span>
      <span class="meta right">${live ? `空闲 ${freeCards.length} / ${gpus.length}` : ''}${s.updated ? ' · 更新于 ' + B.ago(s.updated, d.now) : ''}</span></h2>`;
    if (s.status === 'pending') html += '<div class="empty">正在连接…</div>';
    if (s.status === 'error') {
      html += `<div class="err">读取失败：<code>${esc(errText(s.error))}</code>${gpus.length ? '（下面是上一次成功时的数据）' : ''}</div>`;
      if (/Permission denied/.test(s.error || '')) html += `<div class="hint">这台机器还没有配置免密登录：<code>ssh-copy-id ${esc(s.host)}</code></div>`;
    }
    if (gpus.length) html += `<div class="scroll" style="${live ? '' : 'opacity:.55'}"><table class="gt">
      <tr><th>卡</th><th>状态</th><th>显存</th><th>利用率</th><th>温度</th><th>使用者 / 进程</th></tr>${rows}</table></div>`;
    html += '</section>';
  }
  const nFree = freeBy.reduce((n, x) => n + x.cards.length, 0);
  const freeHtml = `<section class="card lead"><h2>现在能跑的卡 <span class="big">${nFree}</span><span class="meta">张完全空闲 / 在线的共 ${total} 张</span></h2>` +
    (nFree ? freeBy.map(({s, cards}) => {
      const ids = cards.map(g => g.index).join(',');
      return `<div class="slot"><div class="where"><b>${esc(s.name)}</b><span class="small">${esc(modelsOf(cards))}</span></div>
        <div class="gchips"><span class="small">卡号</span>${cards.map(g => `<span class="gchip">${esc(g.index)}</span>`).join('')}</div>
        <div class="cmds"><code>CUDA_VISIBLE_DEVICES=${esc(ids)}</code>
          <button class="copy" data-c="CUDA_VISIBLE_DEVICES=${esc(ids)}">复制</button>
          <button class="copy" data-c="ssh ${esc(s.host)}">复制 ssh 命令</button></div></div>`;
    }).join('') : '<div class="empty">现在没有完全空闲的卡。下面的一览里大字是每张卡的剩余显存，可以挑余量大的叠加使用。</div>') + '</section>';
  const list = Object.entries(users).sort((a, b) => b[1].mem - a[1].mem);
  const usersHtml = list.length ? `<table class="gt"><tr><th>用户</th><th>占用卡数</th><th>显存合计</th><th>所在服务器</th></tr>` +
    list.map(([u, v]) => `<tr><td><span class="user${u === me ? ' me' : ''}">${esc(u)}</span></td><td class="n">${v.gpus.size}</td><td class="n">${gb(v.mem)} GB</td>
      <td>${esc([...v.where].join('、'))}</td></tr>`).join('') + '</table>' : '<div class="empty">当前没有人在用卡。</div>';
  const down = d.servers.length - okServers;
  return `<div class="gtop"><h1>GPU</h1><span class="sub">${okServers} / ${d.servers.length} 台服务器在线${down ? '（' + down + ' 台读不到）' : ''} · 每 ${d.interval} 秒更新</span>
      <button id="gpu-refresh">立即刷新</button></div>` + freeHtml + gpuGrid(servers, me) +
    `<h3 class="gh">各服务器明细</h3>${html}<details class="box" id="gpu-users"><summary>谁在用（按占用显存排序）</summary><div class="scroll">${usersHtml}</div></details>`;
}

// ============================================================ project page
function ensureDetail(name, force = false) {
  const fresh = S.detail[name] && Date.now() - (S.fetchedAt[name] || 0) < 30000;
  if ((fresh && !force) || S.loading[name]) return;
  S.loading[name] = true;
  let changed = false;
  fetch('/api/project/' + encodeURIComponent(name)).then(async res => {
    if (res.status === 404) { changed = !S.missing[name]; S.missing[name] = true; delete S.detail[name]; return; }
    if (!res.ok) return;
    const d = await res.json(), old = S.detail[name];
    const same = k => JSON.stringify(old[k]) === JSON.stringify(d[k]);
    changed = !old || old.scanned_at !== d.scanned_at || !same('stale') || !same('scan') || !same('project') || !same('answers') || !same('marks')
      || !same('paper_marks') || !same('papers_elsewhere');
    S.detail[name] = d; S.missing[name] = false;
  }).catch(() => {}).finally(() => {
    S.loading[name] = false; S.fetchedAt[name] = Date.now();
    const r = route();
    if (changed && r.page === 'project' && r.name === name && !busy()) render();
  });
}

function pageProject(r) {
  ensureDetail(r.name);
  const d = S.detail[r.name];
  if (!d) return S.missing[r.name]
    ? `<h1 class="pt">${esc(r.name)}</h1><div class="card"><div class="empty">没有这个项目（或者它的 board/project.json 还没写好）。</div></div>`
    : `<div class="empty">正在读取 ${esc(r.name)}…</div>`;
  const p = d.project || {};
  const unread = docItems(d).filter(x => x.state === 'unread' || x.state === 'changed').length;
  const papers = paperItems(d), papersRead = papers.filter(p => p.read).length, mustUnread = papers.filter(p => p.must && !p.read).length;
  const W = work(d), since = lastSeenOf(d.name), fresh = since == null ? 0 : timelineOf(d).filter(e => e.t > since).length;
  const openTasks = W.tasks.filter(t => TASK_COLS.includes(t.meta.status)).length, cands = W.decisions.filter(x => x.state === '候选').length;
  const mySessions = S.sessions ? S.sessions.sessions.filter(x => x.project === d.name && x.live).length : null;
  const counts = {home: fresh ? fresh + ' 新' : null, method: methodIndex(d).steps.length || null, flow: d.attempts.length || null, setup: null, runs: d.runs.length,
    examples: (d.example.probe_head || []).length || null,
    docs: docItems(d).length ? (unread ? `${unread} 未看 / ${docItems(d).length}` : docItems(d).length) : null,
    papers: papers.length ? (papers.length - papersRead ? `${papers.length - papersRead} 未读 / ${papers.length}` : papers.length) : null,
    tasks: openTasks || null, decisions: cands ? cands + ' 待采纳' : W.decisions.length || null, sessions: mySessions || null, timeline: null,
    upkeep: (d.checks || []).length + d.errors.length || null};
  const nameHtml = ui.renaming
    ? `<span class="rename"><input id="rename-input" data-p="${esc(d.name)}" maxlength="24" value="${esc(p.title || d.name)}" placeholder="控制台里显示的名字">
        <button id="rename-save">保存</button>${p.full_title ? '<button id="rename-reset">恢复原名</button>' : ''}<button id="rename-cancel">取消</button>
        <span class="small" id="rename-err">最多 24 个字，只改控制台里的显示，不改目录名</span></span>`
    : `<h1>${esc(p.title || d.name)}</h1><button class="lnkbtn" id="rename-open" title="改控制台里显示的名字">改名</button>`;
  const head = `<div class="phead">${nameHtml}${B.pill(p.status)}
      ${p.current_version ? `<span class="small">当前版本 <b>${esc(p.current_version)}</b></span>` : ''}
      ${papers.length ? `<a class="small" href="${plink(d.name, 'papers')}">论文 ${papers.length} 篇 · 读过 ${papersRead}${mustUnread ? ` · <b>必读未读 ${mustUnread}</b>` : ''}</a>` : ''}
      ${entryBadge(d.name)}
      <span class="small right">数据读取于 ${B.ago(d.scanned_at, d.now)} · 每分钟刷新</span></div>
    ${p.full_title && !ui.renaming ? `<div class="small fulltitle">${esc(d.name)} · 原名：${esc(p.full_title)}</div>` : `<div class="small fulltitle">${esc(d.name)}</div>`}
    ${projectHero(d)}
    <nav class="ptabs">${[['研究', RESEARCH_TABS], ['工作', WORK_TABS]].map(([g, tabs]) => `<span class="pgrp">${g}</span>` +
      tabs.map(([k, label]) => `<a class="ptab${k === r.tab ? ' on' : ''}${k === 'upkeep' ? ' quiet' : ''}" href="${plink(d.name, k)}">${label}` +
      (counts[k] ? `<span class="cnt">${counts[k]}</span>` : '') + '</a>').join('')).join('<span class="psep"></span>')}</nav>`;
  const body = {home: tabHome, method: tabMethod, flow: tabFlow, setup: setupCard, runs: tabRuns, examples: tabExamples, docs: tabDocs, papers: tabPapers,
    tasks: tabTasks, decisions: tabDecisions, sessions: tabSessions, timeline: tabTimeline, upkeep: tabUpkeep}[r.tab](d, r);
  return head + notices(d) + withTerms(body, d);
}

function notices(d) {
  const out = [], sc = d.scan || {}, now = d.now;
  if (d.stale) out.push(d.scanned_at ? `这个项目的数据是 ${B.ago(d.scanned_at, now)}的，最近一次读取失败（${esc(d.stale.error)}）。`
    : `这个项目还没有成功读取过（${esc(d.stale.error)}）。`);
  if (sc.running_since && now - sc.running_since > 5) out.push(`正在扫描共享盘，已 ${B.span(now - sc.running_since)}；下面是上一次的结果。`);
  if (sc.error) out.push(`最近一次扫描失败（${esc(sc.error)}），下面是 ${B.ago(sc.finished, now)}的结果。`);
  return out.map(t => `<div class="notice">${t}</div>`).join('');
}

// the right side of the flow when nothing is selected: conclusion, numbers, what needs you, activity, checks
function flowSummary(d) {
  const p = d.project || {}, c = ctx(d), name = d.name;
  const tracks = statusCounts(d.tracks), atts = statusCounts(d.attempts);
  const running = (tracks.find(([s]) => s === '进行中') || [0, 0])[1];
  const active = d.runs.filter(x => (x.run || {}).status === '运行中');
  const best = B.bestRun(d.runs, c.disp.headline);
  let bestCard;
  if (best) {
    const h = c.disp.headline, pr = c.disp.ratio[h.metric], v = best.vals;
    const frac = pr && typeof v[pr[0]] === 'number' && typeof v[pr[1]] === 'number' ? `（${B.num(v[pr[0]])}/${B.num(v[pr[1]])}）` : '';
    bestCard = `<a class="stat" href="${plink(name, 'runs', runKey(best.run))}"><div class="k">最好的${esc(c.metricName(h.metric))} · ${esc(c.groupName(h.group))}</div>
      <div class="v">${B.num(best.value)}</div><div class="s">来自 <b>${esc(c.label(best.run))}</b>${frac}</div></a>`;
  } else {
    bestCard = `<div class="stat"><div class="k">最好的主指标</div><div class="v">—</div>
      <div class="s">${c.disp.headline ? '还没有运行记录这个指标' : '未设置：在 project.json 的 display.headline 里写明分组、指标和方向'}</div></div>`;
  }
  const pending = d.attempts.filter(a => pendingOf(a).length).sort((a, b) => dateKey(b).localeCompare(dateKey(a)));
  const answers = d.answers || {}, proj = {name, title: p.title};
  const todos = openTodos(d.todos).sort((a, b) => kindRank(a) - kindRank(b)).map(t => ({...t, answer: answers[String(t.id)] || null}));
  const waitItems = [
    ...todos.map(t => todoItem(t, proj, d.now)),
    ...pending.map(a => typeof a.meta.id === 'string' && pendingOf(a).includes('结论待确认')
      ? confirmItem(proj, {id: a.meta.id, title: a.meta.title || attName(a), proposed: a.meta.proposed,
          conclusion: ((a.sections || {})['结论和原因'] || '').slice(0, 600), answer: answers['a:' + a.meta.id] || null}, d.now)
      : `<li><span class="pill t-other">缺结论</span> <a href="${plink(name, 'flow', 'a:' + attKey(a))}">${esc(attName(a))}</a>
          <span class="small">${needsLabel(pendingOf(a))}</span></li>`)];
  const ev = B.foldEvents(B.events(d)).slice(0, 14);
  const evHref = e => e.tab === 'experiments' ? plink(name, 'flow', e.item != null ? 'a:' + e.item : null)
    : e.tab === 'roadmap' && e.item != null ? plink(name, 'flow', (e.text.startsWith('定版') ? 'v:' : 't:') + e.item) : plink(name, e.tab, e.item);
  const concl = currentConclusion(d);
  const recent = d.attempts.filter(a => ['成功', '部分成功', '失败', '放弃'].includes(a.meta.status) && (a.sections || {})['结论和原因'])
    .sort((a, b) => dateKey(b).localeCompare(dateKey(a))).slice(0, 4);
  return `<div class="detail"><h2>项目概况</h2>
    ${concl.text ? `<p class="concl">${esc(concl.text)}</p><div class="small">${concl.from}</div>` : '<p class="concl faint">还没有结论</p>'}
    ${p.question ? `<p class="question">研究问题：${esc(p.question)}</p>` : ''}
    <div class="stats">
      <a class="stat" href="${plink(name, 'flow')}"><div class="k">支线</div><div class="v">${d.tracks.length}</div>
        <div class="s">${running ? `<b>${running}</b> 条进行中 · ` : ''}${countLine(tracks.filter(([s]) => s !== '进行中')) || '还没有支线'}</div></a>
      <a class="stat" href="${plink(name, 'flow')}"><div class="k">尝试</div><div class="v">${d.attempts.length}</div>
        <div class="s">${countLine(atts) || '还没有尝试'}</div></a>
      ${bestCard}
      <a class="stat" href="${plink(name, 'runs')}"><div class="k">正在跑</div><div class="v">${active.length}</div>
        <div class="s">${active.length ? active.map(x => `${esc(c.label(x))} · ${esc((x.run || {}).server || '?')}${esc(gpusText(x.run || {}))} ${statePill(x.state)}`).join('<br>') : '没有正在跑的任务'}</div></a>
      ${paperStat(d)}
    </div>
    ${recent.length ? `<div class="sec"><h3>最近定下的结论</h3><ul class="gists">${recent.map(a => `<li>${B.pill(a.meta.status)}
      <a href="${plink(name, 'flow', 'a:' + attKey(a))}">${esc(attName(a))}</a>：${esc(gistOf(d, a))}</li>`).join('')}</ul></div>` : ''}
    <details class="sec" id="sum-setup"><summary><h3 class="inl">实验设置摘要</h3></summary>${setupSummary(d)}</details>
    <div>
      <div class="card"><h2>等你处理 <span class="meta">${waitItems.length} 条</span></h2>
        ${waitItems.length ? `<ul class="tlist">${waitItems.slice(0, 14).join('')}</ul>` +
          (waitItems.length > 14 ? `<div class="pad small"><a href="#/todo">全部 ${waitItems.length} 条 →</a></div>` : '') : '<div class="empty">没有等你处理的事。</div>'}</div>
      <div class="card"><h2>最近动态</h2>
        ${ev.length ? `<ul class="feed">${ev.map(e => `<li><span class="when">${B.ago(e.t, d.now)}</span><a href="${evHref(e)}">${esc(e.text)}</a></li>`).join('')}</ul>`
          : '<div class="empty">还没有记录。</div>'}</div>
    </div>
    ${(d.checks || []).length + d.errors.length ? `<div class="pad small"><a href="${plink(name, 'upkeep')}">记录维护：${(d.checks || []).length + d.errors.length} 条格式问题 →</a></div>` : ''}</div>`;
}

function paperStat(d) {
  const ps = paperItems(d);
  if (!ps.length) return '';
  const read = ps.filter(p => p.read).length, must = ps.filter(p => p.must && !p.read).length;
  return `<a class="stat" href="${plink(d.name, 'papers')}"><div class="k">论文</div><div class="v">${ps.length}</div>
    <div class="s">读过 <b>${read}</b>${must ? ` · 必读未读 <b>${must}</b>` : ''}</div></a>`;
}
// one line per kind on the overview; every name links to its definition on the setup tab
function setupSummary(d) {
  const c = ctx(d), s = c.setup, name = d.name;
  if (!s.present) return `<div class="card"><h2>实验设置</h2><div class="empty">还没有 board/setup.json（数据集、模型、指标、方法的定义）。</div></div>`;
  const link = (kind, x) => `<a href="${plink(name, 'setup', kind + ':' + x.id)}" title="${esc(x.about || '')}">${esc(x.name || x.id)}</a>`;
  const ds = [...s.datasets.values()], tests = ds.filter(x => x.role === 'test'), others = ds.filter(x => x.role !== 'test');
  const keyMetrics = [...s.metrics.values()].filter(x => x.better);
  const line = (k, html) => html ? `<div class="kv2"><span>${k}</span><div>${html}</div></div>` : '';
  return `<div class="card"><h2>实验设置 <a class="meta" href="${plink(name, 'setup')}">完整定义 →</a></h2><div class="pad">
    ${s.goal ? `<p class="goal">${esc(s.goal)}</p>` : ''}
    ${line('测试集', tests.map(x => link('ds', x)).join('、'))}
    ${line('其他数据', others.map(x => link('ds', x)).join('、'))}
    ${line('模型', s.models.map(x => link('model', x)).join('、'))}
    ${line('主要指标', keyMetrics.map(x => link('mt', x) + `<span class="small">${x.better === 'higher' ? '↑' : '↓'}</span>`).join('、'))}
    ${line('方法', s.methods.map(x => link('method', x)).join('、'))}
    </div></div>`;
}

// the paper-style "experimental setup" from board/setup.json; entries carry anchors that other pages link to
// board/flow.json: how the method works, as boxes and arrows from left to right
function methodFlow(d) {
  const f = d.flow;
  if (!f || !Array.isArray(f.nodes) || !f.nodes.length) return '';
  const nodes = f.nodes.filter(n => n && typeof n.id === 'string'), byId = new Map(nodes.map(n => [n.id, n]));
  const edges = (Array.isArray(f.edges) ? f.edges : []).filter(e => Array.isArray(e) && byId.has(e[0]) && byId.has(e[1]));
  const indeg = new Map(nodes.map(n => [n.id, 0]));
  for (const [, b] of edges) indeg.set(b, indeg.get(b) + 1);
  const order = [], seen = new Set();
  let queue = nodes.filter(n => !indeg.get(n.id));
  while (queue.length) {
    const n = queue.shift();
    if (seen.has(n.id)) continue;
    seen.add(n.id); order.push(n);
    for (const [a, b] of edges) if (a === n.id) { indeg.set(b, indeg.get(b) - 1); if (!indeg.get(b)) queue.push(byId.get(b)); }
  }
  order.push(...nodes.filter(n => !seen.has(n.id)));
  const groups = f.groups && typeof f.groups === 'object' ? Object.keys(f.groups) : [];
  const gi = g => Math.max(0, groups.indexOf(g)) % 4;
  const box = n => { const href = linkFor(d.name, n.link), tag = href ? 'a' : 'div';
    return `<${tag} class="mbox g${gi(n.group)}"${href ? ` href="${href}"` : ''}><b>${esc(n.title || n.id)}</b>${n.subtitle ? `<span>${esc(n.subtitle)}</span>` : ''}</${tag}>`; };
  return `<div class="setblk"><h3>方法流程 <span class="faint">board/flow.json</span></h3><div class="mflow">${order.map(box).join('<span class="marr">→</span>')}</div>
    ${groups.length ? `<div class="mleg">${groups.map(g => `<span class="mbox g${gi(g)} sw"></span>${esc(f.groups[g])}`).join('　')}</div>` : ''}</div>`;
}
function setupCard(d) {  // the 实验设置 tab
  const c = ctx(d), s = c.setup, name = d.name;
  if (!s.present) return `<div class="card"><h2>实验设置</h2>${methodFlow(d) ? `<div class="pad">${methodIndex(d).steps.length ? '' : methodFlow(d)}</div>` : ''}<div class="empty">还没有实验设置（board/setup.json）。
    在这个项目的会话里说「补实验设置」，会话会按整体方案写好目标、数据集、模型、指标和方法的定义；之后更新方案或定新版本时会同步更新。
    写好后这里会像论文的「实验设置」一节那样列出来，运行页的分组和指标也会链接到这里。</div></div>`;
  const row = (anchor, cells) => `<tr data-anchor="${esc(anchor)}">${cells.map(x => `<td>${x}</td>`).join('')}</tr>`;
  const table = (title, n, heads, rows) => n ? `<div class="setblk"><h3>${title} <span class="faint">${n}</span></h3>
    <div class="scroll"><table class="mt setup"><tr>${heads.map(h => `<th>${h}</th>`).join('')}</tr>${rows.join('')}</table></div></div>` : '';
  const ds = [...s.datasets.values()], mt = [...s.metrics.values()];
  const runsOfMethod = m => (Array.isArray(m.runs) ? m.runs : []).map(String);
  return `<div class="card"><h2>实验设置 <span class="meta">board/setup.json</span></h2><div class="pad">${methodFlow(d)}
    ${s.goal ? `<p class="goal">${esc(s.goal)}</p>` : ''}
    <div class="setgrid">
    ${table('数据集', ds.length, ['名称', '用途', '规模', '说明'], ds.map(x => row('ds:' + x.id, [
      `<b>${esc(x.name || x.id)}</b><div class="small">${esc(x.id)}</div>`, esc(ROLE[x.role] || x.role || ''), x.size != null ? `<span class="num">${B.num(x.size)}</span>` : '',
      `${esc(x.about || '')}${x.source ? `<div class="small"><code>${esc(x.source)}</code></div>` : ''}`])))}
    ${table('模型', s.models.length, ['名称', '版本', '说明'], s.models.map(x => row('model:' + x.id, [
      `<b>${esc(x.name || x.id)}</b>`, x.version ? `<code>${esc(x.version)}</code>` : '<span class="faint">没有记录</span>', esc(x.about || '')])))}
    ${table('指标', mt.length, ['名称', '方向', '说明'], mt.map(x => row('mt:' + x.id, [
      `<b>${esc(x.name || x.id)}</b><div class="small">${esc(x.id)}</div>`, esc(BETTER[x.better] || '—'), esc(x.about || '')])))}
    ${table('方法', s.methods.length, ['名称', '支线', '运行', '说明'], s.methods.map(x => row('method:' + x.id, [
      `<b>${esc(x.name || x.id)}</b>${methodIndex(d).stepsOfSetupMethod(x.id).map(st => `<div class="small"><a href="${plink(name, 'method', st.meta.id)}">怎么做 →</a></div>`).join('')}`, x.track && c.trackById.get(x.track) ? `<a href="${plink(name, 'flow', 't:' + x.track)}">${esc(c.trackById.get(x.track).meta.title || x.track)}</a>` : '',
      runsOfMethod(x).map(r => c.runById.get(r) ? `<a href="${plink(name, 'runs', r)}">${esc(r)}</a>` : `<span class="faint">${esc(r)}</span>`).join('<br>'),
      esc(x.about || '')])))}
    </div></div></div>`;
}

function checksCard(d) {
  const items = [...(d.checks || []).map(x => [x.where, x.what]), ...d.errors.map(e => [e.file, e.error])];
  if (!items.length) return '';
  return `<div class="card"><h2>记录检查 <span class="meta">${items.length} 条 · 写记录的会话可以按这里修，修好后约 1 分钟消失 · 格式见 /shared/ssd/dchen/_shared/BOARD_FORMAT.md</span></h2>
    <div class="scroll"><table class="mt" style="margin:4px 8px 8px">${items.map(([w, t]) => `<tr><td><code>${esc(w)}</code></td><td style="white-space:normal">${esc(t)}</td></tr>`).join('')}</table></div></div>`;
}

// ------------------------------------------------------------ 流程: the experiment flow on the left, the selected step on the right
const plain = t => String(t || '').replace(/[*`#>]/g, '').replace(/\{\{run:[^}]*\}\}/g, '…').trim();
const sentence = (title, text) => B.firstSentence('## ' + title + '\n' + plain(text), title);
// the project's current conclusion: the current version's 结论 section, else project.json's conclusion
function currentConclusion(d) {
  const p = d.project || {}, v = ctx(d).verById.get(p.current_version);
  const sec = v && new Map(sectionsOf(v.body).list).get('结论');
  if (sec && sec.trim()) return {text: plain(sec).split('\n').filter(Boolean).join(' '), from: `来自版本 ${esc(p.current_version)} 的「结论」`};
  return {text: p.conclusion || '', from: p.conclusion ? '来自 project.json（当前版本还没有「结论」一节）' : ''};
}
// one line about an attempt: its conclusion once there is one, else its results, else what it set out to test
function gistOf(d, a) {
  const sec = a.sections || {}, op = a.onepager || {}, t = k => (op[k] || {}).text || '';
  return plain(t('所以')) || gist(sec['结论和原因']) || plain(t('结果')) || gist(sec['结果']) || plain(t('想回答')) || gist(sec['假设']) || '';
}
// the first sentence that says something: skips a bare status line such as "部分成功(用户于 11-21 确认)。"
function gist(text) {
  const parts = plain(text).split(/(?<=[。；;！!？?])|\n+/).map(x => x.trim().replace(/^([-+]|\d+[.、)])\s*/, '')).filter(Boolean);
  const said = parts.find(x => !/^(部分成功|成功|失败|放弃|待确认)\s*([(（][^)）]*[)）])?\s*[。;；]?$/.test(x));
  const out = said || parts[0] || '';
  return out.length > 140 ? out.slice(0, 139) + '…' : out;
}
// {{run:<id>/<group>/<metric>}} in the records -> the number from that run
function refValue(d, spec) {
  const parts = spec.split('/'), id = parts[0], metric = parts[parts.length - 1], group = parts.length > 2 ? parts.slice(1, -1).join('/') : null;
  const r = ctx(d).runById.get(id), m = r && (r.run || {}).metrics;
  const v = !m ? undefined : group ? (m[group] || {})[metric] : m[metric];
  return {r, v, id, group, metric};
}
function refs(html, d) {
  return String(html).replace(/\{\{run:([^}\s]+)\}\}/g, (all, spec) => {
    const x = refValue(d, spec.replace(/&amp;/g, '&'));
    if (typeof x.v !== 'number') return `<span class="ref bad" title="找不到运行记录里的这个数：${esc(spec)}">?</span>`;
    return `<a class="ref" href="${plink(d.name, 'runs', x.id)}" title="运行 ${esc(x.id)}${x.group ? ' · ' + esc(ctx(d).groupName(x.group)) : ''} · ${esc(ctx(d).metricName(x.metric))}">${B.num(x.v)}</a>`;
  });
}

const FROW = 46, FLANE = 18, FPAD = 14;
const FSYM = {ok: '✔', part: '◐', bad: '✗', off: '⊘', run: '▶', todo: '?', other: '○'};
const nodeTone = n => n.type === 'attempt' ? (n.ref.meta.status === '计划中' ? 'plan' : B.tone(n.ref.meta.status)) : n.type;
// what the runs of an attempt say right now (from the run records, no one has to write it)
function runState(d, a) {
  const runs = ctx(d).runsOf(a).filter(r => !r.missing), st = r => (r.run || {}).status;
  const running = runs.filter(r => st(r) === '运行中'), lost = running.filter(r => r.state === '已中断' || r.state === '状态未知');
  return {runs, running, lost, waiting: a.meta.status === '进行中' && runs.length > 0 && !running.length};
}
function flowFlags(d, a) {
  const rs = runState(d, a), out = [], ans = (d.answers || {})['a:' + attKey(a)];
  if (rs.running.length) out.push(`<span class="ff run">▶ ${rs.running.length}/${rs.runs.length} 在跑</span>`);
  if (rs.lost.length) out.push(`<span class="ff bad">${rs.lost.length} 个状态存疑</span>`);
  if (rs.waiting) out.push('<span class="ff todo">结果已出 · 待写结论</span>');
  if (a.meta.status === '待确认') out.push(ans ? '<span class="ff run">已回复</span>' : '<span class="ff todo">等你确认</span>');
  else if (pendingOf(a).includes('结论和原因')) out.push('<span class="ff todo">缺结论</span>');
  return out.join('');
}
function flowRow(d, n, sel) {
  const name = d.name, p = d.project || {};
  let key, cls = n.type, f1, f2 = '';
  if (n.type === 'start') {
    key = null;
    f1 = '<span class="nm">开始</span>';
    f2 = p.question ? '研究问题：' + esc(p.question) : '';
  } else if (n.type === 'version') {
    const v = n.ref, vid = idOf(v), cur = p.current_version === vid;
    const merged = Array.isArray(v.meta.merged_tracks) ? v.meta.merged_tracks.length : 0;
    key = 'v:' + vid;
    f1 = `<span class="nm">版本 ${esc(vid)}${v.meta.title ? ' · ' + esc(v.meta.title) : ''}</span>${cur ? '<span class="pill t-run">当前</span>' : ''}<span class="dt">${esc(B.day(v.meta.date))}</span>`;
    f2 = [merged ? `并入 ${merged} 条支线` : '', sentence('结论', new Map(sectionsOf(v.body).list).get('结论'))].filter(Boolean).map(esc).join(' · ');
  } else if (n.type === 'track') {
    const t = n.ref;
    key = t ? 't:' + idOf(t) : null;
    f1 = `<span class="tag2">支线</span><span class="nm">${t ? esc(t.meta.title || idOf(t)) : '没有归属支线的尝试'}</span>${t ? B.pill(t.meta.status) : ''}`;
    const ts = t ? Object.fromEntries(sectionsOf(t.body).list) : {};
    f2 = esc(sentence('结局和原因', ts['结局和原因']) || sentence('现状', ts['现状']) || sentence('假设', ts['假设']));
  } else {
    const a = n.ref, st = a.meta.status, tone = nodeTone(n);
    key = 'a:' + attKey(a);
    cls += ' s-' + tone;
    const lead = tone === 'bad' ? '失败原因：' : tone === 'off' ? '放弃原因：' : tone === 'plan' ? '计划：' : '';
    f1 = `<span class="sym s-${tone}">${tone === 'plan' ? '○' : FSYM[tone] || '○'}</span><span class="nm">${esc(attName(a))}</span>` +
      `<span class="st">${esc(st || '无状态')}</span>${flowFlags(d, a)}<span class="dt">${esc(attDate(a).slice(5))}</span>`;
    const g = gistOf(d, a);
    f2 = g ? esc(lead + g) : '';
  }
  const nd = key ? docsAbout(d, key).length : 0;
  if (nd) f1 = f1.replace(/(<span class="dt">|$)/, `<span class="fdoc" title="这一步有 ${nd} 篇文档">📄${nd}</span>$1`);
  const href = key ? plink(name, 'flow', key) : plink(name, 'flow');
  const on = key ? sel === key : sel == null;
  return `<a class="frow k-${cls}${on ? ' on' : ''}" href="${href}" style="height:${FROW}px"><div class="f1">${f1}</div>${f2 ? `<div class="f2">${f2}</div>` : ''}</a>`;
}
function flowSvg(L, sel) {
  const X = lane => FPAD + lane * FLANE, Y = row => row * FROW + FROW / 2;
  const W = FPAD * 2 + (L.lanes - 1) * FLANE, H = L.nodes.length * FROW, byKey = new Map(L.nodes.map(n => [n.key, n]));
  const edge = e => {
    const a = byKey.get(e.from), b = byKey.get(e.to), x1 = X(a.lane), y1 = Y(a.row), x2 = X(b.lane), y2 = Y(b.row);
    const hl = sel && (e.from === sel || e.to === sel) ? ' hl' : '';
    let dd;
    if (e.kind === 'after') dd = `M${x1} ${y1} C${x1} ${(y1 + y2) / 2} ${x2} ${(y1 + y2) / 2} ${x2} ${y2}`;
    else if (x1 === x2) dd = `M${x1} ${y1} L${x2} ${y2}`;
    else if (e.kind === 'merge') dd = `M${x1} ${y1} L${x1} ${y2 - FROW / 2} Q${x1} ${y2} ${x2} ${y2}`;
    else dd = `M${x1} ${y1} Q${x2} ${y1} ${x2} ${y1 + FROW / 2} L${x2} ${y2}`;
    return `<path class="fe ${e.kind}${hl}" d="${dd}"/>`;
  };
  // how a lane ends when it does not merge: abandoned, paused, or still open below its last step
  const ends = L.chains.filter(ch => !ch.into && byKey.get(ch.last)).map(ch => {
    const n = byKey.get(ch.last), x = X(n.lane), y = Y(n.row) + 9;
    if (ch.status === '放弃') return `<path class="fend bad" d="M${x - 5} ${y + 8} L${x + 5} ${y + 18} M${x + 5} ${y + 8} L${x - 5} ${y + 18}"/>`;
    if (ch.status === '暂停') return `<path class="fend" d="M${x - 3} ${y + 8} L${x - 3} ${y + 18} M${x + 3} ${y + 8} L${x + 3} ${y + 18}"/>`;
    return `<path class="fe open" d="M${x} ${y} L${x} ${y + FROW / 2}"/>`;
  }).join('');
  const glyph = n => {
    const x = X(n.lane), y = Y(n.row), t = nodeTone(n), on = n.key === sel ? ' on' : '';
    if (n.type === 'start') return `<circle class="fg start${on}" cx="${x}" cy="${y}" r="6"/>`;
    if (n.type === 'version') return `<rect class="fg version${on}" x="${x - 7}" y="${y - 7}" width="14" height="14" rx="2" transform="rotate(45 ${x} ${y})"/>`;
    if (n.type === 'track') return `<rect class="fg ftrack${on}" x="${x - 5}" y="${y - 5}" width="10" height="10" rx="3"/>`;
    const c = `<circle class="fg a-${t}${on}" cx="${x}" cy="${y}" r="7"/>`;
    if (t === 'ok') return c + `<path class="fmark" d="M${x - 3.5} ${y} L${x - 1} ${y + 3} L${x + 4} ${y - 3}"/>`;
    if (t === 'bad') return c + `<path class="fmark" d="M${x - 3} ${y - 3} L${x + 3} ${y + 3} M${x + 3} ${y - 3} L${x - 3} ${y + 3}"/>`;
    if (t === 'part') return c + `<path class="fhalf" d="M${x} ${y - 7} A7 7 0 0 1 ${x} ${y + 7} Z"/>`;
    if (t === 'run') return c + `<circle class="fdot" cx="${x}" cy="${y}" r="3"/>`;
    if (t === 'todo') return c + `<text class="fq" x="${x}" y="${y + 4}">?</text>`;
    return c;
  };
  return `<svg class="flowsvg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" aria-hidden="true">` +
    L.edges.map(edge).join('') + ends + L.nodes.map(glyph).join('') + '</svg>';
}
function tabFlow(d, r) {
  const c = ctx(d), L = d._flow || (d._flow = B.flowLayout(d)), sel = r.item;
  const W = FPAD * 2 + (L.lanes - 1) * FLANE;
  const rows = L.nodes.map(n => flowRow(d, n, sel)).join('');
  const legend = `<div class="flegend">${[['ok', '成功'], ['part', '部分成功'], ['bad', '失败'], ['off', '放弃'], ['run', '进行中'], ['todo', '待确认'], ['plan', '计划中']]
    .map(([t, l]) => `<span><span class="sym s-${t}">${t === 'plan' ? '○' : FSYM[t]}</span>${l}</span>`).join('')}<span><span class="sym version">◆</span>版本</span></div>`;
  let detail;
  if (sel == null) detail = flowSummary(d);
  else if (sel.startsWith('v:') && c.verById.get(sel.slice(2))) detail = versionDetail(d, c.verById.get(sel.slice(2)));
  else if (sel.startsWith('t:') && c.trackById.get(sel.slice(2))) detail = trackDetail(d, c.trackById.get(sel.slice(2)));
  else if (sel.startsWith('a:') && c.attById.get(sel.slice(2))) detail = attemptDetail(d, c.attById.get(sel.slice(2)), L);
  else detail = `<div class="detail empty">找不到「${esc(sel)}」，可能已经改名或删除。<a href="${plink(d.name, 'flow')}">回到项目概况</a></div>`;
  const empty = L.nodes.length <= 1 ? '<div class="empty">还没有版本、支线或尝试。讨论方案时让会话把计划的尝试写成「计划中」，这里就会画出来。</div>' : '';
  return `<div class="flowwrap"><div class="list flowbox">${legend}<div class="items">${empty ||
      `<div class="flow" style="--fw:${W}px">${flowSvg(L, sel)}${rows}</div>`}</div></div><div>${refs(detail, d)}</div></div>`;
}

// ------------------------------------------------------------ 方法: how the method works, step by step; a step can open into its own sub-flow
// board/method/<id>.md: parent (the step it is part of), next (arrows on the same level), variant_of (another way to do
// that step), tracks (tracks trying it), methods (setup.json method ids)
function methodIndex(d) {
  if (d._method) return d._method;
  const c = ctx(d), steps = (d.method || []).filter(x => typeof x.meta.id === 'string');
  const byId = new Map(steps.map(x => [x.meta.id, x])), valid = id => typeof id === 'string' && byId.has(id);
  const list = v => (Array.isArray(v) ? v : v ? [v] : []).map(String);
  const slot = x => valid(x.meta.variant_of) && x.meta.variant_of !== x.meta.id ? byId.get(x.meta.variant_of) : x;
  const parentOf = x => { const p = slot(x).meta.parent; return valid(p) && p !== x.meta.id ? p : null; };
  const kids = new Map();
  for (const x of steps) if (slot(x) === x) { const k = parentOf(x) || ''; if (!kids.has(k)) kids.set(k, []); kids.get(k).push(x); }
  // a level in the order of its arrows (next), then file order
  const level = k => {
    const xs = kids.get(k) || [], ids = new Set(xs.map(x => x.meta.id)), indeg = new Map(xs.map(x => [x.meta.id, 0]));
    for (const x of xs) for (const nx of list(x.meta.next)) if (ids.has(nx)) indeg.set(nx, indeg.get(nx) + 1);
    const out = [], seen = new Set(), queue = xs.filter(x => !indeg.get(x.meta.id));
    while (queue.length) {
      const x = queue.shift();
      if (seen.has(x.meta.id)) continue;
      seen.add(x.meta.id); out.push(x);
      for (const nx of list(x.meta.next)) if (ids.has(nx)) { indeg.set(nx, indeg.get(nx) - 1); if (!indeg.get(nx)) queue.push(byId.get(nx)); }
    }
    return out.concat(xs.filter(x => !seen.has(x.meta.id)));
  };
  const variants = x => steps.filter(y => y !== x && y.meta.variant_of === x.meta.id);
  const path = x => { const out = []; let cur = x; while (cur && out.length < 12 && !out.includes(cur)) { out.unshift(cur); const p = parentOf(cur); cur = p ? byId.get(p) : null; } return out; };
  const hasInside = x => (kids.get(x.meta.id) || []).length > 0;
  // experiments on a step: its tracks' attempts, plus attempts that name the step in their method field
  const tracksOf = x => list(x.meta.tracks).map(t => c.trackById.get(t)).filter(Boolean);
  const attemptsOf = x => d.attempts.filter(a => list(x.meta.tracks).includes(a.meta.track) || list(a.meta.method).includes(x.meta.id));
  const stepsOfAttempt = a => steps.filter(x => list(x.meta.tracks).includes(a.meta.track) || list(a.meta.method).includes(x.meta.id));
  const stepsOfTrack = tid => steps.filter(x => list(x.meta.tracks).includes(tid));
  const stepsOfSetupMethod = mid => steps.filter(x => list(x.meta.methods).includes(mid));
  const title = x => x.meta.title || x.meta.id;
  // loops on a level: a step with back: <earlier step on the same level> repeats that stretch (loop: "30 轮")
  const loopsOf = lv => lv.map((x, j) => { const i = lv.findIndex(y => y.meta.id === x.meta.back);
    return i >= 0 && i <= j ? {from: i, to: j, label: typeof x.meta.loop === 'string' || typeof x.meta.loop === 'number' ? String(x.meta.loop) : ''} : null; }).filter(Boolean);
  return d._method = {steps, byId, slot, level, variants, path, hasInside, tracksOf, attemptsOf, stepsOfAttempt, stepsOfTrack, stepsOfSetupMethod, title, list, loopsOf};
}
const stepLink = (d, x) => `<a class="chip" href="${plink(d.name, 'method', x.meta.id)}">${esc(methodIndex(d).title(x))}</a>`;
function methodTab(d, x, hl, loop) {  // one step as a box on its level; loop: 'in' inside a loop, 'end' where it goes back
  const M = methodIndex(d), sec = Object.fromEntries(sectionsOf(x.body).list), vs = M.variants(x);
  const atts = [x, ...vs].flatMap(M.attemptsOf), inside = M.hasInside(x) || vs.some(M.hasInside);
  const dots = [...new Set(atts)].slice(0, 12).map(a => { const t = a.meta.status === '计划中' ? 'plan' : B.tone(a.meta.status);
    return `<span class="sym s-${t}" title="${esc(attName(a))}：${esc(a.meta.status || '')}">${t === 'plan' ? '○' : FSYM[t] || '○'}</span>`; }).join('');
  return `<a class="mstep${hl ? ' on' : ''}${loop ? ' inloop' : ''}" href="${plink(d.name, 'method', x.meta.id)}"><b>${loop === 'start' ? '<span class="lpn">↻</span>' : ''}${esc(M.title(x))}${loop === 'end' ? '<span class="lpn">↺</span>' : ''}</b>
    ${gist(sec['一句话']) ? `<span class="ms1">${esc(gist(sec['一句话']))}</span>` : ''}
    <span class="ms2">${vs.length ? `<span class="tag2">${vs.length + 1} 种做法</span>` : ''}${inside ? '<span class="tag2">可展开</span>' : ''}${dots}</span></a>`;
}
function tabMethod(d, r) {
  const M = methodIndex(d), name = d.name;
  if (!M.steps.length) {
    return `<div class="card"><h2>方法</h2><div class="pad">${methodFlow(d)}
      <div class="empty">还没有把方法拆成步骤（board/method/&lt;步骤&gt;.md）。在项目会话里说「把方法写成步骤」，会话会按整体方案写出每一步怎么做、输入输出和关键设置；
      某一步还可以有不同做法和自己的子流程。写好后这里能一层层点开，并和实验互相链接。</div></div></div>`;
  }
  const sel = r.item != null ? M.byId.get(r.item) : null;
  const path = sel ? M.path(sel) : [];
  const rows = [];
  let key = '', label = '方法';
  for (let i = 0; i <= path.length; i++) {
    const lv = M.level(key);
    if (!lv.length) break;
    const p = path[i], hl = p ? M.slot(p) : null, loops = M.loopsOf(lv);
    const loopAt = j => { const l = loops.find(l => j >= l.from && j <= l.to); return !l ? null : j === l.to ? 'end' : j === l.from ? 'start' : 'in'; };
    const notes = loops.map(l => `<div class="mloop">↺ 第 ${l.from + 1}–${l.to + 1} 步（「${esc(M.title(lv[l.from]))}」到「${esc(M.title(lv[l.to]))}」）循环${l.label ? '，' + esc(l.label) : ''}</div>`).join('');
    rows.push(`<div class="mrow"><div class="mlab">${label}</div><div><div class="mline">${lv.map((x, j) => methodTab(d, x, x === hl, loopAt(j))).join('<span class="marr">→</span>')}</div>${notes}</div></div>`);
    if (!p) break;
    const vs = M.variants(hl);
    if (vs.length) rows.push(`<div class="mrow"><div class="mlab">「${esc(M.title(hl))}」的做法</div><div class="mline chips">${[hl, ...vs].map(x =>
      `<a class="chip${x === p ? ' on' : ''}" href="${plink(name, 'method', x.meta.id)}">${esc(M.title(x))}${x === hl ? '<span class="faint">（原做法）</span>' : ''}</a>`).join('')}</div></div>`);
    key = p.meta.id;
    label = i === path.length - 1 ? `「${esc(M.title(p))}」的子流程` : `「${esc(M.title(p))}」里面`;
  }
  if (r.item != null && !sel) rows.push(`<div class="notice">找不到步骤「${esc(r.item)}」，可能已经改名或删除。</div>`);
  return `<div class="card mcard">${rows.join('')}</div>` + (sel ? refs(methodDetail(d, sel, path), d) :
    `<div class="detail"><h2>方法</h2><div class="md"><p>上面是方法从输入到输出的流程，点任一步看它怎么做：输入和输出、具体做法、关键设置、一个例子，以及试过它的实验。
      标着「N 种做法」的步骤有不同做法可以比较，「可展开」的步骤里面还有自己的子流程。</p></div>
      ${M.level('').map(x => { const g = gist(Object.fromEntries(sectionsOf(x.body).list)['一句话']); return `<div class="kv2"><span>${esc(M.title(x))}</span><div>${esc(g)}</div></div>`; }).join('')}
      ${docsOfStep(d, 'm:')}</div>`);
}
function methodDetail(d, x, path) {
  const M = methodIndex(d), c = ctx(d), name = d.name, sec = Object.fromEntries(sectionsOf(x.body).list), base = M.slot(x);
  const crumb = `<div class="crumbs"><a href="${plink(name, 'method')}">方法</a>${path.slice(0, -1).map(p => ` <span class="small">›</span> <a href="${plink(name, 'method', p.meta.id)}">${esc(M.title(p))}</a>`).join('')}</div>`;
  const box = (t, text) => (text || '').trim() ? `<div class="sec"><h3>${t}</h3><div class="md">${B.md(text)}</div></div>` : '';
  const known = ['一句话', '输入和输出', '怎么做', '关键设置', '例子'];
  const extra = Object.keys(sec).filter(k => !known.includes(k)).map(k => box(esc(k), sec[k])).join('');
  const tracks = M.tracksOf(x), atts = M.attemptsOf(x);
  const exp = tracks.length || atts.length ? `<div class="sec"><h3>试过它的实验</h3>
    ${tracks.length ? `<div class="chips">${tracks.map(t => `<a class="chip" href="${plink(name, 'flow', 't:' + idOf(t))}">支线 · ${esc(t.meta.title || idOf(t))} · ${esc(statusOf(t))}</a>`).join('')}</div>` : ''}
    <ul class="gists">${atts.map(a => { const t = a.meta.status === '计划中' ? 'plan' : B.tone(a.meta.status);
      return `<li><span class="sym s-${t}">${t === 'plan' ? '○' : FSYM[t] || '○'}</span> <a href="${plink(name, 'flow', 'a:' + attKey(a))}">${esc(attName(a))}</a>
        <span class="small">${esc(a.meta.status || '')}</span>${gistOf(d, a) ? '：' + esc(gistOf(d, a)) : ''}</li>`; }).join('')}</ul></div>`
    : '<div class="sec"><h3>试过它的实验</h3><div class="none">还没有支线或尝试关联到这一步（在步骤里写 tracks，或在尝试里写 method）。</div></div>';
  const smeth = id => (c.setup.methods || []).find(m => m.id === id);
  const setupIds = M.list(x.meta.methods).filter(smeth);
  const setup = setupIds.length ? `<div class="sec"><h3>实验设置里对应的方法</h3><div class="chips">${setupIds.map(id =>
    `<a class="chip" href="${plink(name, 'setup', 'method:' + id)}">${esc(smeth(id).name || id)}</a>`).join('')}</div></div>` : '';
  const others = x === base ? M.variants(x) : [base, ...M.variants(base).filter(v => v !== x)];
  return `<div class="detail">${crumb}<h2>${esc(M.title(x))}${x !== base ? ` <span class="tag2">「${esc(M.title(base))}」的一种做法</span>` : ''}</h2>
    <div class="dmeta"><span>${esc(x.file)}</span>${others.length ? `<span>同一步的其他做法：${others.map(o => `<a href="${plink(name, 'method', o.meta.id)}">${esc(M.title(o))}</a>`).join('、')}</span>` : ''}</div>
    ${gist(sec['一句话']) ? `<div class="oneline">${esc(plain(sec['一句话']).split('\n').filter(Boolean).join(' '))}</div>` : ''}
    ${box('输入和输出', sec['输入和输出'])}${box('怎么做', sec['怎么做'])}${box('关键设置', sec['关键设置'])}${box('例子', sec['例子'])}${extra}
    ${exp}${setup}${stepRefs(d, 'm:' + x.meta.id)}</div>`;
}

// the headline metric across some runs, as bars (names from setup.json)
function headlineBars(d, runs, title) {
  const c = ctx(d), h = c.disp.headline;
  if (!h) return '';
  const eligible = runs.filter(r => (r.run || {}).kind !== 'data' && (r.run || {}).kind !== 'smoke'), lab = labelsFor(c, eligible);
  const items = eligible.map(r => {
    const v = B.groupValues((r.run || {}).metrics, h.group), pr = c.disp.ratio[h.metric];
    return {label: lab(r), value: v[h.metric], href: plink(d.name, 'runs', runKey(r)),
      dim: typeof v.n === 'number' && typeof v.of === 'number' && v.n < v.of,
      html: typeof v[h.metric] === 'number' ? B.num(v[h.metric]) + (pr && typeof v[pr[0]] === 'number' && typeof v[pr[1]] === 'number' ? ` <span class="small">(${B.num(v[pr[0]])}/${B.num(v[pr[1]])})</span>` : '') : ''};
  });
  const chart = B.bars(items, h.better);
  return chart ? `<div class="sec"><h3>${esc(title)} · ${esc(c.metricName(h.metric))}（${esc(c.groupName(h.group))}）</h3>${chart}
    <div class="small">绿色是最好的一次；斜纹是抽查（只评了部分样本），不参与比较。</div></div>` : '';
}

// the headline metric for some runs across the test sets: a run per row, a test set per column
function runsMatrix(d, runs, title) {
  const c = ctx(d), h = c.disp.headline;
  if (!h) return '';
  const rows = runs.filter(r => (r.run || {}).kind !== 'data' && (r.run || {}).kind !== 'smoke' &&
    Object.values((r.run || {}).metrics || {}).some(v => v && typeof v === 'object' && typeof v[h.metric] === 'number'));
  if (!rows.length) return '';
  const groups = B.metricGroups(rows.map(r => (r.run || {}).metrics)).filter(g => g.cols.includes(h.metric) && g.runs);
  const cols = B.arrangeGroups(groups, c.disp).ordered.slice(0, 6), lab = labelsFor(c, rows);
  return `<div class="sec"><h3>${esc(title)} · ${esc(c.metricName(h.metric))}（数字来自运行记录）</h3><div class="scroll"><table class="mt"><tr><th>运行</th>` +
    cols.map(g => `<th class="num" title="${esc((c.setup.datasets.get(g.name) || {}).about || '')}">${esc(c.groupName(g.name))}</th>`).join('') + '</tr>' +
    rows.map(r => `<tr><td><a href="${plink(d.name, 'runs', runKey(r))}" title="${esc(runKey(r))}">${esc(lab(r))}</a></td>` +
      cols.map(g => `<td class="num">${cellHtml(B.groupValues((r.run || {}).metrics, g.name), h.metric, c.disp.ratio)}</td>`).join('') + '</tr>').join('') +
    '</table></div></div>';
}

function bodySections(body, order = []) {
  const {pre, list} = sectionsOf(body), map = new Map(list);
  const titles = [...order.filter(t => map.has(t)), ...list.map(([t]) => t).filter(t => !order.includes(t))];
  return (pre ? `<div class="sec first"><div class="md">${B.md(pre)}</div></div>` : '') +
    titles.map((t, i) => `<div class="sec${!pre && i === 0 ? ' first' : ''}"><h3>${esc(t)}</h3>${map.get(t) ? `<div class="md">${B.md(map.get(t))}</div>` : '<div class="none">（空）</div>'}</div>`).join('');
}

function versionDetail(d, v) {
  const c = ctx(d), name = d.name, vid = idOf(v), node = c.tree.versions.find(x => idOf(x.version) === vid);
  const merged = (node ? node.tracks : []).filter(n => n.merged === vid), branched = (node ? node.tracks : []).filter(n => n.merged !== vid);
  const chip = n => `<a class="chip" href="${plink(name, 'flow', 't:' + idOf(n.track))}">${esc(n.track.meta.title || idOf(n.track))} · ${esc(statusOf(n.track))} · ${n.attempts.length} 次尝试</a>`;
  const runs = [...new Set((node ? node.tracks : []).flatMap(n => n.attempts.flatMap(a => c.runsOf(a).filter(r => !r.missing))))];
  const cur = (d.project || {}).current_version === vid;
  return `<div class="detail"><h2>版本 ${esc(vid)}${v.meta.title ? ' · ' + esc(v.meta.title) : ''}${cur ? ' <span class="pill t-run">当前版本</span>' : ''}</h2>
    <div class="dmeta"><span>${esc(B.day(v.meta.date)) || '没有日期'}</span>${v.meta.parent ? `<span>来自 <a href="${plink(name, 'flow', 'v:' + v.meta.parent)}">${esc(v.meta.parent)}</a></span>` : ''}<span>${esc(v.file)}</span></div>
    ${merged.length ? `<div class="sec first"><h3>并入这个版本的支线</h3><div class="chips">${merged.map(chip).join('')}</div></div>` : ''}
    ${branched.length ? `<div class="sec${merged.length ? '' : ' first'}"><h3>从这个版本分出的支线</h3><div class="chips">${branched.map(chip).join('')}</div></div>` : ''}
    <div class="sec"><h3>这个版本的方案</h3>${v.body.trim() ? `<div class="md">${B.md(v.body)}</div>` : '<div class="none">版本正文是空的</div>'}</div>
    ${runsMatrix(d, runs, '这个版本下各次运行')}
    ${headlineBars(d, runs, '这个版本下各次运行')}${stepRefs(d, 'v:' + vid)}</div>`;
}

function trackDetail(d, t) {
  const c = ctx(d), name = d.name, tid = idOf(t), node = [...c.tree.versions.flatMap(v => v.tracks), ...c.tree.loose].find(n => idOf(n.track) === tid);
  const atts = node ? node.attempts : [];
  const ver = c.verById.get(c.homeOf.get(tid)), merged = node && node.merged;
  const placement = [
    t.meta.from_version ? `从 <a href="${plink(name, 'flow', 'v:' + t.meta.from_version)}">${esc(t.meta.from_version)}</a> 分出` : '',
    merged ? `并入 <a href="${plink(name, 'flow', 'v:' + merged)}">${esc(merged)}</a>` : ''].filter(Boolean).join('，');
  const steps = atts.map((a, i) => {
    const sec = a.sections || {}, plain = t => String(t || '').replace(/[*`]/g, '');
    const gist = B.firstSentence('## 结果\n' + plain(sec['结果']), '结果') || B.firstSentence('## 结论和原因\n' + plain(sec['结论和原因']), '结论和原因');
    return `<li><span class="stepn">${i + 1}</span><div><a href="${plink(name, 'flow', 'a:' + attKey(a))}"><b>${esc(attName(a))}</b></a> ${B.pill(a.meta.status)}
      <span class="small">${esc(attDate(a))}</span>${pendingOf(a).length ? ` <span class="todo">${needsLabel(pendingOf(a))}</span>` : ''}
      ${gist ? `<div class="small">${esc(gist)}</div>` : ''}</div></li>`;
  }).join('');
  const runs = [...new Set(atts.flatMap(a => c.runsOf(a).filter(r => !r.missing)))];
  const msteps = methodIndex(d).stepsOfTrack(tid);
  return `<div class="detail">${crumbs(d, {version: ver})}<h2>${esc(t.meta.title || tid)} ${B.pill(t.meta.status)}</h2>
    ${msteps.length ? `<div class="chips mstepsof"><span class="small">试的是方法里的：</span>${msteps.map(x => stepLink(d, x)).join('')}</div>` : ''}
    <div class="dmeta">${placement ? `<span>${placement}</span>` : ''}<span>${esc(B.day(t.meta.started))}${t.meta.ended ? ' → ' + esc(B.day(t.meta.ended)) : ''}</span><span>${esc(t.file)}</span></div>
    <div class="sec first"><h3>尝试的先后（${atts.length}）</h3>${steps ? `<ol class="steps">${steps}</ol>` : '<div class="none">还没有尝试</div>'}</div>
    ${runsMatrix(d, runs, '这条支线的各次运行')}
    ${headlineBars(d, runs, '这条支线的各次运行')}
    ${bodySections(t.body, ['假设', '现状', '结局和原因']).replace('class="sec first"', 'class="sec"')}${stepRefs(d, 't:' + tid)}</div>`;
}

const SECTION_ORDER = ['一页纸', '假设', '预期', '改了什么', '结果', '例子', '结论和原因'];
function attemptDetail(d, a, L) {
  const c = ctx(d), m = a.meta, name = d.name, pend = pendingOf(a), sec = a.sections || {};
  const t = c.trackById.get(m.track), ver = t && c.verById.get(c.homeOf.get(idOf(t)));
  const lane = L ? L.nodes.filter(n => n.type === 'attempt') : [];  // steps in the order of the flow
  const i = lane.findIndex(n => n.ref === a), prev = i > 0 ? lane[i - 1].ref : null, next = i >= 0 && i < lane.length - 1 ? lane[i + 1].ref : null;
  const siblings = d.attempts.filter(x => x.meta.track === m.track && t), k = siblings.indexOf(a);
  const rs = runState(d, a), tone = m.status === '计划中' ? 'plan' : B.tone(m.status);
  const box = (title, text, none) => `<div class="sec"><h3>${title}</h3>${(text || '').trim() ? `<div class="md">${B.md(text)}</div>` : `<div class="none">${none}</div>`}</div>`;
  // 1. one line
  const concl = gist(sec['结论和原因']);
  const line = concl ? esc(concl)
    : m.status === '计划中' ? '还没开始：' + esc(sentence('假设', sec['假设']) || '计划中的尝试')
    : rs.running.length ? `正在跑：${rs.running.length} / ${rs.runs.length} 个运行还没结束`
    : rs.waiting ? '运行都结束了，结果已出，等会话写结论' : esc(sentence('结果', sec['结果']) || '还没有结论');
  const answer = (d.answers || {})['a:' + attKey(a)];
  const confirm = m.status === '待确认' && typeof m.id === 'string'
    ? `<div class="sec"><h3>等你确认结论</h3>${replyBox(name, 'a:' + m.id, ['成功', '部分成功', '失败', '放弃'], m.proposed, answer || null, d.now)}</div>` : '';
  // 2. what happened: the hypothesis, what was changed, expected against observed
  const happened = `<div class="sec"><h3>发生了什么</h3>
    ${(sec['假设'] || '').trim() ? `<div class="kv2"><span>假设</span><div class="md">${B.md(sec['假设'])}</div></div>` : ''}
    ${(sec['改了什么'] || '').trim() ? `<div class="kv2"><span>改了什么</span><div class="md">${B.md(sec['改了什么'])}</div></div>` : ''}
    <div class="vs"><div><div class="vsh">预期</div>${(sec['预期'] || '').trim() ? `<div class="md">${B.md(sec['预期'])}</div>` : '<div class="none">没写预期</div>'}</div>
      <div><div class="vsh">实际</div>${(sec['结果'] || '').trim() ? `<div class="md">${B.md(sec['结果'])}</div>` : `<div class="none">${m.status === '计划中' ? '还没开始' : '还没写结果'}</div>`}</div></div></div>`;
  // 3. one example, 4. the numbers (from the run records), 5. the conclusion and the rest of the record
  const example = box('一个例子', sec['例子'], m.status === '计划中' ? '还没开始' : '这次尝试没有写例子') +
    `<div class="small more2"><a href="${plink(name, 'examples')}">看项目的全部样例 →</a></div>`;
  const data = `<div class="sec"><h3>数据 <span class="faint">来自运行记录</span></h3>${rs.runs.length ? linkedRuns(d, a, c.runsOf(a)) : '<div class="none">还没有关联的运行</div>'}</div>`;
  const conclusion = ['成功', '部分成功', '失败', '放弃', '待确认'].includes(m.status) || (sec['结论和原因'] || '').trim()
    ? box('结论和原因', sec['结论和原因'], m.status === '待确认' ? '待你确认结论' : '待写结论和原因') : '';
  const extra = Object.keys(sec).filter(x => !SECTION_ORDER.includes(x));
  const pre = (a.body.split(/^## /m)[0] || '').trim();
  const rest = pre || extra.length ? `<details class="sec" id="rest-${esc(attKey(a))}"><summary><h3 class="inl">其他记录</h3></summary>
    ${pre ? `<div class="md">${B.md(pre)}</div>` : ''}${extra.map(x => box(esc(x), sec[x], '（空）')).join('')}</details>` : '';
  const nav = (prev || next) ? `<div class="stepnav">${prev ? `<a href="${plink(name, 'flow', 'a:' + attKey(prev))}">← 上一步：${esc(attName(prev))}</a>` : '<span></span>'}
    ${next ? `<a href="${plink(name, 'flow', 'a:' + attKey(next))}">下一步：${esc(attName(next))} →</a>` : ''}</div>` : '';
  return `<div class="detail">${crumbs(d, {version: ver, track: t})}
    <h2>${esc(attName(a))} ${m.status === '计划中' ? '<span class="pill t-other">计划中</span>' : B.pill(m.status)}${pend.length ? ` <span class="todo">${needsLabel(pend)}</span>` : ''}</h2>
    <div class="dmeta"><span>${esc(attDate(a)) || '没有日期'}</span>${k >= 0 ? `<span>这条支线的第 ${k + 1} / ${siblings.length} 次尝试</span>` : '<span>没有归属支线</span>'}<span>${esc(a.file)}</span></div>
    ${methodIndex(d).stepsOfAttempt(a).length ? `<div class="chips mstepsof"><span class="small">改的是方法里的：</span>${methodIndex(d).stepsOfAttempt(a).map(x => stepLink(d, x)).join('')}</div>` : ''}
    ${Object.keys(a.onepager || {}).length ? onepagerBox(a) : `<div class="oneline s-${tone}">${line}</div>${onepagerBox(a, {missing: true})}`}
    ${confirm}${happened}${example}${data}${conclusion}${stepRefs(d, 'a:' + attKey(a))}${rest}${nav}</div>`;
}

// numbers of an attempt's runs, following display (primary columns, numerator/denominator in one cell) and setup names
function cellHtml(vals, col, ratio) {
  const p = ratio[col];
  return p && typeof vals[p[0]] === 'number' && typeof vals[p[1]] === 'number'
    ? `${B.num(vals[col])} <span class="small">(${B.num(vals[p[0]])}/${B.num(vals[p[1]])})</span>` : B.num(vals[col]);
}
function columnsFor(g, disp) {
  const primary = disp.primary.filter(x => g.cols.includes(x));
  if (primary.length) return primary;
  const parts = new Set(Object.entries(disp.ratio).filter(([x]) => g.cols.includes(x)).flatMap(([, p]) => p));
  return g.cols.filter(x => !parts.has(x));
}
function metricTh(c, col) {
  const m = c.setup.metrics.get(col);
  return `<th class="num" title="${esc(m ? [m.about, BETTER[m.better]].filter(Boolean).join('；') : col)}">${esc(c.metricName(col))}${m && m.better ? (m.better === 'higher' ? ' ↑' : ' ↓') : ''}</th>`;
}
function groupTd(d, g) {
  const c = ctx(d), ds = c.setup.datasets.get(g);
  return `<td title="${esc(ds && ds.about || '')}">${ds ? `<a href="${plink(d.name, 'setup', 'ds:' + g)}">${esc(ds.name || g)}</a>` : esc(c.groupName(g))}${ds && ds.size != null ? ` <span class="small">${B.num(ds.size)} 条</span>` : ''}</td>`;
}
function linkedRuns(d, a, runs) {
  const c = ctx(d), name = d.name, disp = c.disp;
  const found = runs.filter(r => !r.missing), missing = runs.filter(r => r.missing).map(r => r.missing), lab = labelsFor(c, found);
  const chips = `<div class="chips" style="margin:2px 0 6px">${found.map(r => `<a class="chip" href="${plink(name, 'runs', runKey(r))}" title="${esc(runKey(r))}">${esc(lab(r))}</a>`).join('')}</div>` +
    (missing.length ? `<div class="none">找不到这些运行的记录：${missing.map(esc).join('、')}</div>` : '');
  const scored = found.filter(r => B.metricGroups([(r.run || {}).metrics]).some(g => g.runs));
  const groups = B.metricGroups(scored.map(r => (r.run || {}).metrics)).filter(g => g.runs);
  if (!groups.length) return chips + (found.length ? '<div class="none">这些运行还没有指标</div>' : '');
  const {ordered, defaultName} = B.arrangeGroups(groups, disp);
  if (scored.length === 1) {  // one run with numbers: a row per group (test set), primary metrics as columns
    const vals = g => B.groupValues((scored[0].run || {}).metrics, g.name);
    const cols = [...new Set(ordered.flatMap(g => columnsFor(g, disp)))].slice(0, 6);
    return chips + `<div class="scroll"><table class="mt"><tr><th>测试集</th>${cols.map(x => metricTh(c, x)).join('')}</tr>` +
      ordered.map(g => `<tr>${groupTd(d, g.name)}${cols.map(x => `<td class="num">${cellHtml(vals(g), x, disp.ratio)}</td>`).join('')}</tr>`).join('') + '</table></div>';
  }
  // several runs: one test set at a time, a row per run
  const key = attKey(a);
  if (!ordered.some(g => g.name === ui.attGroup[key])) ui.attGroup[key] = defaultName;
  const g = ordered.find(x => x.name === ui.attGroup[key]), cols = columnsFor(g, disp).slice(0, 6);
  return chips + (ordered.length > 1 ? `<div class="chips" style="margin:4px 0">${ordered.map(x =>
      `<a href="#" class="chip${x === g ? ' on' : ''}" data-agroup="${esc(x.name)}" data-att="${esc(key)}">${esc(c.groupName(x.name))}</a>`).join('')}</div>` : '') +
    `<div class="scroll"><table class="mt"><tr><th>运行</th>${cols.map(x => metricTh(c, x)).join('')}</tr>` +
    scored.filter(r => B.hasNumber(B.groupValues((r.run || {}).metrics, g.name))).map(r => { const v = B.groupValues((r.run || {}).metrics, g.name);
      return `<tr><td><a href="${plink(name, 'runs', runKey(r))}" title="${esc(runKey(r))}">${esc(lab(r))}</a></td>${cols.map(x => `<td class="num">${cellHtml(v, x, disp.ratio)}</td>`).join('')}</tr>`; }).join('') +
    '</table></div>';
}

// ------------------------------------------------------------ 运行: the comparison table on the left, the selected run on the right
const SHOW_GROUPS = 6;
// ============================================================ 运行: one table of runs (sortable, filtered), or a cross table
// Everything the page shows is in its address, so a refresh or a shared link keeps it:
//   view   '' table | pivot cross table | data the data runs        g      test set (metric group)
//   sort   name | att | date | s:<setting> | m:<metric>, desc 1/0   where  setting filters "key:value;key:value" (parts URI-encoded)
//   att    attempt   track  track   st  status   all  1 = every metric column   rows / cols / metric  the cross table
function runsQuery(r) {
  const q = r.query || {};
  const where = String(q.where || '').split(';').filter(Boolean).map(part => {
    const i = part.indexOf(':');
    if (i < 0) return null;
    try { return [decodeURIComponent(part.slice(0, i)), decodeURIComponent(part.slice(i + 1))]; } catch (e) { return null; }
  }).filter(Boolean);
  return {view: ['pivot', 'data'].includes(q.view) ? q.view : '', g: q.g == null ? null : q.g === '~' ? '' : q.g, sort: q.sort || null,
    desc: q.desc === '1' ? true : q.desc === '0' ? false : null, where, att: q.att || '', track: q.track || '', st: q.st || '',
    all: q.all === '1', rows: q.rows || null, cols: q.cols ? q.cols.split(',').filter(Boolean) : null, metric: q.metric || null};
}
const whereText = w => w.map(([k, v]) => encodeURIComponent(k) + ':' + encodeURIComponent(v)).join(';');
function runsLink(d, Q, change = {}, item = null) {  // the runs page with some of Q changed; g '~' is the group of top-level numbers
  const x = Object.assign({}, Q, change);
  return plink(d.name, 'runs', item, {view: x.view || null, g: x.g === '' ? '~' : x.g, sort: x.sort, desc: x.sort && x.desc != null ? (x.desc ? '1' : '0') : null,
    where: x.where.length ? whereText(x.where) : null, att: x.att || null, track: x.track || null, st: x.st || null,
    all: x.all ? '1' : null, rows: x.rows, cols: x.cols && x.cols.length ? x.cols.join(',') : null, metric: x.metric});
}
// a metric's direction: display.headline for its own group, else setup.json
function metricBetter(c, disp, g, col) {
  const h = disp.headline;
  if (h && h.metric === col && (h.group === g || !g)) return h.better;
  const m = c.setup.metrics.get(col);
  return m && (m.better === 'higher' || m.better === 'lower') ? m.better : null;
}
// the metric columns to show: display.primary_metrics, else the group's columns in the order the runs wrote them,
// without counts (n, of), the parts of ratios, and columns that are the same in every run shown
function runMetricCols(disp, g, shown, vals) {
  const primary = disp.primary.filter(x => g.cols.includes(x));
  if (primary.length) return primary;
  const parts = new Set(Object.values(disp.ratio).flat());
  const same = col => shown.length > 1 && new Set(shown.map(x => B.valueText(vals(x)[col]))).size <= 1;
  return g.cols.filter(col => !parts.has(col) && col !== 'n' && col !== 'of' && !same(col)).slice(0, 6);
}
const isSpot = v => typeof v.n === 'number' && typeof v.of === 'number' && v.n < v.of;  // scored part of the set only
function tabRuns(d, r) {
  const c = ctx(d), name = d.name, Q = runsQuery(r);
  const newest = x => String((x.run || {}).started || '') + ' ' + x.id;
  const all = [...d.runs].sort((a, b) => newest(b).localeCompare(newest(a)));
  if (!all.length) return '<div class="card"><div class="empty">还没有运行记录（runs/）。</div></div>';
  const sel = (r.item != null && all.find(x => runKey(x) === r.item)) || null;
  const dataRuns = all.filter(x => (x.run || {}).kind === 'data'), evalRuns = all.filter(x => (x.run || {}).kind !== 'data');
  const data = (Q.view === 'data' || (sel && (sel.run || {}).kind === 'data')) && dataRuns.length || !evalRuns.length;
  Q.view = data ? 'data' : Q.view === 'pivot' ? 'pivot' : '';
  const disp = data ? B.parseDisplay(null) : c.disp;
  const cfg = new Map(all.map(x => [x, B.flatten((x.run || {}).config)]));
  const attOf = x => { const a = c.attemptOf(x); return a ? attKey(a) : String((x.run || {}).attempt || ''); };

  // which runs: the kind, then attempt, track, status and setting filters
  const base = data ? dataRuns : evalRuns;
  let runs = base;
  if (Q.track) runs = runs.filter(x => { const a = c.attemptOf(x); return a && a.meta.track === Q.track; });
  if (Q.att) runs = runs.filter(x => attOf(x) === Q.att);
  if (Q.st) runs = runs.filter(x => ((x.run || {}).status || '未写') === Q.st);
  for (const [k, v] of Q.where) runs = runs.filter(x => k in cfg.get(x) && B.valueText(cfg.get(x)[k]) === v);

  // which test set
  const groups = B.metricGroups(runs.map(x => (x.run || {}).metrics));
  const {ordered, defaultName} = B.arrangeGroups(groups, disp);
  let gname = groups.some(x => x.name === Q.g) ? Q.g : defaultName;
  if (sel && Q.g == null && !B.hasNumber(B.groupValues((sel.run || {}).metrics, gname ?? ''))) {  // show the picked run's own group
    const own = B.metricGroups([(sel.run || {}).metrics]).find(x => x.runs);
    if (own && groups.some(x => x.name === own.name)) gname = own.name;
  }
  const g = groups.find(x => x.name === gname);
  const vals = x => g ? B.groupValues((x.run || {}).metrics, g.name) : {};
  const shown = g ? runs.filter(x => B.hasNumber(vals(x))) : [];
  const bare = runs.filter(x => !groups.some(y => B.hasNumber(B.groupValues((x.run || {}).metrics, y.name))));

  // controls
  const counts = (list, f) => { const m = new Map(); for (const x of list) { const k = f(x); if (k) m.set(k, (m.get(k) || 0) + 1); } return m; };
  const select = (key, label, opts) => opts.length > 1 || Q[key] ? `<label class="rq"><span class="small">${label}</span><select data-rq="${key}"><option value="">全部</option>` +
    opts.map(([v, t, n]) => `<option value="${esc(v)}"${Q[key] === v ? ' selected' : ''}>${esc(t)}（${n}）</option>`).join('') + '</select></label>' : '';
  const attCounts = counts(base, attOf), atts = [...attCounts].map(([id, n]) => { const a = c.attById.get(id); return [id, a ? attName(a) : id, n]; });
  const trackCounts = counts(base, x => { const a = c.attemptOf(x); return a && typeof a.meta.track === 'string' ? a.meta.track : ''; });
  const tracks = [...trackCounts].map(([id, n]) => { const t = c.trackById.get(id); return [id, t ? t.meta.title || id : id, n]; });
  const stats = [...counts(base, x => (x.run || {}).status || '未写')];
  const label = x => esc(c.groupName(x.name));
  const visible = ordered.slice(0, SHOW_GROUPS);
  if (g && !visible.includes(g)) visible.push(g);
  const more = ordered.filter(x => !visible.includes(x));
  const controls = `<div class="card"><div class="filters">
      <div class="chips">${evalRuns.length ? `<a class="chip${!data ? ' on' : ''}" href="${runsLink(d, Q, {view: Q.view === 'pivot' ? 'pivot' : '', g: null, where: [], att: '', st: '', track: ''})}">评测和训练 ${evalRuns.length}</a>` : ''}
        ${dataRuns.length ? `<a class="chip${data ? ' on' : ''}" href="${plink(name, 'runs', null, {view: 'data'})}">数据 ${dataRuns.length}</a>` : ''}</div>
      ${data ? '' : `<div class="chips"><a class="chip${Q.view === '' ? ' on' : ''}" href="${runsLink(d, Q, {view: ''})}">表格</a><a class="chip${Q.view === 'pivot' ? ' on' : ''}" href="${runsLink(d, Q, {view: 'pivot'})}">交叉表</a></div>`}
      ${select('att', '实验', atts)}${data ? '' : select('track', '支线', tracks)}${select('st', '状态', stats.map(([s, n]) => [s, s, n]))}
    </div>
    ${Q.where.length ? `<div class="chips wherechips"><span class="small">只看</span>${Q.where.map(([k, v], i) =>
      `<a class="chip on" href="${runsLink(d, Q, {where: Q.where.filter((_, j) => j !== i)})}" title="去掉这个条件">${esc(k)} = ${esc(v)} ✕</a>`).join('')}</div>` : ''}
    ${ordered.length > 1 ? `<div class="tabs"><span class="small">测试集</span>` +
      visible.map(x => `<a class="chip${x === g ? ' on' : ''}" data-g="${esc(x.name)}" href="${runsLink(d, Q, {g: x.name, sort: null, desc: null, metric: null}, r.item)}" title="${esc((c.setup.datasets.get(x.name) || {}).about || x.name)}">${label(x)} <span class="small">${x.runs}</span></a>`).join('') +
      (more.length ? `<select data-rq="g"><option value="">更多（${more.length}）</option>` + more.map(x => `<option value="${esc(x.name === '' ? '~' : x.name)}">${label(x)}（${x.runs}）</option>`).join('') + '</select>' : '') + '</div>' : ''}
    ${g ? groupNote(d, g.name, shown.length, runs.length - shown.length - bare.length) : ''}
    ${disp.problems.length ? `<div class="dnote">project.json 的 display 里有用不上的设置：${disp.problems.map(esc).join('；')}。已按默认方式显示。</div>` : ''}
    </div>`;

  const parts = {c, disp, g, shown, vals, cfg, attOf, sel};
  const body = Q.view === 'pivot' ? runsPivot(d, r, Q, parts) : runsTable(d, r, Q, parts);
  const bareHtml = bare.length ? `<div class="bare"><span class="small">还没有指标的运行：</span>` +
    bare.map(x => `<a href="${runsLink(d, Q, {}, runKey(x))}">${esc(c.label(x))}</a>`).join('') + '</div>' : '';
  if (Q.view === 'pivot') return controls + `<div class="card">${body}</div>`;
  if (!sel) return controls + `<div class="card">${body}${bareHtml}<div class="pad small">点表头排序，再点一次反过来；点一个设置值，只看这些运行；点一行，看这次运行属于哪次尝试、各测试集上的指标、配置、产物和曲线。</div></div>`;
  return controls + `<div class="md2 wide"><div class="card" style="margin:0">${body}${bareHtml}</div><div>${runDetail(d, sel)}</div></div>`;
}
// The table: one row per run; the settings that differ between these runs, then the metrics of the test set,
// each metric cell with a bar from zero and the best one marked. Sorted by the address, else by the headline metric.
function runsTable(d, r, Q, {c, disp, g, shown, vals, cfg, attOf, sel}) {
  if (!shown.length) return '<div class="empty">这个测试集下没有运行。</div>';
  const {cols: settings, common} = B.settingColumns(shown.map(x => (x.run || {}).config), {prefer: disp.settings});
  let metrics = Q.all ? g.cols : runMetricCols(disp, g, shown, vals);
  if (sel && !Q.all) metrics = metrics.slice(0, 3);
  const better = col => metricBetter(c, disp, g.name, col);
  // the sort: the address, else the headline metric (or the first one with a direction) best first, else newest first
  let sort = Q.sort, desc = Q.desc;
  const ok = k => k === 'name' || k === 'att' || k === 'date' || (k.startsWith('s:') && settings.includes(k.slice(2))) || (k.startsWith('m:') && g.cols.includes(k.slice(2)));
  if (!sort || !ok(sort)) {
    const h = disp.headline && disp.headline.group === g.name && g.cols.includes(disp.headline.metric) ? disp.headline.metric : metrics.find(col => better(col));
    sort = h ? 'm:' + h : 'date';
    desc = null;
  }
  const firstDesc = k => k.startsWith('m:') ? better(k.slice(2)) !== 'lower' : k === 'date';   // best first, newest first, A to Z
  if (desc == null) desc = firstDesc(sort);
  const valueOf = k => k === 'name' ? x => c.label(x) : k === 'att' ? attOf : k === 'date' ? x => (x.run || {}).started || null
    : k.startsWith('s:') ? x => cfg.get(x)[k.slice(2)] : x => vals(x)[k.slice(2)];
  const rows = B.sortRows(shown, valueOf(sort), desc);
  const th = (k, text, cls = '', title = '') => {
    const on = sort === k, next = on ? !desc : firstDesc(k);
    return `<th class="${cls}${on ? ' sorted' : ''}"${title ? ` title="${esc(title)}"` : ''}><a href="${runsLink(d, Q, {sort: k, desc: next}, r.item)}">${text}${on ? (desc ? ' ▼' : ' ▲') : ''}</a></th>`;
  };
  const mth = col => { const m = c.setup.metrics.get(col), b = better(col);   // the direction is in the tooltip; arrows are the sort
    return th('m:' + col, esc(c.metricName(col)), 'num', [m && m.about && m.about.replace(/[。.；;]\s*$/, ''), b ? BETTER[b] : '没有声明方向', col].filter(Boolean).join('；')); };
  const scales = new Map(metrics.map(col => [col, B.scale(shown.filter(x => !isSpot(vals(x))).map(x => vals(x)[col]), better(col))]));
  const setCell = (x, k) => {
    const f = cfg.get(x);
    if (!(k in f)) return '<td class="faint">—</td>';
    const t = B.valueText(f[k]), shown = t.length > 40 ? t.slice(0, 39) + '…' : t;
    return `<td class="sv"><a href="${runsLink(d, Q, {where: [...Q.where.filter(([kk]) => kk !== k), [k, t]]})}" title="只看 ${esc(k)} = ${esc(t)} 的运行">${esc(shown)}</a></td>`;
  };
  const metCell = (x, col) => {
    const v = vals(x), val = v[col], s = scales.get(col), spot = isSpot(v), num = typeof val === 'number' && isFinite(val);
    const best = num && !spot && s.best(val);
    return `<td class="n mcell${best ? ' best' : ''}${spot ? ' spot' : ''}"${spot ? ` title="抽查：只评了 ${B.num(v.n)}/${B.num(v.of)} 条，不参与比较"` : ''}>` +
      (num ? `<i style="width:${(s.pos(val) * 100).toFixed(1)}%"></i>` : '') + `<span>${cellHtml(v, col, disp.ratio)}</span></td>`;
  };
  const multiAtt = new Set(shown.map(attOf)).size > 1;
  const row = x => {
    const rr = x.run || {}, a = attOf(x), att = c.attById.get(a);
    return `<tr class="run${x === sel ? ' open' : ''}" data-href="${runsLink(d, Q, {}, runKey(x))}">
      <td class="rname"><b>${esc(c.label(x))}</b>${c.label(x) !== runKey(x) ? `<div class="small">${esc(runKey(x))}</div>` : ''}` +
      `${rr.status && rr.status !== '完成' ? ' ' + B.pill(rr.status) : ''} ${statePill(x.state)}</td>
      ${multiAtt ? `<td class="small">${att ? `<a href="${plink(d.name, 'flow', 'a:' + a)}">${esc(attName(att))}</a>` : esc(a) || '—'}</td>` : ''}
      ${settings.map(k => setCell(x, k)).join('')}${metrics.map(col => metCell(x, col)).join('')}
      <td class="small">${esc(B.day(rr.started) || '—')}</td></tr>`;
  };
  const head = `<tr>${th('name', '运行')}${multiAtt ? th('att', '实验') : ''}${settings.map(k => th('s:' + k, esc(k), 'set')).join('')}${metrics.map(mth).join('')}${th('date', '开始')}</tr>`;
  const commonHtml = common.length ? `<div class="tmeta">这些运行共同的设置：${common.slice(0, 10).map(([k, v]) => `<code>${esc(k)}</code> = ${esc(B.valueText(v))}`).join('，')}${common.length > 10 ? ' ……' : ''}</div>` : '';
  const more = g.cols.length > metrics.length || Q.all ? `<a class="allcols" href="${runsLink(d, Q, {all: !Q.all}, r.item)}">${Q.all ? '只显示主要列' : `显示全部指标（${g.cols.length}）`}</a>` : '';
  return `${commonHtml}<div class="scroll"><table class="runs">${head}${rows.map(row).join('')}</table></div>
    <div class="tmeta">${shown.length} 次运行${more ? ' · ' + more : ''}${shown.some(x => isSpot(vals(x))) ? ' · 斜纹是抽查，不参与比较' : ''}</div>`;
}
// The cross table: rows are the values of one setting, columns of one or two others (grouped by the first), each
// cell the mean of a metric over its runs (spot checks left out unless that is all there is), shaded by the
// metric's direction; a cell links to the table narrowed to its runs.
function runsPivot(d, r, Q, {c, disp, g, shown, vals, cfg, attOf}) {
  if (!shown.length) return '<div class="empty">这个测试集下没有运行。</div>';
  const {cols: settings} = B.settingColumns(shown.map(x => (x.run || {}).config), {prefer: disp.settings, max: 12});
  if (!settings.length) return '<div class="empty">这些运行之间没有不同的设置，排不成交叉表。在上面选一个实验，或者看表格。</div>';
  const kinds = k => new Set(shown.map(x => B.valueText(cfg.get(x)[k]))).size;
  const byKinds = [...settings].sort((a, b) => kinds(b) - kinds(a));                       // stable: record order on ties
  const rowKey = settings.includes(Q.rows) ? Q.rows : byKinds[0];
  let colKeys = (Q.cols || []).filter(k => settings.includes(k) && k !== rowKey).slice(0, 2);
  if (!colKeys.length) {
    const rest = byKinds.filter(k => k !== rowKey);
    colKeys = rest.slice(0, 1);
    if (rest[1] && kinds(rest[0]) * kinds(rest[1]) <= 12) colKeys.push(rest[1]);
  }
  const metrics = runMetricCols(disp, g, shown, vals);
  const h = disp.headline && disp.headline.group === g.name && g.cols.includes(disp.headline.metric) ? disp.headline.metric : null;
  const metric = g.cols.includes(Q.metric) ? Q.metric : h || metrics[0] || g.cols[0];
  const better = metricBetter(c, disp, g.name, metric);
  const text = (x, k) => k in cfg.get(x) ? B.valueText(cfg.get(x)[k]) : '—';
  const byTime = [...shown].sort((a, b) => String((a.run || {}).started || '').localeCompare(String((b.run || {}).started || '')));
  const P = B.pivot(byTime, x => text(x, rowKey), x => colKeys.map(k => text(x, k)), x => vals(x)[metric], x => isSpot(vals(x)));
  // columns grouped by the first setting, in order of first appearance of each value
  const order = colKeys.map((k, i) => [...new Set(P.cols.map(cv => cv[i]))]);
  const cols = [...P.cols].sort((a, b) => colKeys.reduce((acc, _, i) => acc || order[i].indexOf(a[i]) - order[i].indexOf(b[i]), 0));
  const means = [];
  for (const rv of P.rows) for (const cv of cols) { const x = P.cell(rv, cv); if (x && !x.dim) means.push(x.mean); }
  const sc = B.scale(means), dirSc = B.scale(means, better);
  const lo = Math.min(...means), hi = Math.max(...means);
  const cell = (rv, cv) => {
    const x = P.cell(rv, cv);
    if (!x) return '<td class="pv empty">—</td>';
    const shade = x.dim ? 0 : better ? dirSc.good(x.mean) ?? 0 : hi > lo ? (x.mean - lo) / (hi - lo) : 0;
    const cls = 'pv' + (x.dim ? ' spot' : dirSc.best(x.mean) ? ' best' : '') + (better ? '' : ' flat');
    const where = [...Q.where.filter(([k]) => k !== rowKey && !colKeys.includes(k)),
      ...[[rowKey, rv], ...colKeys.map((k, i) => [k, cv[i]])].filter(([, v]) => v !== '—')];
    const tip = x.items.map(it => `${runKey(it)}：${B.num(vals(it)[metric])}${isSpot(vals(it)) ? '（抽查）' : ''}`).join('\n');
    return `<td class="${cls}" style="--g:${shade.toFixed(3)}" title="${esc(tip)}"><a href="${runsLink(d, Q, {view: '', where})}">` +
      `<span class="pvv">${B.num(x.mean)}</span>${x.n > 1 ? ` <span class="small">×${x.n}</span>` : ''}</a></td>`;
  };
  const corner = `<th class="pvk"${colKeys.length > 1 ? ' rowspan="2"' : ''}>${esc(rowKey)}<div class="small">${colKeys.map(esc).join(' · ')}</div></th>`;
  let head;
  if (colKeys.length > 1) {
    const groups = [];
    for (const cv of cols) { const last = groups[groups.length - 1]; if (last && last[0] === cv[0]) last[1]++; else groups.push([cv[0], 1]); }
    head = `<tr>${corner}${groups.map(([v, n]) => `<th class="pvc" colspan="${n}">${esc(v)}</th>`).join('')}</tr>` +
      `<tr>${cols.map(cv => `<th class="pvc">${esc(cv[1])}</th>`).join('')}</tr>`;
  } else head = `<tr>${corner}${cols.map(cv => `<th class="pvc">${esc(cv[0])}</th>`).join('')}</tr>`;
  const rowsHtml = P.rows.map(rv => `<tr class="pvr"><th class="pvh">${esc(rv)}</th>${cols.map(cv => cell(rv, cv)).join('')}</tr>`).join('');
  const opt = (v, t, on) => `<option value="${esc(v)}"${on ? ' selected' : ''}>${esc(t)}</option>`;
  const pick = (key, list, cur, none) => `<select data-rq="${key}">${none ? opt('', none, !cur) : ''}${list.map(k => opt(k, k, k === cur)).join('')}</select>`;
  const metricName = k => c.metricName(k);
  const controls = `<div class="pvsel"><label>行 ${pick('rows', settings, rowKey)}</label>
      <label>列 ${pick('cols', settings.filter(k => k !== rowKey), colKeys[0])} × ${pick('cols', settings.filter(k => k !== rowKey), colKeys[1], '（不分）')}</label>
      <label>格子 <select data-rq="metric">${g.cols.map(k => opt(k, metricName(k), k === metric)).join('')}</select></label></div>`;
  const atts = new Set(shown.map(attOf)).size;
  return `${controls}${atts > 1 ? `<div class="dnote">这些运行来自 ${atts} 个实验；先在上面选一个实验，交叉表更清楚。</div>` : ''}
    <div class="scroll"><table class="pivot">${head}${rowsHtml}</table></div>
    <div class="tmeta">格子是「${esc(metricName(metric))}」${better ? '（' + BETTER[better] + '，越好绿色越深，最好的一格加粗）' : '（没有声明方向，只按大小深浅）'}；
      ×n 是 n 个运行的平均；斜纹是抽查，不算进比较；点一格，看这一格的运行。</div>`;
}
function groupNote(d, gname, n, missing) {
  const c = ctx(d), ds = c.setup.datasets.get(gname);
  const what = ds ? `<b>${esc(ds.name || gname)}</b>${ds.size != null ? `（${B.num(ds.size)} 条）` : ''}：${esc(ds.about || '')} <a href="${plink(d.name, 'setup', 'ds:' + gname)}">定义 →</a>`
    : gname ? `分组「${esc(gname)}」在 setup.json 里没有定义` : '没有分组的指标';
  return `<div class="tmeta">${what}<br>本组 ${n} 次运行${missing ? `，另有 ${missing} 次没有这组指标` : ''}</div>`;
}
function runDetail(d, x) {
  const c = ctx(d), rr = x.run || {}, s = x.series, p = placeOfRun(d, x);
  const facts = [['运行编号', runKey(x)], ['类型', rr.kind], ['服务器', rr.server ? rr.server + gpusText(rr) : null], ['开始', B.when(rr.started)], ['结束', B.when(rr.ended)],
    ['tmux', rr.tmux], ['PID', rr.pid], ['commit', rr.commit], ['续自', rr.resumed_from], ['目录', x.dir]].filter(([, v]) => v != null && v !== '');
  // all test sets this run has numbers for, primary columns
  const groups = B.metricGroups([rr.metrics]).filter(g => g.runs), {ordered} = B.arrangeGroups(groups, c.disp);
  const cols = ui.runAllCols ? [...new Set(ordered.flatMap(g => g.cols))] : [...new Set(ordered.flatMap(g => columnsFor(g, c.disp)))].slice(0, 6);
  const allN = new Set(ordered.flatMap(g => g.cols)).size;
  const metricsHtml = ordered.length ? `<div class="scroll"><table class="mt"><tr><th>测试集</th>${cols.map(col => metricTh(c, col)).join('')}</tr>` +
    ordered.map(g => { const v = B.groupValues(rr.metrics, g.name); return `<tr>${groupTd(d, g.name)}${cols.map(col => `<td class="num">${cellHtml(v, col, c.disp.ratio)}</td>`).join('')}</tr>`; }).join('') +
    `</table></div>${allN > cols.length || ui.runAllCols ? `<div class="small"><a href="#" data-runallcols>${ui.runAllCols ? '只显示主要列' : `显示全部列（${allN}）`}</a></div>` : ''}` : '<div class="none">没有指标</div>';
  const samples = x.samples_head || [];
  const sampleCols = [...new Set(samples.flatMap(o => Object.keys(B.flatten(o))))].slice(0, 8);
  return `<div class="detail">${crumbs(d, p)}<h2>${esc(c.label(x))} ${B.pill(rr.status)} ${statePill(x.state)}</h2>
    <div class="dmeta">${facts.map(([k, v]) => `<span><span class="small">${k}</span> ${esc(v)}</span>`).join('')}</div>
    <div class="sec first"><h3>指标</h3>${metricsHtml}</div>
    ${s && s.keys && s.keys.length ? `<div class="sec"><h3>曲线</h3><div class="charts">${s.keys.slice(0, 12).map(k => B.chart(s.points, k)).join('')}</div>
      <div class="small">metrics.jsonl 共 ${s.total} 行${s.downsampled ? `，图上抽样显示 ${s.points.length} 个点` : ''}${s.bad_lines ? `，${s.bad_lines} 行无法解析` : ''}</div></div>` : ''}
    ${x.images.length ? `<div class="sec"><h3>图片</h3><div class="imgs">${x.images.map(im => `<a href="${esc(fileUrl(d.name, im))}" target="_blank" rel="noopener">` +
      `<img loading="lazy" src="${esc(fileUrl(d.name, im))}" alt="${esc(im)}" title="${esc(im)}"></a>`).join('')}</div></div>` : ''}
    ${rr.config != null ? `<div class="sec"><h3>配置</h3>${B.kvTable(rr.config)}</div>` : ''}
    ${rr.artifacts != null ? `<div class="sec"><h3>产物</h3>${B.kvTable(rr.artifacts)}</div>` : ''}
    ${samples.length ? `<div class="sec"><h3>样本输出（前 ${samples.length} 行）</h3><div class="scroll"><table class="mt"><tr>${sampleCols.map(k => `<th>${esc(k)}</th>`).join('')}</tr>` +
      samples.map(o => { const f = B.flatten(o); return `<tr>${sampleCols.map(k => `<td style="white-space:normal">${esc(f[k] == null ? '' : typeof f[k] === 'object' ? JSON.stringify(f[k]) : f[k])}</td>`).join('')}</tr>`; }).join('') +
      '</table></div></div>' : ''}</div>`;
}

// ------------------------------------------------------------ 样例: the worked example, then the probe set (list + one probe)
function tabExamples(d, r) {
  const c = ctx(d), name = d.name, w = d.example && d.example.walkthrough, probe = (d.example && d.example.probe_head) || [];
  let walk = '<div class="empty">还没有样例（examples/walkthrough.md）。</div>';
  if (w) {
    const parts = B.splitFull(w.body), open = ui.exampleOpen;
    if (!w.body.trim()) walk = '<div class="pad faint">文件是空的</div>';
    else if (parts) walk = `<div class="md pad">${B.md(open ? w.body : parts.head)}</div><div class="more"><button data-example>${open ? '收起完整原文' : '展开完整原文'}</button>
        <span class="small">完整原文约 ${Math.max(1, Math.round(parts.full.length / 1000))} 千字</span></div>`;
    else walk = `<div class="md pad${open ? '' : ' clip'}" id="exbody">${B.md(w.body)}</div><div class="more" id="exmore"><button data-example>${open ? '收起' : '展开全文'}</button></div>`;
  }
  const walkCard = `<div class="card"><h2>一个例子从头走到底${w ? ` <span class="meta">${esc(w.file)} · ${B.ago(w.mtime, d.now)}更新</span>` : ''}</h2>${walk}</div>`;
  if (!probe.length) return walkCard;
  const correct = p => { const dec = B.flatten(p.decisions || {}); const ks = Object.keys(dec); return [ks.filter(k => dec[k] === p.label).length, ks.length]; };
  const sel = (r.item != null && probe.find(p => String(p.id) === r.item)) || probe[0];
  const items = probe.map(p => { const [k, n] = correct(p);
    return `<a class="li${p === sel ? ' on' : ''}" href="${plink(name, 'examples', String(p.id))}"><div class="row1"><span class="name">${esc(p.id ?? '')}</span>
      ${n ? `<span class="small">${k}/${n} 组判对</span>` : ''}</div><div class="row2"><span>${esc(c.groupName(p.set))}</span>${p.label != null ? `<span>标准答案 ${esc(p.label)}</span>` : ''}</div></a>`; }).join('');
  const dec = B.flatten(sel.decisions || {});
  const decRows = Object.entries(dec).map(([k, v]) => {
    const m = c.setup.methods.find(x => x.id === k);
    return `<tr><td>${m ? `<a href="${plink(name, 'setup', 'method:' + k)}">${esc(m.name || k)}</a>` : esc(k)}</td><td>${esc(v)}</td><td>${v === sel.label ? '<span class="pill t-ok">对</span>' : '<span class="pill t-bad">错</span>'}</td></tr>`;
  }).join('');
  const inputs = sel.state && typeof sel.state === 'object' ? Object.entries(sel.state).map(([k, v]) =>
    `<div class="sec"><h3>${esc({policy: '策略', conversation: '对话', context: '上下文', proposed_action: '待执行的操作'}[k] || k)}</h3><pre class="plain">${esc(typeof v === 'string' ? v : JSON.stringify(v, null, 2))}</pre></div>`).join('') : '';
  const probeDetail = `<div class="detail"><h2>${esc(sel.id ?? '')} ${sel.label != null ? `<span class="pill t-run">标准答案 ${esc(sel.label)}</span>` : ''}</h2>
    <div class="dmeta"><span>${esc(c.groupName(sel.set))}</span></div>
    ${decRows ? `<div class="sec first"><h3>各对照组的判定</h3><div class="scroll"><table class="mt"><tr><th>对照组</th><th>判定</th><th></th></tr>${decRows}</table></div></div>` : ''}
    ${inputs}${sel.meta ? `<div class="sec"><h3>样例属性</h3>${B.kvTable(sel.meta)}</div>` : ''}</div>`;
  return walkCard + `<div class="card"><h2>观察样例 <span class="meta">examples/probe.jsonl · ${probe.length} 条 · 每条看各对照组的判定</span></h2></div>
    <div class="md2"><div class="list"><div class="items">${items}</div></div>${probeDetail}</div>`;
}
function fitExample() {
  const body = $('exbody'), more = $('exmore');
  if (!body || !more || ui.exampleOpen) return;
  if (body.scrollHeight <= body.clientHeight + 4) { body.classList.remove('clip'); more.hidden = true; }
}

// ------------------------------------------------------------ 文档: general documents (version and track notes live in the roadmap)
// documents with where they belong in the flow (front matter about: v:/t:/a:), their kind, what they replace,
// and the user's mark (看过 / 确认; 有更新 when the file changed after the mark). Figures (board/figures/*.html,
// 图解) are one kind: their title and about come from the page's <title> and <meta>, and they are shown in a frame.
const DOC_KINDS = ['方案', '观察', '复盘', '报告', '说明', '笔记', '图解'];
const FIGURE_MAX = 10 * 1024 * 1024;  // the server does not serve larger files
function docItems(d) {
  if (d._docs) return d._docs;
  const out = [], marks = d.marks || {};
  const base = x => x.file.split('/').pop().replace(/\.(md|html)$/, '');
  const add = (x, extra) => {
    const m = x.meta || {}, mark = marks[x.file] || null;
    const changed = !!(mark && typeof mark.mtime === 'number' && x.mtime > mark.mtime + 1);
    out.push(Object.assign({file: x.file, body: x.body, mtime: x.mtime, title: m.title || base(x),
      about: typeof m.about === 'string' && ABOUT_RE.test(m.about) ? m.about : null,
      kind: typeof m.kind === 'string' ? m.kind : null, replaces: (Array.isArray(m.replaces) ? m.replaces : m.replaces ? [m.replaces] : []).map(String),
      mark, state: !mark ? 'unread' : changed ? 'changed' : mark.mark === '确认' ? 'ok' : 'seen'}, extra));
  };
  if (d.plan) add(d.plan, {id: 'plan', title: '整体方案', kind: '方案'});
  for (const x of d.docs || []) {
    const root = !x.file.startsWith('docs/');
    add(x, {id: root ? x.file : x.file.slice(5)});
    if (root) Object.assign(out[out.length - 1], {title: (x.meta || {}).title || (x.file === 'NOTES.md' ? '进度笔记' : x.file === 'REPORT.md' ? '总报告' : x.file),
      kind: (x.meta || {}).kind || (x.file === 'NOTES.md' ? '笔记' : '报告')});
  }
  for (const x of d.figures || []) add(x, {id: x.file.slice('figures/'.length), kind: '图解', figure: true,
    question: typeof (x.meta || {}).question === 'string' ? x.meta.question : null, size: x.size});
  // replaces: a document names older ones by file name (with or without .md) or by its id here
  for (const x of out) for (const r of x.replaces) {
    const old = out.find(y => y !== x && (y.id === r || y.file === r || base(y) === r.replace(/\.md$/, '') || y.file === 'docs/' + r));
    if (old) old.replacedBy = x;
  }
  return d._docs = out;
}
const DOC_STATE = {unread: ['未看', 'other'], seen: ['看过', 'part'], ok: ['已确认', 'ok'], changed: ['有更新', 'todo']};
const docBadge = x => `<span class="pill t-${DOC_STATE[x.state][1]}">${DOC_STATE[x.state][0]}</span>`;
const docsAbout = (d, key) => docItems(d).filter(x => x.about === key);
// where a document or paper sits: a flow step (v: t: a:), a method step (m:), a task card (k:) or a decision (d:)
const ABOUT_RE = /^[vtamkd]:/;
function placeLabel(d, key) {
  const c = ctx(d);
  if (!key) return '整个项目';
  const id = key.slice(2);
  if (key.startsWith('k:')) { const t = work(d).task(id); return t ? '任务 · ' + taskTitle(t) : null; }
  if (key.startsWith('d:')) { const x = work(d).decision(id); return x ? '决定 · ' + (x.meta.title || id) : null; }
  if (key.startsWith('m:')) { const M = methodIndex(d), x = M.byId.get(id); return x ? '方法 · ' + M.path(x).map(M.title).join(' › ') : null; }
  if (key.startsWith('v:')) { const v = c.verById.get(id); return v ? `版本 ${id}${v.meta.title ? ' · ' + v.meta.title : ''}` : null; }
  if (key.startsWith('t:')) { const t = c.trackById.get(id); return t ? '支线 · ' + (t.meta.title || id) : null; }
  const a = c.attById.get(id);
  if (!a) return null;
  const t = c.trackById.get(a.meta.track);
  return (t ? (t.meta.title || idOf(t)) + ' › ' : '') + '尝试 · ' + attName(a);
}
// "这一步的文档" under a flow step
function docsOfStep(d, key) {
  const docs = docsAbout(d, key);
  return docs.length ? `<div class="sec"><h3>这一步的文档 <span class="faint">${docs.length}</span></h3><ul class="doclist">${docs.map(x =>
    `<li>${docBadge(x)}${x.kind ? `<span class="tag2">${esc(x.kind)}</span>` : ''}<a href="${plink(d.name, 'docs', x.id)}">${esc(x.title)}</a>` +
    `${x.replacedBy ? ` <span class="small">已被「${esc(x.replacedBy.title)}」取代</span>` : ''}</li>`).join('')}</ul></div>` : '';
}
// what a step's detail lists: its documents, then its papers
const stepRefs = (d, key) => docsOfStep(d, key) + papersOfStep(d, key);
// documents and papers are grouped by where they sit: the whole project first, then the method steps,
// then the flow in its order, then task cards and decisions, then places that do not exist
function groupByPlace(d, items) {
  const L = d._flow || (d._flow = B.flowLayout(d)), order = new Map(L.nodes.map(n => [n.key, n.row]));
  const mOrder = new Map(methodIndex(d).steps.map((x, i) => ['m:' + x.meta.id, i]));
  const key = x => x.about && placeLabel(d, x.about) ? x.about : x.about ? '?' + x.about : '';
  const rank = g => g === '' ? -1 : g.startsWith('?') ? 1e9 : g.startsWith('m:') ? -1 + (mOrder.get(g) + 1) / 1e4
    : g.startsWith('k:') ? 1e8 : g.startsWith('d:') ? 1e8 + 1 : order.get(g) ?? 1e8 - 1;
  return [...new Set(items.map(key))].sort((a, b) => rank(a) - rank(b)).map(g => [g, items.filter(x => key(x) === g)]);
}
const placeHead = (d, g) => g === '' ? '整个项目' : g.startsWith('?') ? '找不到的位置：' + esc(g.slice(1))
  : `<a href="${refHref(d, g)}">${esc(placeLabel(d, g))}</a>`;
function tabDocs(d, r) {
  const all = docItems(d), name = d.name, q = r.query || {};
  if (!all.length) return '<div class="card"><div class="empty">还没有文档（board/plan.md、board/docs/*.md、board/figures/*.html 图解、项目根目录的 NOTES.md 和 REPORT.md）。各版本的方案在「流程」里，点版本节点就能看到。</div></div>';
  const f = q.f || '', k = q.k || '';
  const pass = x => (!f || x.state === f) && (!k || x.kind === k);
  const docs = all.filter(pass);
  const sel = all.find(x => x.id === r.item) || docs[0] || all[0];
  const qs = {f, k};
  const item = x => `<a class="li${x === sel ? ' on' : ''}${x.replacedBy ? ' old' : ''}" href="${plink(name, 'docs', x.id, qs)}">
      <div class="row1">${docBadge(x)}<span class="name">${esc(x.title)}</span>${x.kind ? `<span class="tag2">${esc(x.kind)}</span>` : ''}</div>
      <div class="row2"><span>${esc(x.file)}</span><span>${B.ago(x.mtime, d.now)}更新</span>${x.replacedBy ? `<span>已被「${esc(x.replacedBy.title)}」取代</span>` : ''}</div></a>`;
  const items = groupByPlace(d, docs).map(([g, xs]) => `<div class="tgroup">${placeHead(d, g)}</div>` + xs.map(item).join('')).join('');
  const count = st => all.filter(x => x.state === st).length;
  const chip = (label, nf, nk, n) => `<a class="chip${f === nf && k === nk ? ' on' : ''}" href="${plink(name, 'docs', r.item, {f: nf, k: nk})}">${label}${n != null ? ' ' + n : ''}</a>`;
  const kinds = DOC_KINDS.filter(x => all.some(y => y.kind === x));
  const filters = `<div class="filters"><div class="chips">${chip('全部', '', '', all.length)}${[['unread', '未看'], ['changed', '有更新'], ['seen', '看过'], ['ok', '已确认']]
      .filter(([st]) => count(st)).map(([st, l]) => chip(l, st, k, count(st))).join('')}</div>
    ${kinds.length ? `<div class="chips">${kinds.map(x => chip(x, f, k === x ? '' : x, null)).join('')}</div>` : ''}</div>`;
  const stage = sel.about ? placeLabel(d, sel.about) : '整个项目';
  const mk = (m, label) => `<button class="mk${sel.mark && sel.mark.mark === m && sel.state !== 'changed' ? ' on' : ''}" data-mark="${m}" data-file="${esc(sel.file)}">${label}</button>`;
  const markNote = sel.state === 'changed' ? `<div class="notice">你 ${isoAgo(sel.mark.at, d.now)}标了「${esc(sel.mark.mark)}」，之后这篇文档改过。看完新的再标一次。</div>`
    : sel.mark ? `<div class="small">${isoAgo(sel.mark.at, d.now)}标为「${esc(sel.mark.mark)}」</div>` : '';
  const rel = [sel.replacedBy ? `已被 <a href="${plink(name, 'docs', sel.replacedBy.id, qs)}">${esc(sel.replacedBy.title)}</a> 取代` : '',
    ...sel.replaces.map(x => { const o = all.find(y => y.replacedBy === sel && (y.id === x || y.file === x || y.file.endsWith('/' + x.replace(/\.md$/, '') + '.md')));
      return o ? `取代了 <a href="${plink(name, 'docs', o.id, qs)}">${esc(o.title)}</a>` : `取代了 ${esc(x)}`; })].filter(Boolean);
  return `<div class="md2"><div class="list">${filters}<div class="items">${items || '<div class="empty">没有符合条件的文档。</div>'}</div>
      <div class="pad small">文档开头写 about（属于哪一步）和 kind（类型），图解写在 &lt;meta name="about"&gt; 里，这里就按流程归类，格式见 BOARD_FORMAT.md。</div></div>
    <div class="detail"><h2>${esc(sel.title)} ${sel.kind ? `<span class="tag2">${esc(sel.kind)}</span>` : ''}${docBadge(sel)}</h2>
      <div class="dmeta"><span>属于：${sel.about && placeLabel(d, sel.about) ? `<a href="${refHref(d, sel.about)}">${esc(stage)}</a>` : sel.about ? '找不到 ' + esc(sel.about) : '整个项目'}</span>
        <span>${esc(sel.file)}</span>${sel.figure ? `<span>${sizeText(sel.size)}</span>` : ''}<span>${B.ago(sel.mtime, d.now)}更新</span>${rel.map(x => `<span>${x}</span>`).join('')}</div>
      <div class="marks">${mk('看过', '看过')}${mk('确认', '确认内容')}${sel.mark ? '<button class="mk" data-mark="" data-file="' + esc(sel.file) + '">清除标记</button>' : ''}
        <span class="small">「确认」表示你认可这篇的内容，会话改它之前会先问你</span></div>${markNote}
      ${sel.figure ? figureView(d, sel) : `<div class="md">${refs(B.md(sel.body), d)}</div>`}</div></div>`;
}

// ============================================================ 论文: the project's reading list (board/papers.jsonl), ticked 读过 here
// Sessions write the list; the only thing the page writes is the tick (a row of marks.jsonl, via /api/paper-mark).
function paperItems(d) {
  if (d._papers) return d._papers;
  const marks = d.paper_marks || {}, away = d.papers_elsewhere || {};
  return d._papers = (d.papers || []).map((p, i) => Object.assign({}, p, {i, mark: marks[p.id] || null, read: !!marks[p.id],
    must: p.priority === '必读', elsewhere: marks[p.id] ? [] : away[p.id] || [],
    about: typeof p.about === 'string' && ABOUT_RE.test(p.about) ? p.about : null}));
}
// unread before read, 必读 before 选读, then the order of the list
const sortPapers = ps => [...ps].sort((a, b) => (a.read - b.read) || (b.must - a.must) || (a.i - b.i));
const paperUrl = u => typeof u === 'string' && /^https?:\/\/[^\s"'<>]+$/i.test(u.trim()) ? u.trim() : null;  // never javascript: and the like
const authorsText = a => Array.isArray(a) ? a.slice(0, 3).map(String).join('、') + (a.length > 3 ? ' 等' : '') : typeof a === 'string' ? a : '';
function projectLabel(name) {
  const p = S.projects && (S.projects.projects || []).find(x => x.name === name);
  return (p && p.title) || name;
}
function paperRow(d, p, opts = {}) {
  const url = paperUrl(p.url), title = esc(String(p.title || p.id));
  const meta = [authorsText(p.authors), p.year, p.venue].filter(x => x != null && x !== '').map(x => esc(String(x))).join(' · ');
  const foot = [opts.place && p.about ? `属于：<a href="${refHref(d, p.about)}">${esc(placeLabel(d, p.about) || p.about)}</a>` : '',
    p.read ? `${isoAgo(p.mark.at, d.now)}勾了读过` : '',
    p.elsewhere.length ? '在 ' + p.elsewhere.map(n => `<a href="${plink(n, 'papers')}">${esc(projectLabel(n))}</a>`).join('、') + ' 勾过' : ''].filter(Boolean);
  return `<li class="paper${p.read ? ' read' : ''}" data-anchor="${esc(p.id)}">
    <label class="pcheck" title="${p.read ? '取消「读过」' : '读完了就勾上'}"><input type="checkbox" data-paper="${esc(p.id)}"${p.read ? ' checked' : ''}></label>
    <div class="pbody"><div class="ptitle">${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${title}</a>` : title}
      ${p.must ? '<span class="pill t-todo">必读</span>' : ''}${p.checked ? '' : '<span class="pill t-other" title="会话还没有打开链接核对过标题">未核实</span>'}</div>
      ${meta ? `<div class="small">${meta}</div>` : ''}
      ${p.why ? `<div class="pwhy">${esc(String(p.why))}</div>` : ''}
      ${p.summary ? `<div class="small psum">读后要点：${esc(String(p.summary))}</div>` : ''}
      ${foot.length ? `<div class="small faint">${foot.join(' · ')}</div>` : ''}</div></li>`;
}
// 「这一步的论文」 under a flow step, a method step, a task card or a decision
function papersOfStep(d, key) {
  const ps = sortPapers(paperItems(d).filter(p => p.about === key));
  return ps.length ? `<div class="sec"><h3>这一步的论文 <span class="faint">${ps.length}</span></h3><ul class="papers">${ps.map(p => paperRow(d, p)).join('')}</ul></div>` : '';
}
function tabPapers(d, r) {
  const all = paperItems(d), name = d.name, f = (r.query || {}).f || '';
  if (!all.length) return `<div class="card"><div class="empty">还没有论文清单。讨论方案时，会话会把用到的、建议你读的论文记进 board/papers.jsonl（每篇写为什么读、属于哪一步），这里就会列出来；读完了在这里打勾。</div></div>`;
  const read = all.filter(p => p.read).length, must = all.filter(p => p.must && !p.read).length;
  const list = all.filter(p => !f || (f === 'unread' ? !p.read : p.read));
  const chip = (label, nf, n) => `<a class="chip${f === nf ? ' on' : ''}" href="${plink(name, 'papers', null, {f: nf})}">${label} ${n}</a>`;
  const groups = groupByPlace(d, list).map(([g, ps]) => `<div class="tgroup">${placeHead(d, g)}</div><ul class="papers">${sortPapers(ps).map(p => paperRow(d, p)).join('')}</ul>`).join('');
  return `<div class="card"><h2>论文 <span class="meta">${all.length} 篇 · 读过 ${read}${must ? ` · 必读未读 ${must}` : ''}</span></h2>
    <div class="pad"><div class="chips">${chip('全部', '', all.length)}${chip('未读', 'unread', all.length - read)}${chip('读过', 'read', read)}</div></div>
    ${groups || '<div class="empty">没有符合条件的论文。</div>'}
    <div class="pad small">清单由会话在讨论方案时写进 board/papers.jsonl，每篇写为什么读、属于哪一步；读完了在这里打勾，会话下次开场就知道你读过哪些。
      「未核实」表示会话还没打开链接核对过标题。别的项目里勾过的同一篇会注明，但这里不自动算读过。</div></div>`;
}
const sizeText = n => typeof n !== 'number' ? '' : n < 1024 * 1024 ? Math.max(1, Math.round(n / 1024)) + ' KB' : (n / 1024 / 1024).toFixed(1) + ' MB';
// A figure is a page an agent wrote: it runs in a frame with scripts but without an origin (sandbox="allow-scripts",
// never allow-same-origin), and the server adds a CSP that keeps it off the network. data-key lets setMain keep it.
function figureView(d, x) {
  const url = fileUrl(d.name, x.file);
  const frame = x.size > FIGURE_MAX
    ? `<div class="notice">文件有 ${sizeText(x.size)}，超过 10 MB，控制台不显示。请会话把数据抽样或少画几张。</div>`
    : `<iframe class="figframe" src="${esc(url)}" sandbox="allow-scripts" referrerpolicy="no-referrer" title="${esc(x.title)}" data-key="${esc(x.file + '@' + x.mtime)}"></iframe>`;
  return `${x.question ? `<p class="figq">回答：${esc(x.question)}</p>` : ''}
    <div class="figbar"><a href="${esc(url)}" target="_blank" rel="noopener noreferrer">在新窗口打开 ↗</a>
      <span class="small">会话画的网页，在隔离的框里运行：读不到控制台的内容，也不能联网</span></div>${frame}`;
}

// ============================================================ events
// someone is typing in the page: refreshes must not rebuild it under them
const busy = () => ui.renaming || !!(document.activeElement && document.activeElement.matches('#main textarea, #main input'));
async function sendReply(key) {
  const box = document.querySelector(`.reply[data-key="${CSS.escape(key)}"]`);
  if (!box) return;
  const text = (box.querySelector('textarea') || {}).value || '';
  const answer = ui.pick[key] !== undefined ? ui.pick[key] : (box.querySelector('.opt.on') || {dataset: {}}).dataset.opt || null;
  const btn = box.querySelector('.send');
  btn.disabled = true; btn.textContent = '提交中…';
  try {
    const direct = !!(box.querySelector('[data-direct]') || {}).checked;
    await postJSON('/api/answer', {project: box.dataset.p, id: box.dataset.id, answer, text, ...(direct ? {mode: '直接处理'} : {})});
    delete ui.pick[key]; delete ui.draft[key]; delete ui.editing[key]; delete ui.err[key]; delete ui.direct[key];
    await refreshAfterWrite(box.dataset.p);
  } catch (err) {
    ui.err[key] = err.message; btn.disabled = false; btn.textContent = '提交回复';
    box.querySelector('.rerr').textContent = err.message;
  }
}
async function refreshAfterWrite(name) {
  try { S.projects = await getJSON('/api/projects'); } catch (e) {}
  const r = route();
  if (r.page === 'project' && (r.name === name || name === '_shared')) { S.fetchedAt[r.name] = 0; ensureDetail(r.name, true); }
  render();
}
async function saveName(name, value) {
  try {
    await postJSON('/api/name', {project: name, name: value});
    ui.renaming = false; S.fetchedAt[name] = 0;
    await refreshAfterWrite(name);
  } catch (err) { const el = $('rename-err'); if (el) el.textContent = err.message; }
}
document.addEventListener('input', e => {
  const ta = e.target.closest('textarea[data-draft]');
  if (ta) ui.draft[ta.dataset.draft] = ta.value;
  const dc = e.target.closest('input[data-direct]');
  if (dc) ui.direct[dc.dataset.direct] = dc.checked;
});
document.addEventListener('keydown', e => {
  if (e.target.id === 'rename-input' && e.key === 'Enter') { e.preventDefault(); saveName(e.target.dataset.p, e.target.value); }
  if (e.target.id === 'rename-input' && e.key === 'Escape') { ui.renaming = false; render(); }
});
document.addEventListener('click', e => {
  const t = e.target;
  const opt = t.closest('.opt');
  if (opt) {
    const box = opt.closest('.reply'), key = box.dataset.key, on = !opt.classList.contains('on');
    box.querySelectorAll('.opt').forEach(x => x.classList.remove('on'));
    if (on) opt.classList.add('on');
    ui.pick[key] = on ? opt.dataset.opt : null;
    return;
  }
  const send = t.closest('[data-send]');
  if (send) { sendReply(send.dataset.send); return; }
  const ed = t.closest('[data-edit]'), cancel = t.closest('[data-cancel]');
  if (ed || cancel) {
    e.preventDefault();
    const key = (ed || cancel).dataset[ed ? 'edit' : 'cancel'];
    if (ed) ui.editing[key] = true; else { delete ui.editing[key]; delete ui.pick[key]; delete ui.draft[key]; delete ui.err[key]; }
    render(); return;
  }
  const mk = t.closest('[data-mark]');
  if (mk) {
    const r = route();
    mk.disabled = true;
    postJSON('/api/mark', {project: r.name, file: mk.dataset.file, mark: mk.dataset.mark || null})
      .then(() => { S.fetchedAt[r.name] = 0; return refreshAfterWrite(r.name); })
      .catch(err => { mk.disabled = false; alert('标记没有保存：' + err.message); });
    return;
  }
  if (t.closest('#rename-open')) { ui.renaming = true; render(); const i = $('rename-input'); if (i) { i.focus(); i.select(); } return; }
  if (t.closest('#rename-save')) { const i = $('rename-input'); saveName(i.dataset.p, i.value); return; }
  if (t.closest('#rename-reset')) { const i = $('rename-input'); saveName(i.dataset.p, ''); return; }
  if (t.closest('#rename-cancel')) { ui.renaming = false; render(); return; }
  const go = t.closest('[data-goto]');
  if (go) { e.preventDefault(); const el = document.getElementById(go.dataset.goto); if (el) el.scrollIntoView({block: 'start', behavior: 'smooth'}); return; }
  if (t.closest('#gpu-refresh')) { fetch('/api/refresh').then(() => setTimeout(poll, 4000)); return; }
  const copy = t.closest('.copy');
  if (copy) { navigator.clipboard.writeText(copy.dataset.c).then(() => { const x = copy.textContent; copy.textContent = '已复制'; setTimeout(() => copy.textContent = x, 1200); }); return; }
  if (t.closest('[data-example]')) { ui.exampleOpen = !ui.exampleOpen; render(); return; }
  const ag = t.closest('[data-agroup]');
  if (ag) { e.preventDefault(); ui.attGroup[ag.dataset.att] = ag.dataset.agroup; render(); return; }
  if (t.closest('[data-runallcols]')) { e.preventDefault(); ui.runAllCols = !ui.runAllCols; render(); return; }
  const row = t.closest('tr[data-href]');
  if (row && !t.closest('a')) location.hash = row.dataset.href;
});
document.addEventListener('change', e => {
  const pc = e.target.closest('input[data-paper]');
  if (pc) {
    const r = route(), want = pc.checked;
    pc.disabled = true; pc.blur();  // a focused input would hold off the refresh (busy)
    postJSON('/api/paper-mark', {project: r.name, paper: pc.dataset.paper, mark: want ? '读过' : null})
      .then(() => { S.fetchedAt[r.name] = 0; return refreshAfterWrite(r.name); })
      .catch(err => { pc.checked = !want; pc.disabled = false; alert('没有保存：' + err.message); });
    return;
  }
  const rq = e.target.closest('select[data-rq]');   // a choice on the runs page goes into its address
  if (rq) {
    const r = route(), Q = runsQuery(r), key = rq.dataset.rq, d = S.detail[r.name];
    if (!d) return;
    const change = {[key]: rq.value || (key === 'g' || key === 'rows' || key === 'metric' ? null : '')};
    if (key === 'cols') change.cols = [...rq.closest('.pvsel').querySelectorAll('select[data-rq="cols"]')].map(x => x.value).filter(Boolean);
    if (key === 'g') Object.assign(change, {sort: null, desc: null, metric: null});
    location.hash = runsLink(d, Q, change, r.item);
  }
});
document.addEventListener('mousedown', e => { copying = !!e.target.closest('.copy'); });
window.addEventListener('hashchange', () => { ui.renaming = false; render(); });

// ============================================================ polling
async function poll() {
  const [st, pj] = await Promise.allSettled([getJSON('/api/status'), getJSON('/api/projects')]);
  const firstProjects = !S.projects && pj.status === 'fulfilled';
  S.offline = st.status === 'rejected' && pj.status === 'rejected';
  if (st.status === 'fulfilled') S.status = st.value;
  if (pj.status === 'fulfilled') S.projects = pj.value;
  const r = route();
  if (r.page === 'sessions' || r.page === 'project' || !S.sessions) ensureSessions();
  // don't disturb a project page, except once when the project list first arrives (entry badge, counts)
  if (r.page === 'project' && !firstProjects) { renderSide(r); ensureDetail(r.name); document.title = titleFor(r); return; }
  if (r.page === 'gpu' && copying) { copying = false; renderSide(r); return; }
  if (busy()) { renderSide(r); return; }
  render();
}
render();
poll();
setInterval(poll, 5000);
