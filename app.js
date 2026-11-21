// The console: routing, sidebar and pages. Needs common.js (Board); no other dependencies.
// Routes (hash):  #/overview  #/gpu  #/todo  #/new  #/p/<project>/<tab>[/<item>][?filters]
//   project tabs: overview, setup, roadmap (versions > tracks > attempts), runs, examples, docs
const B = Board, esc = B.esc, $ = id => document.getElementById(id);

// ============================================================ data
const S = {status: null, projects: null, guide: null, detail: {}, fetchedAt: {}, loading: {}, missing: {}, offline: false};
const ui = {runView: {}, exampleOpen: false, attGroup: {}, runAllCols: false};  // view state kept across refreshes
const getJSON = url => fetch(url).then(r => r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status)));

// ============================================================ routing
const TABS = [['overview', '概览'], ['setup', '实验设置'], ['roadmap', '路线'], ['runs', '运行'], ['examples', '样例'], ['docs', '文档']];
const PAGES = ['overview', 'gpu', 'todo', 'guide', 'new'];

function route() {
  const raw = location.hash.replace(/^#/, '');
  const [path, q = ''] = raw.split('?');
  const parts = path.split('/').filter(Boolean).map(s => { try { return decodeURIComponent(s); } catch (e) { return s; } });
  const query = Object.fromEntries(new URLSearchParams(q));
  if (parts[0] === 'p' && parts[1]) {
    let tab = parts[2], item = parts[3] ?? null;
    if (tab === 'experiments') {  // the experiments tab was merged into the roadmap
      tab = 'roadmap';
      if (item != null) item = 'a:' + item;
      else if (query.track) { item = 't:' + query.track; delete query.track; }
    }
    return {page: 'project', name: parts[1], tab: TABS.some(t => t[0] === tab) ? tab : 'overview', item, query};
  }
  return {page: PAGES.includes(parts[0]) ? parts[0] : 'overview', query};
}
const plink = (name, tab = 'overview', item = null, query = null) => {
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
  if (version) out.push(`<a href="${plink(n, 'roadmap', 'v:' + idOf(version))}">版本 ${esc(idOf(version))}</a>`);
  if (track) out.push(`<a href="${plink(n, 'roadmap', 't:' + idOf(track))}">${esc(track.meta.title || idOf(track))}</a>`);
  if (attempt) out.push(`<a href="${plink(n, 'roadmap', 'a:' + attKey(attempt))}">${esc(attName(attempt))}</a>`);
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
  return pj.projects.reduce((n, p) => n + (p.pending || []).length + (p.todos || []).length, 0) + openTodos(pj.todos).length;
}
function renderSide(r) {
  const pj = S.projects ? S.projects.projects : null;
  const free = S.status ? B.freeCount(S.status) : null, todo = todoCount();
  const item = (href, label, cnt, on) => `<a class="sitem${on ? ' on' : ''}" href="${href}"><span class="lbl">${label}</span>` +
    (cnt != null ? `<span class="cnt">${cnt}</span>` : '') + '</a>';
  const order = {'进行中': 0, '暂停': 1, '已归档': 2};
  const list = pj ? [...pj].sort((a, b) => (order[a.status] ?? 1) - (order[b.status] ?? 1) || String(a.title).localeCompare(String(b.title))) : [];
  $('side').innerHTML = `<a class="brand" href="#/overview">${esc(B.APP_NAME)}</a>` +
    item('#/overview', '总览', null, r.page === 'overview') +
    item('#/gpu', 'GPU', free != null ? free + ' 空闲' : null, r.page === 'gpu') +
    item('#/todo', '待办', todo || null, r.page === 'todo') +
    item('#/guide', '使用说明', null, r.page === 'guide') +
    '<div class="shead">项目</div>' +
    (list.length ? list.map(p => `<a class="sitem${r.page === 'project' && r.name === p.name ? ' on' : ''}" href="${plink(p.name)}" ` +
      `title="${esc(p.title)}${p.status ? ' · ' + esc(p.status) : ''}"><span class="dot d-${B.tone(p.status)}"></span><span class="lbl">${esc(p.title)}</span></a>`).join('')
      : `<div class="faint" style="padding:4px 10px">${pj ? '还没有项目' : '加载中…'}</div>`) +
    `<a class="sitem new${r.page === 'new' ? ' on' : ''}" href="#/new"><span class="lbl">＋ 新建项目</span></a>`;
}

// ============================================================ main
let lastHash = null, copying = false;
function render() {
  const r = route();
  renderSide(r);
  const open = [...document.querySelectorAll('#main details[open][id]')].map(x => x.id);  // keep what the user expanded
  let html;
  if (r.page === 'gpu') html = pageGpu();
  else if (r.page === 'todo') html = pageTodo();
  else if (r.page === 'new') html = pageNew();
  else if (r.page === 'guide') html = pageGuide();
  else if (r.page === 'project') html = pageProject(r);
  else html = pageOverview();
  $('main').innerHTML = html;
  for (const id of open) { const el = document.getElementById(id); if (el) el.open = true; }
  fitExample();
  // a linked setup entry is highlighted on every render and scrolled to once; other pages start at the top
  const anchor = r.page === 'project' && r.tab === 'setup' && r.item != null && document.querySelector(`[data-anchor="${CSS.escape(r.item)}"]`);
  if (anchor) anchor.classList.add('flash');
  if (location.hash !== lastHash && !(r.page === 'project' && !S.detail[r.name] && !S.missing[r.name])) {  // wait for the data before scrolling
    lastHash = location.hash;
    if (anchor) anchor.scrollIntoView({block: 'center'}); else window.scrollTo(0, 0);
  }
  document.title = titleFor(r);
}
function titleFor(r) {
  const app = B.APP_NAME;
  if (r.page === 'gpu') return (S.status ? `(${B.freeCount(S.status)} 空) ` : '') + app;
  if (r.page === 'todo') return `待办 · ${app}`;
  if (r.page === 'new') return `新建项目 · ${app}`;
  if (r.page === 'guide') return `使用说明 · ${app}`;
  if (r.page === 'project') { const d = S.detail[r.name]; return `${(d && d.project && d.project.title) || r.name} · ${app}`; }
  return `总览 · ${app}`;
}

// ============================================================ global overview
function pageOverview() {
  const pj = S.projects;
  if (!pj) return '<div class="empty">加载中…</div>';
  const now = pj.now, order = {'进行中': 0, '暂停': 1, '已归档': 2};
  const list = [...pj.projects].sort((a, b) => (order[a.status] ?? 1) - (order[b.status] ?? 1) || (b.mtime || 0) - (a.mtime || 0));
  const free = S.status ? B.freeCount(S.status) : null;
  const running = list.reduce((n, p) => n + (p.active_runs || []).length, 0);
  const sc = pj.scan || {}, notes = [];
  if (sc.running_since && now - sc.running_since > 5) notes.push(`正在扫描共享盘，已 ${B.span(now - sc.running_since)}；下面是上一次的结果`);
  if (sc.error) notes.push(`最近一次扫描失败（${esc(sc.error)}），下面是 ${B.ago(sc.finished, now)}的结果`);
  const cards = list.map(p => {
    const runningTracks = (p.track_status || {})['进行中'] || 0, as = Object.entries(p.attempt_status || {});
    const waiting = (p.pending || []).length + (p.todos || []).length;
    return `<a class="pcard" href="${plink(p.name)}">
      <div class="t"><span class="dot d-${B.tone(p.status)}"></span><b>${esc(p.title)}</b>${B.pill(p.status)}
        ${p.current_version ? `<span class="small">当前版本 ${esc(p.current_version)}</span>` : ''}</div>
      <div class="c">${p.conclusion ? esc(p.conclusion) : '<span class="faint">还没有结论</span>'}</div>
      <div class="n"><span>支线 <b>${p.n_tracks ?? 0}</b>${runningTracks ? `（${runningTracks} 条进行中）` : ''}</span>
        <span>尝试 <b>${p.n_attempts ?? 0}</b></span><span>运行 <b>${p.n_runs ?? 0}</b></span></div>
      ${as.length ? `<div class="small">${countLine(as.sort((a, b) => b[1] - a[1]))}</div>` : ''}
      ${(p.active_runs || []).map(r => `<div class="small">正在跑 ${esc(r.id)} · ${esc(r.server || '?')}${esc(gpusText(r))} ${B.livePill(r.liveness)}</div>`).join('')}
      ${waiting ? `<div class="w">${waiting} 条等你处理</div>` : ''}
      ${p.n_checks ? `<div class="w">${p.n_checks} 条记录检查待修</div>` : ''}
      ${p.stale ? `<div class="w">数据是 ${B.ago(p.scanned_at, now)}的，最近一次读取失败</div>` : ''}
      ${p.n_errors ? `<div class="w">${p.n_errors} 个记录文件读取有问题</div>` : ''}
      <div class="small">最近动态 ${B.ago(p.mtime, now)} · 常驻入口${hasEntry(p.name) ? '在运行' : '未运行'}</div></a>`;
  }).join('');
  return `<h1 class="pt">总览</h1>
    <div class="summary">
      <a class="stat" href="#/gpu"><div class="k">空闲的卡</div><div class="v">${free ?? '—'}</div><div class="s">所有服务器合计</div></a>
      <a class="stat" href="#/todo"><div class="k">等你处理</div><div class="v">${todoCount() ?? '—'}</div><div class="s">所有项目合计</div></a>
      <div class="stat"><div class="k">正在跑的任务</div><div class="v">${running}</div><div class="s">按项目记录</div></div>
      <div class="stat"><div class="k">项目</div><div class="v">${list.length}</div><div class="s">${sc.finished ? '扫描于 ' + B.ago(sc.finished, now) : ''}</div></div>
    </div>` + notes.map(t => `<div class="notice">${t}</div>`).join('') +
    (list.length ? `<div class="pcards">${cards}</div>`
      : `<div class="card"><div class="empty">还没有项目。在 ${esc(sc.root || '共享盘')}/&lt;项目名&gt;/board/ 下写好 project.json，一分钟内就会出现在这里。</div></div>`);
}

// ============================================================ todo
function todoItem(t, project) {
  const href = project ? linkFor(project.name, t.link) : null;
  return `<li><div>${t.kind && KIND[t.kind] ? `<span class="pill t-${t.kind === 'whenever' ? 'other' : 'todo'}">${KIND[t.kind]}</span> ` : ''}` +
    (href ? `<a href="${href}">${esc(t.text)}</a>` : esc(t.text)) + '</div>' +
    `<div class="small">${project ? esc(project.title) + ' · ' : '跨项目 · '}${t.where ? '在哪做：' + esc(t.where) + ' · ' : ''}` +
    `${t.created ? B.ago(Date.parse(t.created) / 1000, S.projects.now) : ''}${t.by ? ' · 由 ' + esc(t.by) + ' 记下' : ''}</div></li>`;
}
function pageTodo() {
  const pj = S.projects;
  if (!pj) return '<div class="empty">加载中…</div>';
  const urgent = [], later = [];
  for (const p of pj.projects) for (const t of p.todos || []) (t.kind === 'whenever' ? later : urgent).push(todoItem(t, p));
  for (const t of openTodos(pj.todos)) (t.kind === 'whenever' ? later : urgent).push(todoItem(t, null));
  for (const p of pj.projects.filter(p => (p.pending || []).length)) {
    const n = p.pending.length, confirm = p.pending.filter(x => x.needs.includes('结论待确认')).length;
    const parts = [confirm ? `${confirm} 条尝试等你确认结论` : '', n - confirm ? `${n - confirm} 条尝试缺结论和原因` : ''].filter(Boolean);
    later.push(`<li><a href="${plink(p.name, 'roadmap', null, {todo: 1})}"><b>${esc(p.title)}</b>：${parts.join('，')}</a>
      <div class="small">在路线页逐条查看</div></li>`);
  }
  const col = (title, meta, items) => `<div class="card"><h2>${title} <span class="meta">${meta}</span></h2>` +
    (items.length ? `<ul class="tlist">${items.join('')}</ul>` : '<div class="empty">没有。</div>') + '</div>';
  return `<h1 class="pt">待办</h1><div class="cols2">${col('卡着进度的', '需要你先做或先决定，事情才能往下走', urgent)}${col('不急的', '有空时处理', later)}</div>
    <p class="small">待办由各会话写在项目的 board/todos.jsonl 和 /shared/ssd/dchen/_shared/todos.jsonl 里；办完后由会话标记完成。</p>`;
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
const SHARE_MB = 10 * 1024;  // a busy card with at least this much free memory is listed as shareable
const GPU_LABEL = {free: '空闲', busy: '使用中', full: '已占满'};
const gpuState = g => B.isFree(g) ? 'free' : g.mem_total && g.mem_used / g.mem_total > 0.9 ? 'full' : 'busy';
const errText = e => /退出码 3/.test(e || '') ? 'nvidia-smi 无法运行（显卡驱动没有加载），需要管理员处理' : e;
const gb = mb => mb == null ? '–' : (mb / 1024).toFixed(1);
const dur = s => s == null ? '' : s < 3600 ? Math.round(s / 60) + ' 分钟' : s < 86400 ? (s / 3600).toFixed(1) + ' 小时' : (s / 86400).toFixed(1) + ' 天';
function activeRuns() {
  return S.projects ? S.projects.projects.flatMap(p => (p.active_runs || []).map(r => ({...r, project: p.name, title: p.title}))) : [];
}
function pageGpu() {
  const d = S.status;
  if (!d) return S.offline ? '<div class="empty">连不上控制台：请确认 Mac 能连到 xtraa100（校园网 / VPN）</div>' : '<div class="empty">加载中…</div>';
  const runs = activeRuns();
  let total = 0, okServers = 0, html = '';
  const users = {}, freeBy = [], partial = [];
  for (const s of d.servers) {
    const gpus = s.gpus || [], live = s.status === 'ok', freeCards = [];
    if (live) okServers++;
    let rows = '';
    for (const g of gpus) {
      const st = gpuState(g);
      if (live) { total++; if (st === 'free') freeCards.push(g); else if (g.mem_total - g.mem_used >= SHARE_MB) partial.push({s, g}); }
      const pct = g.mem_total ? Math.round(100 * g.mem_used / g.mem_total) : 0;
      const procs = g.procs.map(p => {
        const u = users[p.user] = users[p.user] || {mem: 0, gpus: new Set(), where: new Set()};
        u.mem += p.mem || 0; u.gpus.add(s.name + '#' + g.index); u.where.add(s.name);
        return `<div class="proc"><span class="user">${esc(p.user)}</span>
          <span class="small">${gb(p.mem)} GB · ${dur(p.elapsed)} · PID ${esc(p.pid)}</span>
          <span class="cmd" title="${esc(p.cmd)}">${esc(p.cmd)}</span></div>`;
      }).join('') || (st === 'free' ? '<span class="small">—</span>' : '<span class="small">有显存占用但看不到进程（可能在容器内）</span>');
      const tags = B.runsOnGpu(runs, s.name, g).map(r => {
        const lost = r.liveness === 'maybe_interrupted';
        return `<a class="tag${lost ? ' warn' : ''}" href="${plink(r.project, 'runs', r.id)}" title="tmux ${esc(r.tmux || '?')} · PID ${esc(r.pid ?? '?')}">` +
          `${esc(r.title)} · ${esc(r.id)}${lost ? '（记录说在这里跑，但没找到进程）' : ''}</a>`;
      }).join('');
      rows += `<tr><td class="n">${esc(g.index)}</td><td><span class="pill ${st}">${GPU_LABEL[st]}</span></td><td class="n">${esc(g.name)}</td>
        <td class="n">${gb(g.mem_used)} / ${gb(g.mem_total)} GB<div class="bar"><i style="width:${pct}%;background:var(--${st})"></i></div></td>
        <td class="n">${g.util ?? '–'}%</td><td class="n">${g.temp ?? '–'}°C</td><td>${tags ? `<div>${tags}</div>` : ''}${procs}</td></tr>`;
    }
    if (freeCards.length) freeBy.push({s, cards: freeCards});
    const color = live ? 'var(--free)' : s.status === 'pending' ? 'var(--busy)' : 'var(--full)';
    html += `<section class="card"><h2><span class="dotc" style="background:${color}"></span>${esc(s.name)}<span class="meta">${esc(s.host)}</span>
      <span class="meta right">${live ? `空闲 ${freeCards.length} / ${gpus.length}` : ''}${s.updated ? ' · 更新于 ' + B.ago(s.updated, d.now) : ''}</span></h2>`;
    if (s.status === 'pending') html += '<div class="empty">正在连接…</div>';
    if (s.status === 'error') {
      html += `<div class="err">读取失败：<code>${esc(errText(s.error))}</code>${gpus.length ? '（下面是上一次成功时的数据）' : ''}</div>`;
      if (/Permission denied/.test(s.error || '')) html += `<div class="hint">这台机器还没有配置免密登录：<code>ssh-copy-id ${esc(s.host)}</code></div>`;
    }
    if (gpus.length) html += `<div class="scroll" style="${live ? '' : 'opacity:.55'}"><table class="gt">
      <tr><th>卡</th><th>状态</th><th>型号</th><th>显存</th><th>利用率</th><th>温度</th><th>使用者 / 进程</th></tr>${rows}</table></div>`;
    html += '</section>';
  }
  const nFree = freeBy.reduce((n, x) => n + x.cards.length, 0);
  freeBy.sort((a, b) => b.cards.length - a.cards.length || b.cards[0].mem_total - a.cards[0].mem_total);
  const freeHtml = `<section class="card lead"><h2>现在能跑的卡 <span class="big">${nFree}</span><span class="meta">张空闲 / 共 ${total} 张</span></h2>` +
    (nFree ? freeBy.map(({s, cards}) => {
      const ids = cards.map(g => g.index).join(',');
      const models = [...new Set(cards.map(g => `${g.name} · ${Math.round(g.mem_total / 1024)} GB`))].join(' / ');
      return `<div class="slot"><div class="where"><b>${esc(s.name)}</b><span class="small">${esc(models)}</span></div>
        <div class="gchips"><span class="small">卡号</span>${cards.map(g => `<span class="gchip">${esc(g.index)}</span>`).join('')}</div>
        <div class="cmds"><code>CUDA_VISIBLE_DEVICES=${esc(ids)}</code>
          <button class="copy" data-c="CUDA_VISIBLE_DEVICES=${esc(ids)}">复制</button>
          <button class="copy" data-c="ssh ${esc(s.host)}">复制 ssh 命令</button></div></div>`;
    }).join('') : `<div class="empty">现在没有完全空闲的卡。${partial.length ? '可以看看下面还有余量的卡。' : ''}</div>`) + '</section>';
  partial.sort((a, b) => (b.g.mem_total - b.g.mem_used) - (a.g.mem_total - a.g.mem_used));
  const partialHtml = partial.length ? `<section class="card"><h2>挤一挤能用的卡 <span class="meta">已有人在用，但剩余显存不少于 ${SHARE_MB / 1024} GB；共用会互相拖慢</span></h2>
    <div class="scroll"><table class="gt"><tr><th>服务器</th><th>卡</th><th>剩余显存</th><th>当前利用率</th><th>已在用的人</th></tr>` +
    partial.map(({s, g}) => `<tr><td><b>${esc(s.name)}</b></td><td class="n">${esc(g.index)}</td>
      <td class="n">${gb(g.mem_total - g.mem_used)} GB <span class="small">/ ${gb(g.mem_total)}</span></td><td class="n">${g.util ?? '–'}%</td>
      <td>${[...new Set(g.procs.map(p => p.user))].map(u => `<span class="user">${esc(u)}</span>`).join('、') || '<span class="small">看不到进程</span>'}</td></tr>`).join('') +
    '</table></div></section>' : '';
  const list = Object.entries(users).sort((a, b) => b[1].mem - a[1].mem);
  const usersHtml = list.length ? `<table class="gt"><tr><th>用户</th><th>占用卡数</th><th>显存合计</th><th>所在服务器</th></tr>` +
    list.map(([u, v]) => `<tr><td><span class="user">${esc(u)}</span></td><td class="n">${v.gpus.size}</td><td class="n">${gb(v.mem)} GB</td>
      <td>${esc([...v.where].join('、'))}</td></tr>`).join('') + '</table>' : '<div class="empty">当前没有人在用卡。</div>';
  const down = d.servers.length - okServers;
  return `<div class="gtop"><h1>GPU</h1><span class="sub">${okServers} / ${d.servers.length} 台服务器在线${down ? '（' + down + ' 台读不到）' : ''} · 每 ${d.interval} 秒更新</span>
      <button id="gpu-refresh">立即刷新</button></div>` + freeHtml + partialHtml +
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
    changed = !old || old.scanned_at !== d.scanned_at || JSON.stringify(old.stale) !== JSON.stringify(d.stale) || JSON.stringify(old.scan) !== JSON.stringify(d.scan);
    S.detail[name] = d; S.missing[name] = false;
  }).catch(() => {}).finally(() => {
    S.loading[name] = false; S.fetchedAt[name] = Date.now();
    const r = route();
    if (changed && r.page === 'project' && r.name === name) render();
  });
}

