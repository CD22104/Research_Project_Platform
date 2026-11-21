// The console: routing, sidebar and pages. Needs common.js (Board); no other dependencies.
// Routes (hash):  #/overview  #/gpu  #/todo  #/new  #/p/<project>/<tab>[/<item>][?filters]
const B = Board, esc = B.esc, $ = id => document.getElementById(id);

// ============================================================ data
const S = {status: null, projects: null, detail: {}, fetchedAt: {}, loading: {}, missing: {}, offline: false};
const ui = {runView: {}, openRuns: new Set(), exampleOpen: false, attGroup: {}};  // view state kept across refreshes
const getJSON = url => fetch(url).then(r => r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status)));

// ============================================================ routing
const TABS = [['overview', '概览'], ['experiments', '实验'], ['roadmap', '路线'], ['runs', '运行'], ['examples', '样例'], ['docs', '文档']];
const PAGES = ['overview', 'gpu', 'todo', 'new'];

function route() {
  const raw = location.hash.replace(/^#/, '');
  const [path, q = ''] = raw.split('?');
  const parts = path.split('/').filter(Boolean).map(s => { try { return decodeURIComponent(s); } catch (e) { return s; } });
  const query = Object.fromEntries(new URLSearchParams(q));
  if (parts[0] === 'p' && parts[1])
    return {page: 'project', name: parts[1], tab: TABS.some(t => t[0] === parts[2]) ? parts[2] : 'overview', item: parts[3] ?? null, query};
  return {page: PAGES.includes(parts[0]) ? parts[0] : 'overview', query};
}
const plink = (name, tab = 'overview', item = null, query = null) => {
  const q = query && Object.entries(query).filter(([, v]) => v != null && v !== '');
  return B.projectUrl(name, tab, item) + (q && q.length ? '?' + new URLSearchParams(q) : '');
};
// old addresses: /#projects, /#gpu (and project.html?p= redirects here)
if (location.hash === '#projects' || location.hash === '' || location.hash === '#') history.replaceState(null, '', '#/overview');
else if (location.hash === '#gpu') history.replaceState(null, '', '#/gpu');

// ============================================================ helpers
const attKey = a => String(a.meta.id || a.file.split('/').pop().replace(/\.md$/, ''));
const attName = a => attKey(a).replace(/^\d{4}-\d{2}-\d{2}-/, '');
const attDate = a => B.day(a.meta.date) || (attKey(a).match(/^\d{4}-\d{2}-\d{2}/) || [''])[0];
const runKey = r => String((r.run || {}).id || r.id);
const pendingOf = a => (a.needs_input || []).filter(x => x !== '预期');
const fileUrl = (name, rel) => '/files/' + encodeURIComponent(name) + '/' + rel.split('/').map(encodeURIComponent).join('/');
const dateKey = a => String(a.meta.date || a.meta.started || attDate(a) || '');
const gpusText = x => Array.isArray(x.gpus) && x.gpus.length ? ' 卡 ' + x.gpus.join(',') : '';
// status counts of records, most frequent first: [[status, n]]
function statusCounts(records) {
  const m = new Map();
  for (const r of records) { const s = typeof r.meta.status === 'string' && r.meta.status ? r.meta.status : '未写'; m.set(s, (m.get(s) || 0) + 1); }
  return [...m].sort((a, b) => b[1] - a[1]);
}
const countLine = pairs => pairs.map(([s, n]) => `${esc(s)} <b>${n}</b>`).join(' · ');
const needsLabel = needs => needs.includes('结论待确认') ? '待你确认结论' : needs.includes('结论和原因') ? '待你填写结论和原因' : needs.join('、');

// ============================================================ sidebar
function renderSide(r) {
  const pj = S.projects ? S.projects.projects : null;
  const free = S.status ? B.freeCount(S.status) : null;
  const todo = pj ? pj.reduce((n, p) => n + (p.pending || []).length, 0) : null;
  const item = (href, label, cnt, on) => `<a class="sitem${on ? ' on' : ''}" href="${href}"><span class="lbl">${label}</span>` +
    (cnt != null ? `<span class="cnt">${cnt}</span>` : '') + '</a>';
  const order = {'进行中': 0, '暂停': 1, '已归档': 2};
  const list = pj ? [...pj].sort((a, b) => (order[a.status] ?? 1) - (order[b.status] ?? 1) || String(a.title).localeCompare(String(b.title))) : [];
  $('side').innerHTML = `<a class="brand" href="#/overview">${esc(B.APP_NAME)}</a>` +
    item('#/overview', '总览', null, r.page === 'overview') +
    item('#/gpu', 'GPU', free != null ? free + ' 空闲' : null, r.page === 'gpu') +
    item('#/todo', '待办', todo || null, r.page === 'todo') +
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
  else if (r.page === 'project') html = pageProject(r);
  else html = pageOverview();
  $('main').innerHTML = html;
  for (const id of open) { const el = document.getElementById(id); if (el) el.open = true; }
  fitExample();
  if (location.hash !== lastHash) {
    lastHash = location.hash;
    const target = r.page === 'project' && r.tab === 'runs' && r.item != null && document.querySelector(`[data-dir][data-key="${CSS.escape(r.item)}"]`);
    if (target) target.scrollIntoView({block: 'start'}); else window.scrollTo(0, 0);
  }
  document.title = titleFor(r);
}
function titleFor(r) {
  const app = B.APP_NAME;
  if (r.page === 'gpu') return (S.status ? `(${B.freeCount(S.status)} 空) ` : '') + app;
  if (r.page === 'todo') return `待办 · ${app}`;
  if (r.page === 'new') return `新建项目 · ${app}`;
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
  const todo = list.reduce((n, p) => n + (p.pending || []).length, 0);
  const running = list.reduce((n, p) => n + (p.active_runs || []).length, 0);
  const sc = pj.scan || {};
  const notes = [];
  if (sc.running_since && now - sc.running_since > 5) notes.push(`正在扫描共享盘，已 ${B.span(now - sc.running_since)}；下面是上一次的结果`);
  if (sc.error) notes.push(`最近一次扫描失败（${esc(sc.error)}），下面是 ${B.ago(sc.finished, now)}的结果`);
  const cards = list.map(p => {
    const ts = Object.entries(p.track_status || {}), as = Object.entries(p.attempt_status || {});
    const runningTracks = (p.track_status || {})['进行中'] || 0;
    return `<a class="pcard" href="${plink(p.name)}">
      <div class="t"><span class="dot d-${B.tone(p.status)}"></span><b>${esc(p.title)}</b>${B.pill(p.status)}
        ${p.current_version ? `<span class="small">当前版本 ${esc(p.current_version)}</span>` : ''}</div>
      <div class="c">${p.conclusion ? esc(p.conclusion) : '<span class="faint">还没有结论</span>'}</div>
      <div class="n"><span>支线 <b>${p.n_tracks ?? 0}</b>${runningTracks ? `（${runningTracks} 条进行中）` : ''}</span>
        <span>尝试 <b>${p.n_attempts ?? 0}</b></span><span>运行 <b>${p.n_runs ?? 0}</b></span></div>
      ${as.length ? `<div class="small">${countLine(as.sort((a, b) => b[1] - a[1]))}</div>` : ''}
      ${(p.active_runs || []).map(r => `<div class="small">正在跑 ${esc(r.id)} · ${esc(r.server || '?')}${esc(gpusText(r))} ${B.livePill(r.liveness)}</div>`).join('')}
      ${(p.pending || []).length ? `<div class="w">${p.pending.length} 条等你处理</div>` : ''}
      ${p.stale ? `<div class="w">数据是 ${B.ago(p.scanned_at, now)}的，最近一次读取失败</div>` : ''}
      ${p.n_errors ? `<div class="w">${p.n_errors} 个记录文件读取有问题</div>` : ''}
      <div class="small">最近动态 ${B.ago(p.mtime, now)}</div></a>`;
  }).join('');
  return `<h1 class="pt">总览</h1>
    <div class="summary">
      <a class="stat" href="#/gpu"><div class="k">空闲的卡</div><div class="v">${free ?? '—'}</div><div class="s">所有服务器合计</div></a>
      <a class="stat" href="#/todo"><div class="k">等你处理</div><div class="v">${todo}</div><div class="s">所有项目合计</div></a>
      <div class="stat"><div class="k">正在跑的任务</div><div class="v">${running}</div><div class="s">按项目记录</div></div>
      <div class="stat"><div class="k">项目</div><div class="v">${list.length}</div><div class="s">${sc.finished ? '扫描于 ' + B.ago(sc.finished, now) : ''}</div></div>
    </div>` + notes.map(t => `<div class="notice">${t}</div>`).join('') +
    (list.length ? `<div class="pcards">${cards}</div>`
      : `<div class="card"><div class="empty">还没有项目。在 ${esc(sc.root || '共享盘')}/&lt;项目名&gt;/board/ 下写好 project.json，一分钟内就会出现在这里。</div></div>`);
}

// ============================================================ todo
function pageTodo() {
  const pj = S.projects;
  if (!pj) return '<div class="empty">加载中…</div>';
  const later = pj.projects.filter(p => (p.pending || []).length).map(p => {
    const n = p.pending.length, confirm = p.pending.filter(x => x.needs.includes('结论待确认')).length;
    const parts = [confirm ? `${confirm} 条尝试等你确认结论` : '', n - confirm ? `${n - confirm} 条尝试缺结论和原因` : ''].filter(Boolean);
    return `<li><a href="${plink(p.name, 'experiments', null, {todo: 1})}"><b>${esc(p.title)}</b>：${parts.join('，')}</a>
      <div class="small">在实验页逐条查看；结论由你确认后，在该项目的会话里说明即可</div></li>`;
  });
  return `<h1 class="pt">待办</h1><div class="cols2">
    <div class="card"><h2>卡着进度的 <span class="meta">需要你先做，事情才能往下走</span></h2>
      <div class="empty">没有。各会话写进 board/todos.jsonl 的待办会出现在这里（下一步上线）。</div></div>
    <div class="card"><h2>不急的 <span class="meta">有空时处理</span></h2>
      ${later.length ? `<ul class="tlist">${later.join('')}</ul>` : '<div class="empty">没有。</div>'}</div></div>`;
}

function pageNew() {
  return `<h1 class="pt">新建项目</h1><div class="card"><div class="pad md">
    <p>控制台只读，不在页面上建项目。请在任意一台服务器上的 Claude 会话里说：</p>
    <pre><code>新建项目 &lt;名字&gt;</code></pre>
    <p>会话会在共享盘 <code>/shared/ssd/dchen/&lt;名字&gt;/</code> 建好目录和 <code>board/</code>，一分钟内左侧栏就会出现这个项目。</p></div></div>`;
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
  const counts = {experiments: d.attempts.length, roadmap: d.tracks.length, runs: d.runs.length, docs: docItems(d).length};
  const head = `<div class="phead"><h1>${esc(p.title || d.name)}</h1>${B.pill(p.status)}
      ${p.current_version ? `<span class="small">当前版本 <b>${esc(p.current_version)}</b></span>` : ''}
      <span class="small right">数据读取于 ${B.ago(d.scanned_at, d.now)} · 每分钟刷新</span></div>
    <nav class="ptabs">${TABS.map(([k, label]) => `<a class="ptab${k === r.tab ? ' on' : ''}" href="${plink(d.name, k)}">${label}` +
      (counts[k] ? `<span class="cnt">${counts[k]}</span>` : '') + '</a>').join('')}</nav>`;
  const body = {overview: tabOverview, experiments: tabExperiments, roadmap: tabRoadmap, runs: tabRuns, examples: tabExamples, docs: tabDocs}[r.tab](d, r);
  return head + notices(d) + body + (r.tab === 'overview' && d.errors.length ? errorsCard(d) : '');
}

function notices(d) {
  const out = [], sc = d.scan || {}, now = d.now;
  if (d.stale) out.push(d.scanned_at ? `这个项目的数据是 ${B.ago(d.scanned_at, now)}的，最近一次读取失败（${esc(d.stale.error)}）。`
    : `这个项目还没有成功读取过（${esc(d.stale.error)}）。`);
  if (sc.running_since && now - sc.running_since > 5) out.push(`正在扫描共享盘，已 ${B.span(now - sc.running_since)}；下面是上一次的结果。`);
  if (sc.error) out.push(`最近一次扫描失败（${esc(sc.error)}），下面是 ${B.ago(sc.finished, now)}的结果。`);
  return out.map(t => `<div class="notice">${t}</div>`).join('');
}
const errorsCard = d => `<div class="card"><h2>读取有问题的文件 <span class="meta">${d.errors.length} 个 · 修好后下一次扫描（约 1 分钟）就会消失</span></h2>
  <table class="mt" style="margin:0 6px 6px">${d.errors.map(e => `<tr><td><code>${esc(e.file)}</code></td><td class="small" style="white-space:normal">${esc(e.error)}</td></tr>`).join('')}</table></div>`;

// ------------------------------------------------------------ 概览
function tabOverview(d) {
  const p = d.project || {}, disp = B.parseDisplay(p.display), name = d.name;
  const tracks = statusCounts(d.tracks), atts = statusCounts(d.attempts);
  const running = (tracks.find(([s]) => s === '进行中') || [0, 0])[1];
  const active = d.runs.filter(x => (x.run || {}).status === '运行中');
  const best = B.bestRun(d.runs, disp.headline);
  const pending = d.attempts.filter(a => pendingOf(a).length).sort((a, b) => dateKey(b).localeCompare(dateKey(a)));
  let bestCard;
  if (best) {
    const h = disp.headline, pr = disp.ratio[h.metric], v = best.vals;
    const frac = pr && typeof v[pr[0]] === 'number' && typeof v[pr[1]] === 'number' ? `（${B.num(v[pr[0]])}/${B.num(v[pr[1]])}）` : '';
    bestCard = `<a class="stat" href="${plink(name, 'runs', runKey(best.run))}"><div class="k">最好的 ${esc(h.metric)} · ${esc(h.group)}</div>
      <div class="v">${B.num(best.value)}</div><div class="s">来自运行 <b>${esc(runKey(best.run))}</b>${frac}</div></a>`;
  } else {
    bestCard = `<div class="stat"><div class="k">最好的主指标</div><div class="v">—</div>
      <div class="s">${disp.headline ? '还没有运行记录这个指标' : '未设置：在 project.json 的 display.headline 里写明分组、指标和方向'}</div></div>`;
  }
  const ev = B.foldEvents(B.events(d)).slice(0, 14);
  return `${p.conclusion ? `<p class="concl">${esc(p.conclusion)}</p>` : '<p class="concl faint">还没有结论</p>'}
    ${p.question ? `<p class="question">研究问题：${esc(p.question)}</p>` : ''}
    <div class="stats">
      <a class="stat" href="${plink(name, 'roadmap')}"><div class="k">支线</div><div class="v">${d.tracks.length}</div>
        <div class="s">${running ? `<b>${running}</b> 条进行中 · ` : ''}${countLine(tracks.filter(([s]) => s !== '进行中')) || '还没有支线'}</div></a>
      <a class="stat" href="${plink(name, 'experiments')}"><div class="k">尝试</div><div class="v">${d.attempts.length}</div>
        <div class="s">${countLine(atts) || '还没有尝试'}</div></a>
      ${bestCard}
      <a class="stat" href="${plink(name, 'runs')}"><div class="k">正在跑</div><div class="v">${active.length}</div>
        <div class="s">${active.length ? active.map(x => `${esc(runKey(x))} · ${esc((x.run || {}).server || '?')}${esc(gpusText(x.run || {}))} ${B.livePill(x.liveness)}`).join('<br>') : '没有正在跑的任务'}</div></a>
    </div>
    <div class="cols2">
      <div class="card"><h2>等你处理 <span class="meta">${pending.length} 条</span></h2>
        ${pending.length ? `<ul class="feed">${pending.slice(0, 12).map(a => `<li><span class="when">${esc(attDate(a))}</span>
          <span><a href="${plink(name, 'experiments', attKey(a))}">${esc(attName(a))}</a> <span class="todo">${needsLabel(pendingOf(a))}</span></span></li>`).join('')}</ul>` +
          (pending.length > 12 ? `<div class="pad small"><a href="${plink(name, 'experiments', null, {todo: 1})}">全部 ${pending.length} 条 →</a></div>` : '')
          : '<div class="empty">没有等你处理的事。</div>'}</div>
      <div class="card"><h2>最近动态</h2>
        ${ev.length ? `<ul class="feed">${ev.map(e => `<li><span class="when">${B.ago(e.t, d.now)}</span>
          <a href="${plink(name, e.tab, e.item)}">${esc(e.text)}</a></li>`).join('')}</ul>` : '<div class="empty">还没有记录。</div>'}</div>
    </div>`;
}

// ------------------------------------------------------------ 实验: attempt list on the left, the selected attempt on the right
const SECTION_ORDER = ['假设', '预期', '改了什么', '结果', '结论和原因'];
function tabExperiments(d, r) {
  const q = r.query || {}, name = d.name, disp = B.parseDisplay((d.project || {}).display);
  const trackTitle = id => { const t = d.tracks.find(x => x.meta.id === id); return t ? t.meta.title || id : id; };
  const all = [...d.attempts].sort((a, b) => dateKey(b).localeCompare(dateKey(a)) || attKey(a).localeCompare(attKey(b)));
  const trackIds = new Set(d.tracks.map(t => t.meta.id).filter(Boolean));
  let list = all;
  if (q.track) list = list.filter(a => q.track === '_none' ? !trackIds.has(a.meta.track) : a.meta.track === q.track);
  if (q.status) list = list.filter(a => (typeof a.meta.status === 'string' && a.meta.status ? a.meta.status : '未写') === q.status);
  if (q.todo) list = list.filter(a => pendingOf(a).length);
  const sel = (r.item != null && all.find(a => attKey(a) === r.item)) || list[0] || null;
  const qs = extra => ({track: q.track, status: q.status, todo: q.todo, ...extra});

  const trackOpts = [['', `全部支线（${all.length}）`], ...d.tracks.map(t => [t.meta.id, `${t.meta.title || t.meta.id}（${all.filter(a => a.meta.track === t.meta.id).length}）`])];
  const loose = all.filter(a => !trackIds.has(a.meta.track)).length;
  if (loose) trackOpts.push(['_none', `未归属支线（${loose}）`]);
  const statuses = statusCounts(q.track ? all.filter(a => q.track === '_none' ? !trackIds.has(a.meta.track) : a.meta.track === q.track) : all);
  const nTodo = all.filter(a => pendingOf(a).length).length;
  const chip = (label, query, on) => `<a class="chip${on ? ' on' : ''}" href="${plink(name, 'experiments', null, query)}">${label}</a>`;
  const filters = `<div class="filters"><select data-qtrack>${trackOpts.map(([v, t]) => `<option value="${esc(v)}"${(q.track || '') === v ? ' selected' : ''}>${esc(t)}</option>`).join('')}</select>
    <div class="chips">${chip('全部', qs({status: null, todo: null}), !q.status && !q.todo)}
      ${statuses.map(([s, n]) => chip(`${esc(s)} ${n}`, qs({status: s, todo: null}), q.status === s)).join('')}
      ${nTodo ? chip(`等你处理 ${nTodo}`, qs({todo: 1, status: null}), !!q.todo) : ''}</div></div>`;
  const items = list.map(a => {
    const pend = pendingOf(a);
    return `<a class="li${a === sel ? ' on' : ''}" href="${plink(name, 'experiments', attKey(a), qs({}))}">
      <div class="row1">${B.pill(a.meta.status) || '<span class="pill t-other">无状态</span>'}<span class="name">${esc(attName(a))}</span></div>
      <div class="row2"><span>${esc(attDate(a))}</span><span>${esc(trackIds.has(a.meta.track) ? trackTitle(a.meta.track) : '未归属支线')}</span>
        ${pend.length ? `<span style="color:var(--busy);font-weight:600">${needsLabel(pend)}</span>` : ''}</div></a>`;
  }).join('') || '<div class="empty">没有符合条件的尝试。</div>';
  return `<div class="md2"><div class="list">${filters}<div class="items">${items}</div></div>
    <div>${sel ? attemptDetail(d, sel, disp) : '<div class="detail empty">还没有尝试。</div>'}</div></div>`;
}

function attemptDetail(d, a, disp) {
  const m = a.meta, name = d.name, pend = pendingOf(a), sections = a.sections || {};
  const trackOk = d.tracks.some(t => t.meta.id === m.track);
  const pre = (a.body.split(/^## /m)[0] || '').trim();
  const order = [...SECTION_ORDER, ...Object.keys(sections).filter(k => !SECTION_ORDER.includes(k))];
  const concluded = ['成功', '部分成功', '失败', '放弃', '待确认'].includes(m.status);
  const secs = order.filter(k => k in sections || (k === '预期') || (k === '结论和原因' && concluded)).map((k, i) => {
    const text = (sections[k] || '').trim();
    let body;
    if (text) body = `<div class="md">${B.md(text)}</div>`;
    else if (k === '预期') body = '<div class="none">没写预期</div>';
    else if (k === '结论和原因') body = `<span class="todo">${m.status === '待确认' ? '待你确认结论' : '待你填写结论和原因'}</span>`;
    else body = '<div class="none">（空）</div>';
    return `<div class="sec${i === 0 && !pre ? ' first' : ''}"><h3>${esc(k)}</h3>${body}</div>`;
  }).join('');
  const runs = Array.isArray(m.runs) ? m.runs : [];
  return `<div class="detail"><h2>${esc(attName(a))} ${B.pill(m.status)}${pend.length ? ` <span class="todo">${needsLabel(pend)}</span>` : ''}</h2>
    <div class="dmeta"><span>支线：${trackOk ? `<a href="${plink(name, 'experiments', null, {track: m.track})}">${esc((d.tracks.find(t => t.meta.id === m.track).meta.title) || m.track)}</a>`
      : `<span>${esc(m.track || '未归属支线')}</span>`}</span><span>日期：${esc(attDate(a)) || '—'}</span><span>${esc(a.file)}</span></div>
    ${pre ? `<div class="sec first"><div class="md">${B.md(pre)}</div></div>` : ''}${secs}
    <div class="sec"><h3>关联的运行${runs.length ? `（${runs.length}）` : ''}</h3>${runs.length ? linkedRuns(d, a, runs, disp) : '<div class="none">没有关联运行</div>'}</div>
    </div>`;
}

// metric table for an attempt's runs, following project.json "display"
function cellHtml(vals, c, ratio) {
  const p = ratio[c];
  return p && typeof vals[p[0]] === 'number' && typeof vals[p[1]] === 'number'
    ? `${B.num(vals[c])} <span class="small">(${B.num(vals[p[0]])}/${B.num(vals[p[1]])})</span>` : B.num(vals[c]);
}
function columnsFor(g, disp) {
  const primary = disp.primary.filter(c => g.cols.includes(c));
  if (primary.length) return primary;
  const parts = new Set(Object.entries(disp.ratio).filter(([c]) => g.cols.includes(c)).flatMap(([, p]) => p));
  return g.cols.filter(c => !parts.has(c));
}
function linkedRuns(d, a, ids, disp) {
  const name = d.name;
  const runs = ids.map(id => d.runs.find(r => runKey(r) === String(id))).filter(Boolean);
  const missing = ids.filter(id => !d.runs.some(r => runKey(r) === String(id)));
  const chips = `<div class="chips" style="margin-bottom:6px">${runs.map(r => `<a class="chip" href="${plink(name, 'runs', runKey(r))}">${esc(runKey(r))}</a>`).join('')}</div>` +
    (missing.length ? `<div class="none">找不到这些运行的记录：${missing.map(esc).join('、')}</div>` : '');
  const groups = B.metricGroups(runs.map(r => (r.run || {}).metrics)).filter(g => g.runs);
  if (!groups.length) return chips + (runs.length ? '<div class="none">这些运行还没有指标</div>' : '');
  const {ordered, defaultName} = B.arrangeGroups(groups, disp);
  if (runs.length === 1) {  // one run: a row per group, primary metrics as columns
    const vals = g => B.groupValues((runs[0].run || {}).metrics, g.name);
    const cols = [...new Set(ordered.flatMap(g => columnsFor(g, disp)))].slice(0, 8);
    return chips + `<div class="scroll"><table class="mt"><tr><th>分组</th>${cols.map(c => `<th class="num">${esc(c)}</th>`).join('')}</tr>` +
      ordered.map(g => `<tr><td>${esc(g.name || '其他指标')}</td>${cols.map(c => `<td class="num">${cellHtml(vals(g), c, disp.ratio)}</td>`).join('')}</tr>`).join('') +
      '</table></div>';
  }
  // several runs: one group at a time, a row per run
  const key = attKey(a);
  if (!ordered.some(g => g.name === ui.attGroup[key])) ui.attGroup[key] = defaultName;
  const g = ordered.find(x => x.name === ui.attGroup[key]), cols = columnsFor(g, disp).slice(0, 8);
  return chips + (ordered.length > 1 ? `<div class="chips" style="margin:4px 0">${ordered.map(x =>
      `<a href="#" class="chip${x === g ? ' on' : ''}" data-agroup="${esc(x.name)}" data-att="${esc(key)}">${esc(x.name || '其他指标')}</a>`).join('')}</div>` : '') +
    `<div class="scroll"><table class="mt"><tr><th>运行</th>${cols.map(c => `<th class="num">${esc(c)}</th>`).join('')}</tr>` +
    runs.map(r => { const v = B.groupValues((r.run || {}).metrics, g.name);
      return `<tr><td><a href="${plink(name, 'runs', runKey(r))}">${esc(runKey(r))}</a></td>${cols.map(c => `<td class="num">${cellHtml(v, c, disp.ratio)}</td>`).join('')}</tr>`; }).join('') +
    '</table></div>';
}

// ------------------------------------------------------------ 路线 (interim: versions as trunk, tracks under them)
function tabRoadmap(d) {
  const name = d.name, versions = [...d.versions].sort((a, b) => String(a.meta.date || a.file).localeCompare(String(b.meta.date || b.file)));
  const vids = new Set(versions.map(v => v.meta.id).filter(Boolean));
  const mergedInto = new Map();
  for (const v of versions) for (const tid of Array.isArray(v.meta.merged_tracks) ? v.meta.merged_tracks : []) if (v.meta.id && !mergedInto.has(tid)) mergedInto.set(tid, v.meta.id);
  const home = t => vids.has(t.meta.from_version) ? t.meta.from_version : mergedInto.get(t.meta.id) || null;
  const note = (t, vid) => { const into = mergedInto.get(t.meta.id); return !into ? '' : into === vid ? '并入此版本' : `→ 并入 ${into}`; };
  const cur = (d.project || {}).current_version;
  const track = (t, vid) => {
    const m = t.meta, atts = d.attempts.filter(a => m.id && a.meta.track === m.id).sort((a, b) => dateKey(a).localeCompare(dateKey(b)));
    const gist = B.firstSentence(t.body, '假设');
    return `<div class="track k-${B.tone(m.status)}"><div class="thead">${B.pill(m.status)} <b>${esc(m.title || m.id || t.file)}</b>
        <span class="small">${esc(B.day(m.started))}${m.ended ? ' → ' + esc(B.day(m.ended)) : ''} · ${atts.length} 次尝试</span>
        ${note(t, vid) ? `<span class="merge">${esc(note(t, vid))}</span>` : ''}
        <a class="small" href="${plink(name, 'experiments', null, {track: m.id})}">看这条支线的尝试 →</a></div>
      ${gist ? `<div class="gist"><span class="small">假设</span> ${esc(gist)}</div>` : ''}
      <div class="attl">${atts.map(a => `<a class="chip" href="${plink(name, 'experiments', attKey(a))}">${esc(attName(a))}${a.meta.status ? ' · ' + esc(a.meta.status) : ''}</a>`).join('')}</div></div>`;
  };
  let html = versions.map(v => `<div class="ver${v.meta.id && v.meta.id === cur ? ' cur' : ''}"><span class="vdot"></span>
      <div class="vhead"><b>${esc(v.meta.id || v.file)}</b> ${esc(v.meta.title || '')}<span class="small">${esc(B.day(v.meta.date))}${v.meta.parent ? ' · 来自 ' + esc(v.meta.parent) : ''}</span>
        ${v.meta.id && v.meta.id === cur ? '<span class="pill t-run">当前版本</span>' : ''}<a class="small" href="${plink(name, 'docs', 'ver:' + (v.meta.id || v.file))}">版本说明 →</a></div>
      ${d.tracks.filter(t => v.meta.id && home(t) === v.meta.id).map(t => track(t, v.meta.id)).join('')}</div>`).join('');
  const loose = d.tracks.filter(t => !home(t));
  if (loose.length) html += `<div class="group"><h3>没有挂到版本上的支线</h3>${loose.map(t => track(t, null)).join('')}</div>`;
  return `<div class="card"><h2>路线 <span class="meta">${versions.length} 个版本 · ${d.tracks.length} 条支线 · 分叉图在下一步上线</span></h2>` +
    (html ? `<div class="pad"><div class="tree">${html}</div></div>` : '<div class="empty">还没有版本或支线的记录。</div>') + '</div>';
}

// ------------------------------------------------------------ 运行 (interim: the comparison table; master/detail comes next)
const SHOW_GROUPS = 6;
function tabRuns(d, r) {
  const key = x => String((x.run || {}).started || '') + ' ' + x.id;
  const runs = [...d.runs].sort((a, b) => key(b).localeCompare(key(a)));
  if (!runs.length) return '<div class="card"><div class="empty">还没有运行记录（runs/）。</div></div>';
  if (r.item != null) { const t = runs.find(x => runKey(x) === r.item); if (t && !ui.openRuns.has(t.dir)) { ui.openRuns.add(t.dir); revealGroup(t); } }
  const disp = B.parseDisplay((d.project || {}).display), notes = [...disp.problems];
  const main = runs.filter(x => (x.run || {}).kind !== 'data'), dataRuns = runs.filter(x => (x.run || {}).kind === 'data');
  const mainHtml = main.length ? runTable(d, 'main', main, disp, notes) : '<div class="empty">没有评测或训练的运行。</div>';
  return `<div class="card"><h2>运行 <span class="meta">${runs.length} 次 · 点一行展开配置、产物和曲线</span></h2>` +
    (notes.length ? `<div class="dnote">project.json 的 display 里有用不上的设置：${notes.map(esc).join('；')}。已按默认方式显示。</div>` : '') + mainHtml +
    (dataRuns.length ? `<h3 class="tt">数据 <span class="faint">kind 为 data 的运行：数据集的行数、标签分布等</span></h3>` +
      runTable(d, 'data', dataRuns, B.parseDisplay(null), []) : '') + '</div>';
}
function revealGroup(run) {  // make sure a run picked from elsewhere is visible in its table
  const k = (run.run || {}).kind === 'data' ? 'data' : 'main', st = ui.runView[k] || (ui.runView[k] = {all: false});
  const m = (run.run || {}).metrics;
  if (!B.hasNumber(B.groupValues(m, st.group ?? ''))) { const g = B.metricGroups([m]).find(x => x.runs); if (g) st.group = g.name; }
}
function runTable(d, key, runs, disp, notes) {
  const st = ui.runView[key] || (ui.runView[key] = {group: undefined, all: false});
  const groups = B.metricGroups(runs.map(x => (x.run || {}).metrics));
  const {ordered, defaultName, missingDefault} = B.arrangeGroups(groups, disp);
  if (missingDefault != null) notes.push(`default_group「${missingDefault}」不存在`);
  if (!groups.some(g => g.name === st.group)) st.group = defaultName;
  const g = groups.find(x => x.name === st.group);
  const vals = x => g ? B.groupValues((x.run || {}).metrics, g.name) : {};
  const shown = g ? runs.filter(x => B.hasNumber(vals(x))) : [];
  const bare = runs.filter(x => !groups.some(y => B.hasNumber(B.groupValues((x.run || {}).metrics, y.name))));
  const all = g ? g.cols : [], brief = g ? columnsFor(g, disp) : [], cols = st.all ? all : brief;
  const label = x => esc(x.name || '其他指标');
  const visible = ordered.slice(0, SHOW_GROUPS);
  if (g && !visible.includes(g)) visible.push(g);
  const more = ordered.filter(x => !visible.includes(x));
  const tabs = ordered.length > 1 ? `<div class="tabs"><span class="small">指标分组</span>` +
    visible.map(x => `<button class="tab${x === g ? ' on' : ''}" data-view="${key}" data-group="${esc(x.name)}">${label(x)} <span class="small">${x.runs}</span></button>`).join('') +
    (more.length ? `<select data-view="${key}"><option value="">更多分组（${more.length}）</option>` +
      more.map(x => `<option value="g:${esc(x.name)}">${label(x)}（${x.runs}）</option>`).join('') + '</select>' : '') + '</div>' : '';
  const missing = runs.length - shown.length - bare.length;
  const meta = g ? `<div class="tmeta">本组 ${shown.length} 次运行${missing ? `，另有 ${missing} 次没有这组指标` : ''}` +
    (brief.length < all.length ? ` · <a href="#" data-allcols="${key}">${st.all ? '只显示主要列' : `显示全部列（${all.length}）`}</a>` : '') + '</div>' : '';
  const row = x => {
    const rr = x.run || {}, v = vals(x), open = ui.openRuns.has(x.dir);
    return `<tr class="run${open ? ' open' : ''}" data-dir="${esc(x.dir)}" data-key="${esc(runKey(x))}">
      <td><b>${esc(runKey(x))}</b> <span class="small">${esc(rr.kind || '')}</span></td><td>${B.pill(rr.status)} ${B.livePill(x.liveness)}</td>
      <td class="small">${rr.attempt ? `<a href="${plink(d.name, 'experiments', rr.attempt)}">${esc(rr.attempt)}</a>` : ''}</td>
      <td class="small">${esc(rr.server || '')}${esc(gpusText(rr))}</td><td class="small" title="${esc(B.when(rr.started))}">${esc(B.day(rr.started))}</td>
      ${cols.map(c => `<td class="n">${cellHtml(v, c, disp.ratio)}</td>`).join('')}</tr>` +
      (open ? `<tr class="rdetail"><td colspan="${5 + cols.length}">${runDetail(d, x)}</td></tr>` : '');
  };
  const table = shown.length ? `<div class="scroll"><table class="runs"><tr><th>运行</th><th>状态</th><th>尝试</th><th>服务器</th><th>开始</th>` +
    cols.map(c => `<th class="n">${esc(c)}</th>`).join('') + `</tr>${shown.map(row).join('')}</table></div>` : '';
  const bareHtml = bare.length ? `<div class="bare"><span class="small">还没有指标的运行：</span>` +
    bare.map(x => `<a href="#" data-bare="${esc(x.dir)}" data-key="${esc(runKey(x))}" data-dir="${esc(x.dir)}">${esc(runKey(x))}</a>`).join('') + '</div>' +
    bare.filter(x => ui.openRuns.has(x.dir)).map(x => `<div class="pad" style="background:var(--bg)"><b>${esc(runKey(x))}</b> ${B.pill((x.run || {}).status)} ${runDetail(d, x)}</div>`).join('') : '';
  return tabs + meta + table + bareHtml;
}
function runDetail(d, x) {
  const rr = x.run || {}, s = x.series, parts = [];
  const facts = [['commit', rr.commit], ['续自', rr.resumed_from], ['tmux', rr.tmux], ['PID', rr.pid], ['开始', B.when(rr.started)], ['结束', B.when(rr.ended)], ['目录', x.dir]]
    .filter(([, v]) => v != null && v !== '');
  parts.push(`<div class="facts">${facts.map(([k, v]) => `<span><span class="small">${k}</span> ${esc(v)}</span>`).join('')}</div>`);
  if (s && s.keys && s.keys.length) parts.push(`<div class="charts">${s.keys.slice(0, 12).map(k => B.chart(s.points, k)).join('')}</div>` +
    `<div class="small">metrics.jsonl 共 ${s.total} 行${s.downsampled ? `，图上抽样显示 ${s.points.length} 个点` : ''}${s.bad_lines ? `，${s.bad_lines} 行无法解析` : ''}</div>`);
  if (x.images.length) parts.push(`<div class="imgs">${x.images.map(p => `<a href="${esc(fileUrl(d.name, p))}" target="_blank" rel="noopener">` +
    `<img loading="lazy" src="${esc(fileUrl(d.name, p))}" alt="${esc(p)}" title="${esc(p)}"></a>`).join('')}</div>`);
  const cols = [['配置 config', rr.config], ['产物 artifacts', rr.artifacts], ['全部指标 metrics', rr.metrics]].filter(([, v]) => v != null);
  if (cols.length) parts.push(`<div class="cols2" style="grid-template-columns:repeat(auto-fit,minmax(260px,1fr))">${cols.map(([k, v]) =>
    `<div><div class="small" style="font-weight:600;margin-bottom:2px">${k}</div>${B.kvTable(v)}</div>`).join('')}</div>`);
  if (x.samples_head.length) parts.push(`<div class="small">samples.jsonl 前 ${x.samples_head.length} 行</div>` +
    `<pre class="json">${esc(x.samples_head.map(o => JSON.stringify(o)).join('\n'))}</pre>`);
  return parts.join('');
}

// ------------------------------------------------------------ 样例 (interim: the worked example; the probe list comes next)
function tabExamples(d) {
  const w = d.example && d.example.walkthrough, probe = (d.example && d.example.probe_head) || [];
  let body = '<div class="empty">还没有样例（examples/walkthrough.md）。</div>';
  if (w) {
    const parts = B.splitFull(w.body), open = ui.exampleOpen;
    if (!w.body.trim()) body = '<div class="pad faint">文件是空的</div>';
    else if (parts) body = `<div class="md pad">${B.md(open ? w.body : parts.head)}</div><div class="more"><button data-example>${open ? '收起完整原文' : '展开完整原文'}</button>
        <span class="small">完整原文约 ${Math.max(1, Math.round(parts.full.length / 1000))} 千字</span></div>`;
    else body = `<div class="md pad${open ? '' : ' clip'}" id="exbody">${B.md(w.body)}</div><div class="more" id="exmore"><button data-example>${open ? '收起' : '展开全文'}</button></div>`;
  }
  return `<div class="card"><h2>一个例子${w ? ` <span class="meta">${esc(w.file)} · ${B.ago(w.mtime, d.now)}更新</span>` : ''}</h2>${body}</div>` +
    (probe.length ? `<div class="card"><h2>观察样例 <span class="meta">probe.jsonl 前 ${probe.length} 条 · 逐条对照的视图在下一步上线</span></h2>
      <pre class="json" style="margin:10px 14px">${esc(probe.map(x => JSON.stringify(x)).join('\n'))}</pre></div>` : '');
}
function fitExample() {
  const body = $('exbody'), more = $('exmore');
  if (!body || !more || ui.exampleOpen) return;
  if (body.scrollHeight <= body.clientHeight + 4) { body.classList.remove('clip'); more.hidden = true; }
}

// ------------------------------------------------------------ 文档: list on the left, the document on the right
function docItems(d) {
  const out = [];
  if (d.plan) out.push({id: 'plan', group: '方案', title: '整体方案', sub: d.plan.file, body: d.plan.body, mtime: d.plan.mtime});
  for (const v of d.versions) if (v.body.trim()) out.push({id: 'ver:' + (v.meta.id || v.file), group: '版本说明', title: `${v.meta.id || v.file}${v.meta.title ? ' · ' + v.meta.title : ''}`, sub: v.file, body: v.body, mtime: v.mtime});
  for (const t of d.tracks) if (t.body.trim()) out.push({id: 'trk:' + (t.meta.id || t.file), group: '支线说明', title: t.meta.title || t.meta.id || t.file, sub: t.file, body: t.body, mtime: t.mtime});
  return out;
}
function tabDocs(d, r) {
  const docs = docItems(d), name = d.name;
  if (!docs.length) return '<div class="card"><div class="empty">还没有文档（board/plan.md、版本和支线的正文）。</div></div>';
  const sel = docs.find(x => x.id === r.item) || docs[0];
  let last = null;
  const items = docs.map(x => {
    const head = x.group !== last ? `<div class="shead" style="padding:10px 12px 2px">${esc(x.group)}</div>` : '';
    last = x.group;
    return head + `<a class="li${x === sel ? ' on' : ''}" href="${plink(name, 'docs', x.id)}"><div class="row1"><span class="name">${esc(x.title)}</span></div>
      <div class="row2"><span>${esc(x.sub)}</span><span>${B.ago(x.mtime, d.now)}更新</span></div></a>`;
  }).join('');
  return `<div class="md2"><div class="list"><div class="items">${items}</div></div>
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
  const bare = t.closest('[data-bare]');
  if (bare) { e.preventDefault(); toggleRun(bare.dataset.bare); return; }
  const row = t.closest('tr.run');
  if (row && !t.closest('a')) toggleRun(row.dataset.dir);
});
function toggleRun(dir) { ui.openRuns.has(dir) ? ui.openRuns.delete(dir) : ui.openRuns.add(dir); render(); }
document.addEventListener('change', e => {
  const sel = e.target.closest('select[data-view]');
  if (sel && sel.value.startsWith('g:')) { ui.runView[sel.dataset.view].group = sel.value.slice(2); render(); return; }
  const tr = e.target.closest('select[data-qtrack]');
  if (tr) { const r = route(); location.hash = plink(r.name, 'experiments', null, {track: tr.value, todo: r.query.todo}); }
});
document.addEventListener('mousedown', e => { copying = !!e.target.closest('.copy'); });
window.addEventListener('hashchange', render);

// ============================================================ polling
async function poll() {
  const [st, pj] = await Promise.allSettled([getJSON('/api/status'), getJSON('/api/projects')]);
  S.offline = st.status === 'rejected' && pj.status === 'rejected';
  if (st.status === 'fulfilled') S.status = st.value;
  if (pj.status === 'fulfilled') S.projects = pj.value;
  const r = route();
  if (r.page === 'project') { renderSide(r); ensureDetail(r.name); document.title = titleFor(r); return; }  // don't disturb a project page
  if (r.page === 'gpu' && copying) { copying = false; renderSide(r); return; }
  render();
}
render();
poll();
setInterval(poll, 5000);