function pageProject(r) {
  ensureDetail(r.name);
  const d = S.detail[r.name];
  if (!d) return S.missing[r.name]
    ? `<h1 class="pt">${esc(r.name)}</h1><div class="card"><div class="empty">没有这个项目（或者它的 board/project.json 还没写好）。</div></div>`
    : `<div class="empty">正在读取 ${esc(r.name)}…</div>`;
  const p = d.project || {};
  const counts = {setup: null, roadmap: d.attempts.length, runs: d.runs.length, examples: (d.example.probe_head || []).length || null, docs: docItems(d).length};
  const head = `<div class="phead"><h1>${esc(p.title || d.name)}</h1>${B.pill(p.status)}
      ${p.current_version ? `<span class="small">当前版本 <b>${esc(p.current_version)}</b></span>` : ''}
      ${entryBadge(d.name)}
      <span class="small right">数据读取于 ${B.ago(d.scanned_at, d.now)} · 每分钟刷新</span></div>
    <nav class="ptabs">${TABS.map(([k, label]) => `<a class="ptab${k === r.tab ? ' on' : ''}" href="${plink(d.name, k)}">${label}` +
      (counts[k] ? `<span class="cnt">${counts[k]}</span>` : '') + '</a>').join('')}</nav>`;
  const body = {overview: tabOverview, setup: setupCard, roadmap: tabRoadmap, runs: tabRuns, examples: tabExamples, docs: tabDocs}[r.tab](d, r);
  return head + notices(d) + body;
}

function notices(d) {
  const out = [], sc = d.scan || {}, now = d.now;
  if (d.stale) out.push(d.scanned_at ? `这个项目的数据是 ${B.ago(d.scanned_at, now)}的，最近一次读取失败（${esc(d.stale.error)}）。`
    : `这个项目还没有成功读取过（${esc(d.stale.error)}）。`);
  if (sc.running_since && now - sc.running_since > 5) out.push(`正在扫描共享盘，已 ${B.span(now - sc.running_since)}；下面是上一次的结果。`);
  if (sc.error) out.push(`最近一次扫描失败（${esc(sc.error)}），下面是 ${B.ago(sc.finished, now)}的结果。`);
  return out.map(t => `<div class="notice">${t}</div>`).join('');
}

// ------------------------------------------------------------ 概览: conclusion, numbers, setup, what needs you, activity, checks
function tabOverview(d) {
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
  const todos = openTodos(d.todos);
  const waitItems = [
    ...todos.map(t => { const href = linkFor(name, t.link);
      return `<li><span class="when"><span class="pill t-${t.kind === 'whenever' ? 'other' : 'todo'}">${KIND[t.kind] || '待办'}</span></span>
        <span>${href ? `<a href="${href}">${esc(t.text)}</a>` : esc(t.text)}${t.where ? `<div class="small">在哪做：${esc(t.where)}</div>` : ''}</span></li>`; }),
    ...pending.map(a => `<li><span class="when">${esc(attDate(a))}</span>
      <span><a href="${plink(name, 'roadmap', 'a:' + attKey(a))}">${esc(attName(a))}</a> <span class="todo">${needsLabel(pendingOf(a))}</span></span></li>`)];
  const ev = B.foldEvents(B.events(d)).slice(0, 14);
  const evHref = e => e.tab === 'experiments' ? plink(name, 'roadmap', e.item != null ? 'a:' + e.item : null)
    : e.tab === 'roadmap' && e.item != null ? plink(name, 'roadmap', (e.text.startsWith('定版') ? 'v:' : 't:') + e.item) : plink(name, e.tab, e.item);
  return `${p.conclusion ? `<p class="concl">${esc(p.conclusion)}</p>` : '<p class="concl faint">还没有结论</p>'}
    ${p.question ? `<p class="question">研究问题：${esc(p.question)}</p>` : ''}
    <div class="stats">
      <a class="stat" href="${plink(name, 'roadmap')}"><div class="k">支线</div><div class="v">${d.tracks.length}</div>
        <div class="s">${running ? `<b>${running}</b> 条进行中 · ` : ''}${countLine(tracks.filter(([s]) => s !== '进行中')) || '还没有支线'}</div></a>
      <a class="stat" href="${plink(name, 'roadmap')}"><div class="k">尝试</div><div class="v">${d.attempts.length}</div>
        <div class="s">${countLine(atts) || '还没有尝试'}</div></a>
      ${bestCard}
      <a class="stat" href="${plink(name, 'runs')}"><div class="k">正在跑</div><div class="v">${active.length}</div>
        <div class="s">${active.length ? active.map(x => `${esc(c.label(x))} · ${esc((x.run || {}).server || '?')}${esc(gpusText(x.run || {}))} ${B.livePill(x.liveness)}`).join('<br>') : '没有正在跑的任务'}</div></a>
    </div>
    ${setupSummary(d)}
    <div class="cols2">
      <div class="card"><h2>等你处理 <span class="meta">${waitItems.length} 条</span></h2>
        ${waitItems.length ? `<ul class="feed">${waitItems.slice(0, 14).join('')}</ul>` +
          (waitItems.length > 14 ? `<div class="pad small"><a href="#/todo">全部 ${waitItems.length} 条 →</a></div>` : '') : '<div class="empty">没有等你处理的事。</div>'}</div>
      <div class="card"><h2>最近动态</h2>
        ${ev.length ? `<ul class="feed">${ev.map(e => `<li><span class="when">${B.ago(e.t, d.now)}</span><a href="${evHref(e)}">${esc(e.text)}</a></li>`).join('')}</ul>`
          : '<div class="empty">还没有记录。</div>'}</div>
    </div>
    ${checksCard(d)}`;
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
function setupCard(d) {  // the 实验设置 tab
  const c = ctx(d), s = c.setup, name = d.name;
  if (!s.present) return `<div class="card"><h2>实验设置</h2><div class="empty">还没有 board/setup.json。写上数据集、模型、指标和方法的定义后，这里会像论文的「实验设置」一节那样列出来，运行页的分组和指标也会链接到这里。</div></div>`;
  const row = (anchor, cells) => `<tr data-anchor="${esc(anchor)}">${cells.map(x => `<td>${x}</td>`).join('')}</tr>`;
  const table = (title, n, heads, rows) => n ? `<div class="setblk"><h3>${title} <span class="faint">${n}</span></h3>
    <div class="scroll"><table class="mt setup"><tr>${heads.map(h => `<th>${h}</th>`).join('')}</tr>${rows.join('')}</table></div></div>` : '';
  const ds = [...s.datasets.values()], mt = [...s.metrics.values()];
  const runsOfMethod = m => (Array.isArray(m.runs) ? m.runs : []).map(String);
  return `<div class="card"><h2>实验设置 <span class="meta">board/setup.json</span></h2><div class="pad">
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
      `<b>${esc(x.name || x.id)}</b>`, x.track && c.trackById.get(x.track) ? `<a href="${plink(name, 'roadmap', 't:' + x.track)}">${esc(c.trackById.get(x.track).meta.title || x.track)}</a>` : '',
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

// ------------------------------------------------------------ 路线: versions > tracks > attempts on the left, the selected node on the right
function tabRoadmap(d, r) {
  const c = ctx(d), name = d.name, q = r.query || {}, tree = c.tree;
  const onlyTodo = !!q.todo, keep = a => !onlyTodo || pendingOf(a).length;
  let sel = r.item;
  if (sel == null) {  // default: the current version, else the first node
    const cur = (d.project || {}).current_version;
    sel = cur && c.verById.has(cur) ? 'v:' + cur : tree.versions[0] ? 'v:' + idOf(tree.versions[0].version)
      : tree.loose[0] ? 't:' + idOf(tree.loose[0].track) : d.attempts[0] ? 'a:' + attKey(d.attempts[0]) : null;
  }
  const qs = onlyTodo ? {todo: 1} : null;
  const aRow = a => keep(a) ? `<a class="tn ta${sel === 'a:' + attKey(a) ? ' on' : ''}" href="${plink(name, 'roadmap', 'a:' + attKey(a), qs)}">
      ${B.pill(a.meta.status) || '<span class="pill t-other">无状态</span>'}<span class="nm">${esc(attName(a))}</span><span class="small">${esc(attDate(a).slice(5))}</span>
      ${pendingOf(a).length ? '<span class="tflag">待你</span>' : ''}</a>` : '';
  const tRow = n => {
    const t = n.track, atts = n.attempts.map(aRow).join('');
    if (onlyTodo && !atts) return '';
    return `<a class="tn tt${sel === 't:' + idOf(t) ? ' on' : ''}" href="${plink(name, 'roadmap', 't:' + idOf(t), qs)}"><span class="dot d-${B.tone(t.meta.status)}"></span>
        <span class="nm">${esc(t.meta.title || idOf(t))}</span><span class="cnt">${n.attempts.length}</span></a>${atts}`;
  };
  const cur = (d.project || {}).current_version;
  let items = tree.versions.map(v => {
    const inner = v.tracks.map(tRow).join('');
    if (onlyTodo && !inner) return '';
    return `<a class="tn tv${sel === 'v:' + idOf(v.version) ? ' on' : ''}" href="${plink(name, 'roadmap', 'v:' + idOf(v.version), qs)}">
      <span class="vmark${idOf(v.version) === cur ? ' cur' : ''}"></span><b>${esc(idOf(v.version))}</b><span class="nm">${esc(v.version.meta.title || '')}</span></a>${inner}`;
  }).join('');
  const loose = tree.loose.map(tRow).join(''), orphans = tree.orphans.map(aRow).join('');
  if (loose) items += `<div class="tgroup">没有挂到版本上的支线</div>${loose}`;
  if (orphans) items += `<div class="tgroup">没有归属支线的尝试</div>${orphans}`;
  const nTodo = d.attempts.filter(a => pendingOf(a).length).length;
  const filters = `<div class="filters"><div class="chips">
      <a class="chip${!onlyTodo ? ' on' : ''}" href="${plink(name, 'roadmap', r.item)}">全部 ${d.attempts.length} 次尝试</a>
      ${nTodo ? `<a class="chip${onlyTodo ? ' on' : ''}" href="${plink(name, 'roadmap', r.item, {todo: 1})}">等你处理 ${nTodo}</a>` : ''}</div>
      <span class="small">按时间先后排列</span></div>`;
  let detail = '<div class="detail empty">还没有版本、支线或尝试的记录。</div>';
  if (sel && sel.startsWith('v:') && c.verById.get(sel.slice(2))) detail = versionDetail(d, c.verById.get(sel.slice(2)));
  else if (sel && sel.startsWith('t:') && c.trackById.get(sel.slice(2))) detail = trackDetail(d, c.trackById.get(sel.slice(2)));
  else if (sel && sel.startsWith('a:') && c.attById.get(sel.slice(2))) detail = attemptDetail(d, c.attById.get(sel.slice(2)));
  else if (sel) detail = `<div class="detail empty">找不到「${esc(sel)}」，可能已经改名或删除。</div>`;
  return `<div class="md2"><div class="list">${filters}<div class="items tree2">${items || '<div class="empty">没有符合条件的记录。</div>'}</div></div><div>${detail}</div></div>`;
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
  const chip = n => `<a class="chip" href="${plink(name, 'roadmap', 't:' + idOf(n.track))}">${esc(n.track.meta.title || idOf(n.track))} · ${esc(statusOf(n.track))} · ${n.attempts.length} 次尝试</a>`;
  const runs = [...new Set((node ? node.tracks : []).flatMap(n => n.attempts.flatMap(a => c.runsOf(a).filter(r => !r.missing))))];
  const cur = (d.project || {}).current_version === vid;
  return `<div class="detail"><h2>版本 ${esc(vid)}${v.meta.title ? ' · ' + esc(v.meta.title) : ''}${cur ? ' <span class="pill t-run">当前版本</span>' : ''}</h2>
    <div class="dmeta"><span>${esc(B.day(v.meta.date)) || '没有日期'}</span>${v.meta.parent ? `<span>来自 <a href="${plink(name, 'roadmap', 'v:' + v.meta.parent)}">${esc(v.meta.parent)}</a></span>` : ''}<span>${esc(v.file)}</span></div>
    ${merged.length ? `<div class="sec first"><h3>并入这个版本的支线</h3><div class="chips">${merged.map(chip).join('')}</div></div>` : ''}
    ${branched.length ? `<div class="sec${merged.length ? '' : ' first'}"><h3>从这个版本分出的支线</h3><div class="chips">${branched.map(chip).join('')}</div></div>` : ''}
    <div class="sec"><h3>这个版本的方案</h3>${v.body.trim() ? `<div class="md">${B.md(v.body)}</div>` : '<div class="none">版本正文是空的</div>'}</div>
    ${runsMatrix(d, runs, '这个版本下各次运行')}
    ${headlineBars(d, runs, '这个版本下各次运行')}</div>`;
}

function trackDetail(d, t) {
  const c = ctx(d), name = d.name, tid = idOf(t), node = [...c.tree.versions.flatMap(v => v.tracks), ...c.tree.loose].find(n => idOf(n.track) === tid);
  const atts = node ? node.attempts : [];
  const ver = c.verById.get(c.homeOf.get(tid)), merged = node && node.merged;
  const placement = [
    t.meta.from_version ? `从 <a href="${plink(name, 'roadmap', 'v:' + t.meta.from_version)}">${esc(t.meta.from_version)}</a> 分出` : '',
    merged ? `并入 <a href="${plink(name, 'roadmap', 'v:' + merged)}">${esc(merged)}</a>` : ''].filter(Boolean).join('，');
  const steps = atts.map((a, i) => {
    const sec = a.sections || {}, plain = t => String(t || '').replace(/[*`]/g, '');
    const gist = B.firstSentence('## 结果\n' + plain(sec['结果']), '结果') || B.firstSentence('## 结论和原因\n' + plain(sec['结论和原因']), '结论和原因');
    return `<li><span class="stepn">${i + 1}</span><div><a href="${plink(name, 'roadmap', 'a:' + attKey(a))}"><b>${esc(attName(a))}</b></a> ${B.pill(a.meta.status)}
      <span class="small">${esc(attDate(a))}</span>${pendingOf(a).length ? ` <span class="todo">${needsLabel(pendingOf(a))}</span>` : ''}
      ${gist ? `<div class="small">${esc(gist)}</div>` : ''}</div></li>`;
  }).join('');
  const runs = [...new Set(atts.flatMap(a => c.runsOf(a).filter(r => !r.missing)))];
  return `<div class="detail">${crumbs(d, {version: ver})}<h2>${esc(t.meta.title || tid)} ${B.pill(t.meta.status)}</h2>
    <div class="dmeta">${placement ? `<span>${placement}</span>` : ''}<span>${esc(B.day(t.meta.started))}${t.meta.ended ? ' → ' + esc(B.day(t.meta.ended)) : ''}</span><span>${esc(t.file)}</span></div>
    <div class="sec first"><h3>尝试的先后（${atts.length}）</h3>${steps ? `<ol class="steps">${steps}</ol>` : '<div class="none">还没有尝试</div>'}</div>
    ${runsMatrix(d, runs, '这条支线的各次运行')}
    ${headlineBars(d, runs, '这条支线的各次运行')}
    ${bodySections(t.body, ['假设', '现状', '结局和原因']).replace('class="sec first"', 'class="sec"')}</div>`;
}

const SECTION_ORDER = ['假设', '预期', '改了什么', '结果', '例子', '结论和原因'];
function attemptDetail(d, a) {
  const c = ctx(d), m = a.meta, name = d.name, pend = pendingOf(a), sections = a.sections || {};
  const t = c.trackById.get(m.track), ver = t && c.verById.get(c.homeOf.get(idOf(t)));
  const siblings = t ? c.tree.versions.flatMap(v => v.tracks).concat(c.tree.loose).find(n => idOf(n.track) === idOf(t)).attempts : [];
  const i = siblings.indexOf(a), prev = i > 0 ? siblings[i - 1] : null, next = i >= 0 && i < siblings.length - 1 ? siblings[i + 1] : null;
  const pre = (a.body.split(/^## /m)[0] || '').trim();
  const order = [...SECTION_ORDER, ...Object.keys(sections).filter(k => !SECTION_ORDER.includes(k))];
  const concluded = ['成功', '部分成功', '失败', '放弃', '待确认'].includes(m.status);
  const runs = c.runsOf(a);
  const secs = order.filter(k => k in sections || k === '预期' || k === '结果' || (k === '结论和原因' && concluded)).map(k => {
    const text = (sections[k] || '').trim();
    let body;
    if (text) body = `<div class="md">${B.md(text)}</div>`;
    else if (k === '预期') body = '<div class="none">没写预期</div>';
    else if (k === '结论和原因') body = `<span class="todo">${m.status === '待确认' ? '待你确认结论' : '待你填写结论和原因'}</span>`;
    else if (k === '结果') body = '';
    else body = '<div class="none">（空）</div>';
    // the numbers come from the run records, right under the reading of the results
    if (k === '结果') body += runs.length ? `<div class="auto"><div class="small">数字来自运行记录</div>${linkedRuns(d, a, runs)}</div>` : (text ? '' : '<div class="none">（空）</div>');
    return `<div class="sec"><h3>${esc(k)}</h3>${body}</div>`;
  }).join('').replace('<div class="sec">', pre ? '<div class="sec">' : '<div class="sec first">');
  const nav = (prev || next) ? `<div class="stepnav">${prev ? `<a href="${plink(name, 'roadmap', 'a:' + attKey(prev))}">← 上一次：${esc(attName(prev))}</a>` : '<span></span>'}
    ${next ? `<a href="${plink(name, 'roadmap', 'a:' + attKey(next))}">下一次：${esc(attName(next))} →</a>` : ''}</div>` : '';
  return `<div class="detail">${crumbs(d, {version: ver, track: t})}
    <h2>${esc(attName(a))} ${B.pill(m.status)}${pend.length ? ` <span class="todo">${needsLabel(pend)}</span>` : ''}</h2>
    <div class="dmeta"><span>${esc(attDate(a)) || '没有日期'}</span>${i >= 0 ? `<span>这条支线的第 ${i + 1} / ${siblings.length} 次尝试</span>` : '<span>没有归属支线</span>'}<span>${esc(a.file)}</span></div>
    ${pre ? `<div class="sec first"><div class="md">${B.md(pre)}</div></div>` : ''}${secs}${nav}</div>`;
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
function tabRuns(d, r) {
  const c = ctx(d), name = d.name, q = r.query || {};
  const sortKey = x => String((x.run || {}).started || '') + ' ' + x.id;
  const all = [...d.runs].sort((a, b) => sortKey(b).localeCompare(sortKey(a)));
  if (!all.length) return '<div class="card"><div class="empty">还没有运行记录（runs/）。</div></div>';
  const sel = (r.item != null && all.find(x => runKey(x) === r.item)) || null;
  const dataRuns = all.filter(x => (x.run || {}).kind === 'data'), evalRuns = all.filter(x => (x.run || {}).kind !== 'data');
  const view = (q.view === 'data' || (sel && (sel.run || {}).kind === 'data')) && dataRuns.length ? 'data' : evalRuns.length ? 'main' : 'data';
  const disp = view === 'data' ? B.parseDisplay(null) : c.disp;
  let runs = view === 'data' ? dataRuns : evalRuns;
  if (q.track) runs = runs.filter(x => { const a = c.attemptOf(x); return a && a.meta.track === q.track; });
  const st = ui.runView[view] || (ui.runView[view] = {group: undefined, all: false});
  const groups = B.metricGroups(runs.map(x => (x.run || {}).metrics));
  const {ordered, defaultName} = B.arrangeGroups(groups, disp);
  if (!groups.some(g => g.name === st.group)) st.group = defaultName;
  if (sel && !B.hasNumber(B.groupValues((sel.run || {}).metrics, st.group ?? ''))) {  // show the selected run's own group
    const g = B.metricGroups([(sel.run || {}).metrics]).find(x => x.runs);
    if (g && groups.some(x => x.name === g.name)) st.group = g.name;
  }
  const g = groups.find(x => x.name === st.group);
  const vals = x => g ? B.groupValues((x.run || {}).metrics, g.name) : {};
  const shown = g ? runs.filter(x => B.hasNumber(vals(x))) : [];
  const bare = runs.filter(x => !groups.some(y => B.hasNumber(B.groupValues((x.run || {}).metrics, y.name))));
  const allCols = g ? g.cols : [], brief = g ? columnsFor(g, disp) : [], cols = st.all ? allCols : brief.slice(0, sel ? 3 : 6);
  const qs = extra => ({view: view === 'data' ? 'data' : null, track: q.track, ...extra});

  // controls: which runs, which test set
  const label = x => esc(c.groupName(x.name));
  const visible = ordered.slice(0, SHOW_GROUPS);
  if (g && !visible.includes(g)) visible.push(g);
  const more = ordered.filter(x => !visible.includes(x));
  const trackOpts = [['', '全部支线'], ...d.tracks.filter(t => typeof t.meta.id === 'string').map(t => [t.meta.id, t.meta.title || t.meta.id])];
  const controls = `<div class="card"><div class="filters">
      <div class="chips">${evalRuns.length ? `<a class="chip${view === 'main' ? ' on' : ''}" href="${plink(name, 'runs', null, {track: q.track})}">评测和训练 ${evalRuns.length}</a>` : ''}
        ${dataRuns.length ? `<a class="chip${view === 'data' ? ' on' : ''}" href="${plink(name, 'runs', null, {view: 'data'})}">数据 ${dataRuns.length}</a>` : ''}</div>
      ${view === 'main' ? `<select data-rtrack>${trackOpts.map(([v, t]) => `<option value="${esc(v)}"${(q.track || '') === v ? ' selected' : ''}>${esc(t)}</option>`).join('')}</select>` : ''}
    </div>
    ${ordered.length > 1 ? `<div class="tabs" style="padding-top:8px"><span class="small">测试集</span>` +
      visible.map(x => `<button class="tab${x === g ? ' on' : ''}" data-view="${view}" data-group="${esc(x.name)}" title="${esc((c.setup.datasets.get(x.name) || {}).about || x.name)}">${label(x)} <span class="small">${x.runs}</span></button>`).join('') +
      (more.length ? `<select data-view="${view}"><option value="">更多（${more.length}）</option>` + more.map(x => `<option value="g:${esc(x.name)}">${label(x)}（${x.runs}）</option>`).join('') + '</select>' : '') + '</div>' : ''}
    ${g ? groupNote(d, g.name, shown.length, runs.length - shown.length - bare.length) : ''}
    ${disp.problems.length ? `<div class="dnote">project.json 的 display 里有用不上的设置：${disp.problems.map(esc).join('；')}。已按默认方式显示。</div>` : ''}
    </div>`;

  // bars: the headline metric if this test set has it, otherwise the first column shown
  let barsHtml = '';
  if (g && shown.length > 1) {
    const h = disp.headline, metric = h && h.group === g.name ? h.metric : (h && g.cols.includes(h.metric) ? h.metric : cols[0]);
    const sm = c.setup.metrics.get(metric), better = h && h.metric === metric ? h.better : sm && sm.better || null;
    const lab = labelsFor(c, shown);
    const chart = B.bars(shown.map(x => { const v = vals(x);
      return {label: lab(x), value: v[metric], on: x === sel, href: plink(name, 'runs', runKey(x), qs({})),
        dim: typeof v.n === 'number' && typeof v.of === 'number' && v.n < v.of, html: cellHtml(v, metric, disp.ratio)}; }), better);
    if (chart) barsHtml = `<div class="card"><h2>${esc(c.metricName(metric))} · ${esc(c.groupName(g.name))} <span class="meta">${better ? BETTER[better] + '，绿色是最好的一次；' : '没有声明方向，只按大小排；'}斜纹是抽查，不参与比较</span></h2><div class="pad">${chart}</div></div>`;
  }

  const row = x => {
    const rr = x.run || {}, v = vals(x), p = placeOfRun(d, x);
    return `<tr class="run${x === sel ? ' open' : ''}" data-href="${plink(name, 'runs', runKey(x), qs({}))}">
      <td><b>${esc(c.label(x))}</b>${c.label(x) !== runKey(x) ? `<div class="small">${esc(runKey(x))}</div>` : ''}</td>
      <td>${B.pill(rr.status)} ${B.livePill(x.liveness)}${p.track ? `<div class="small">${esc(p.track.meta.title || idOf(p.track))}</div>` : ''}</td>
      ${cols.map(col => `<td class="n">${cellHtml(v, col, disp.ratio)}</td>`).join('')}</tr>`;
  };
  const table = shown.length ? `<div class="scroll"><table class="runs"><tr><th>运行</th><th>状态 / 支线</th>${cols.map(col => metricTh(c, col)).join('')}</tr>${shown.map(row).join('')}</table></div>` +
    (brief.length < allCols.length || st.all ? `<div class="tmeta"><a href="#" data-allcols="${view}">${st.all ? '只显示主要列' : `显示全部列（${allCols.length}）`}</a></div>` : '') : '<div class="empty">这个测试集下没有运行。</div>';
  const bareHtml = bare.length ? `<div class="bare"><span class="small">还没有指标的运行：</span>` +
    bare.map(x => `<a href="${plink(name, 'runs', runKey(x), qs({}))}">${esc(c.label(x))}</a>`).join('') + '</div>' : '';
  if (!sel) return controls + barsHtml + `<div class="card">${table}${bareHtml}<div class="pad small">点一行，看这次运行属于哪次尝试、各测试集上的指标、配置、产物和曲线。</div></div>`;
  return controls + barsHtml + `<div class="md2 wide"><div class="card" style="margin:0">${table}${bareHtml}</div><div>${runDetail(d, sel)}</div></div>`;
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
  return `<div class="detail">${crumbs(d, p)}<h2>${esc(c.label(x))} ${B.pill(rr.status)} ${B.livePill(x.liveness)}</h2>
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
function docItems(d) {
  const out = [];
  if (d.plan) out.push({id: 'plan', group: '方案', title: '整体方案', sub: d.plan.file, body: d.plan.body, mtime: d.plan.mtime});
  for (const x of d.docs || []) {
    const root = !x.file.startsWith('docs/');
    out.push({id: root ? x.file : x.file.slice(5), group: root ? '项目笔记' : '说明', title: x.meta.title || (x.file === 'NOTES.md' ? '进度笔记' : x.file === 'REPORT.md' ? '总报告' : x.file.split('/').pop().replace(/\.md$/, '')),
      sub: x.file, body: x.body, mtime: x.mtime});
  }
  return out;
}
function tabDocs(d, r) {
  const docs = docItems(d), name = d.name;
  if (!docs.length) return '<div class="card"><div class="empty">还没有文档（board/plan.md、board/docs/*.md、项目根目录的 NOTES.md 和 REPORT.md）。各版本的方案在「路线」里。</div></div>';
  const sel = docs.find(x => x.id === r.item) || docs[0];
  let last = null;
  const items = docs.map(x => {
    const head = x.group !== last ? `<div class="tgroup">${esc(x.group)}</div>` : '';
    last = x.group;
    return head + `<a class="li${x === sel ? ' on' : ''}" href="${plink(name, 'docs', x.id)}"><div class="row1"><span class="name">${esc(x.title)}</span></div>
      <div class="row2"><span>${esc(x.sub)}</span><span>${B.ago(x.mtime, d.now)}更新</span></div></a>`;
  }).join('');
  return `<div class="md2"><div class="list"><div class="items">${items}</div><div class="pad small">各版本的方案和支线说明在「路线」里。</div></div>
    <div class="detail"><h2>${esc(sel.title)}</h2><div class="dmeta"><span>${esc(sel.sub)}</span><span>${B.ago(sel.mtime, d.now)}更新</span></div>
      <div class="md">${B.md(sel.body)}</div></div></div>`;
}

// ============================================================ events
document.addEventListener('click', e => {
  const t = e.target;
  if (t.closest('#gpu-refresh')) { fetch('/api/refresh').then(() => setTimeout(poll, 4000)); return; }
  const copy = t.closest('.copy');
  if (copy) { navigator.clipboard.writeText(copy.dataset.c).then(() => { const x = copy.textContent; copy.textContent = '已复制'; setTimeout(() => copy.textContent = x, 1200); }); return; }
  if (t.closest('[data-example]')) { ui.exampleOpen = !ui.exampleOpen; render(); return; }
  const ag = t.closest('[data-agroup]');
  if (ag) { e.preventDefault(); ui.attGroup[ag.dataset.att] = ag.dataset.agroup; render(); return; }
  const tab = t.closest('button[data-group]');
  if (tab) { ui.runView[tab.dataset.view].group = tab.dataset.group; render(); return; }
  const allc = t.closest('[data-allcols]');
  if (allc) { e.preventDefault(); const st = ui.runView[allc.dataset.allcols]; st.all = !st.all; render(); return; }
  if (t.closest('[data-runallcols]')) { e.preventDefault(); ui.runAllCols = !ui.runAllCols; render(); return; }
  const row = t.closest('tr[data-href]');
  if (row && !t.closest('a')) location.hash = row.dataset.href;
});
document.addEventListener('change', e => {
  const sel = e.target.closest('select[data-view]');
  if (sel && sel.value.startsWith('g:')) { ui.runView[sel.dataset.view].group = sel.value.slice(2); render(); return; }
  const tr = e.target.closest('select[data-rtrack]');
  if (tr) { const r = route(); location.hash = plink(r.name, 'runs', null, {track: tr.value}); }
});
document.addEventListener('mousedown', e => { copying = !!e.target.closest('.copy'); });
window.addEventListener('hashchange', render);

// ============================================================ polling
async function poll() {
  const [st, pj] = await Promise.allSettled([getJSON('/api/status'), getJSON('/api/projects')]);
  const firstProjects = !S.projects && pj.status === 'fulfilled';
  S.offline = st.status === 'rejected' && pj.status === 'rejected';
  if (st.status === 'fulfilled') S.status = st.value;
  if (pj.status === 'fulfilled') S.projects = pj.value;
  const r = route();
  // don't disturb a project page, except once when the project list first arrives (entry badge, counts)
  if (r.page === 'project' && !firstProjects) { renderSide(r); ensureDetail(r.name); document.title = titleFor(r); return; }
  if (r.page === 'gpu' && copying) { copying = false; renderSide(r); return; }
  render();
}
render();
poll();
setInterval(poll, 5000);
