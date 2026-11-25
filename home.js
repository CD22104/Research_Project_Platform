'use strict';
// The project's front page (首页): the session's latest brief on top, then a map of the project drawn from the
// records (question → the method's steps → the parts of the plan → each experiment in one line), then what waits
// for the user. Everything is derived from the fetched project; nothing here writes.

// An attempt's name: its title (a short name people can read), else its id without the date.
function attTitle(a) {
  const t = a && a.meta && typeof a.meta.title === 'string' ? a.meta.title.trim() : '';
  return t || String(a.meta.id || a.file.split('/').pop().replace(/\.md$/, '')).replace(/^\d{4}-\d{2}-\d{2}-/, '');
}
const CONCLUDED = ['成功', '部分成功', '失败', '放弃'];   // a conclusion the user confirmed
// Method steps that may be out of date: an attempt testing the step (method: [step]) reached a conclusion and its
// file changed after the step's file did. A file time is a proxy (other edits to the attempt count too).
// -> Map(step id -> [attempts])
function methodStale(d) {
  if (d._stale) return d._stale;
  const out = new Map(), list = x => Array.isArray(x) ? x : x == null ? [] : [x];
  for (const x of d.method || []) {
    const id = x.meta && x.meta.id;
    if (typeof id !== 'string') continue;
    const late = (d.attempts || []).filter(a => list(a.meta.method).includes(id) && CONCLUDED.includes(a.meta.status)
      && typeof a.mtime === 'number' && typeof x.mtime === 'number' && a.mtime > x.mtime + 1);
    if (late.length) out.set(id, late);
  }
  return d._stale = out;
}

// The newest brief (docs of kind 简报), by its front-matter date, else by when the file changed. -> doc item | null
function latestBrief(d) {
  const at = x => { const v = (x.meta || {}).date, t = typeof v === 'string' ? Date.parse(v.length === 10 ? v + 'T00:00:00Z' : v) : NaN;
    return isNaN(t) ? x.mtime : t / 1000; };
  const briefs = (d.docs || []).filter(x => (x.meta || {}).kind === '简报');
  if (!briefs.length) return null;
  const best = briefs.reduce((a, b) => at(b) > at(a) || (at(b) === at(a) && b.mtime > a.mtime) ? b : a);
  return Object.assign({}, docItems(d).find(x => x.file === best.file), {at: at(best)});
}

const OPEN_TRACK = s => s == null || s === '进行中' || s === '未写';
// The project as a map: the question, the method's top-level steps (how many experiments test each, which may be
// out of date), then the parts of the plan (tracks: open ones first, then closed ones by start; attempts without a
// track under 其他), each experiment in one row.
function projectMap(d) {
  const c = ctx(d), M = methodIndex(d), stale = methodStale(d), since = lastSeenOf(d.name), p = d.project || {};
  const list = x => Array.isArray(x) ? x : x == null ? [] : [x];
  const runsOf = new Map();
  for (const r of d.runs) { const a = c.attemptOf(r); if (a) { if (!runsOf.has(a)) runsOf.set(a, []); runsOf.get(a).push(r); } }
  // top-level steps in the method's own order (next arrows), each counting its variants and sub-steps
  const top = M.steps.filter(x => !x.meta.parent && !x.meta.variant_of && !M.byId.get(x.meta.parent));
  const under = x => [x, ...M.variants(x), ...M.steps.filter(s => M.path(s).includes(x) && s !== x)];
  const ordered = M.level('').filter(x => top.includes(x));   // the method page's own order
  const steps = (ordered.length ? ordered : top).map(x => {
    const all = under(x), ids = new Set(all.map(s => s.meta.id));
    return {step: x, n: d.attempts.filter(a => list(a.meta.method).some(m => ids.has(m))).length, stale: all.some(s => stale.has(s.meta.id))};
  });
  const row = a => {
    const op = a.onepager || {}, runs = runsOf.get(a) || [], key = attKey(a);
    const groups = new Set(runs.flatMap(r => B.metricGroups([(r.run || {}).metrics]).filter(g => g.runs).map(g => g.name)));
    return {a, key, title: attName(a), hasTitle: typeof a.meta.title === 'string' && !!a.meta.title.trim(),
      ask: (op['想回答'] || {}).text || '', so: (op['所以'] || {}).text || gistOf(d, a) || '', status: a.meta.status || '',
      method: list(a.meta.method).map(String), datasets: [...groups], docs: docsAbout(d, 'a:' + key).length, runs: runs.length,
      isNew: since != null && typeof a.mtime === 'number' && a.mtime > since};
  };
  const byDate = (x, y) => dateKey(x).localeCompare(dateKey(y));
  const tracks = [...d.tracks].sort((x, y) => (OPEN_TRACK(y.meta.status) - OPEN_TRACK(x.meta.status)) || String(x.meta.started || '').localeCompare(String(y.meta.started || '')));
  const parts = tracks.map(t => ({track: t, title: t.meta.title || idOf(t), status: statusOf(t), closed: !OPEN_TRACK(t.meta.status),
    about: B.firstSentence('## 假设\n' + ((t.sections || sectionsOf(t.body).list.reduce((o, [k, v]) => (o[k] = v, o), {}))['假设'] || ''), '假设'),
    rows: d.attempts.filter(a => a.meta.track === t.meta.id).sort(byDate).map(row)}));
  const loose = d.attempts.filter(a => !c.trackById.get(a.meta.track) && Object.keys(a.meta).length).sort(byDate).map(row);
  if (loose.length) parts.push({track: null, title: '其他', status: '', closed: false, about: '没有挂到支线上的实验', rows: loose});
  const v = c.verById.get(p.current_version);
  return {question: p.question || '', version: v ? `${p.current_version}${v.meta.title ? ' · ' + v.meta.title : ''}` : p.current_version || '',
    steps, parts: parts.filter(x => x.rows.length || !x.closed), since};   // a closed part with no experiments is left out
}

function briefCard(d) {
  const b = latestBrief(d), name = d.name;
  if (!b) return `<div class="card brief none"><div class="pad small faint">还没有简报。一批工作告一段落时，项目会话写一篇 docs/&lt;日期&gt;-brief.md（kind: 简报）：现在到哪了、新做了什么、先看这几个、等你决定的。它会显示在这里。</div></div>`;
  const later = [...d.attempts, ...(d.docs || []).filter(x => x.file !== b.file)].filter(x => typeof x.mtime === 'number' && x.mtime > b.at + 60).length;
  const read = b.state === 'seen' || b.state === 'ok', folded = read || ui.briefFolded === b.file;
  return `<details class="card brief" id="home-brief"${folded ? '' : ' open'}><summary><h2>给你的简报 <span class="meta">${esc(B.day(new Date(b.at * 1000).toISOString()))} · ${esc(b.title)}${read ? ' · 你看过了' : ''}</span></h2></summary>
    ${later ? `<div class="notice">简报之后又有 ${later} 处变化（地图上标「新」的是你上次来之后的）。</div>` : ''}
    <div class="pad md">${refs(B.md(b.body), d)}</div>
    <div class="pad marks">${read ? '' : `<button class="mk" data-mark="看过" data-file="${esc(b.file)}">看过了</button>`}
      <a class="small" href="${plink(name, 'docs', b.id)}">在文档页打开 →</a>${(d.docs || []).filter(x => (x.meta || {}).kind === '简报').length > 1 ? ` <a class="small" href="${plink(name, 'docs', null, {k: '简报'})}">以前的简报 →</a>` : ''}</div></details>`;
}

function mapCard(d) {
  const m = projectMap(d), name = d.name, c = ctx(d), M = methodIndex(d);
  const stepName = id => { const x = M.byId.get(id); return x ? M.title(x) : id; };
  const strip = m.steps.length ? `<div class="mstrip"><span class="small">方法</span>${m.steps.map(s => `<a class="chip${s.stale ? ' stale' : ''}" href="${plink(name, 'method', s.step.meta.id)}"` +
    ` title="${s.stale ? '可能过时：写完之后又有实验下了结论' : ''}">${esc(M.title(s.step))} <span class="small">${s.n}</span>${s.stale ? ' <span class="dot" aria-label="可能过时">●</span>' : ''}</a>`).join('<span class="arrow">→</span>')}</div>`
    : `<div class="mstrip small faint">还没有方法（board/method/）：把方法拆成几步写下来，实验用 method 字段挂到它检验的那一步。</div>`;
  const rowHtml = x => `<div class="xrow${x.isNew ? ' new' : ''}">
      <div class="m1">${B.pill(x.status)} <a href="${plink(name, 'flow', 'a:' + x.key)}"><b>${esc(x.title)}</b></a>${x.hasTitle ? '' : ' <span class="small faint">（还没写标题）</span>'}
        ${x.isNew ? '<span class="pill t-run">新</span>' : ''}${x.method.map(id => `<a class="tag2" href="${plink(name, 'method', id)}">${esc(stepName(id))}</a>`).join('')}</div>
      ${x.ask ? `<div class="m2"><span class="k">想回答</span>${B.inline(x.ask)}</div>` : ''}
      ${x.so ? `<div class="m2"><span class="k">所以</span>${B.inline(x.so)}</div>` : ''}
      <div class="m4 small">${x.datasets.length ? '数据：' + x.datasets.map(g => `<a href="${plink(name, 'setup', 'ds:' + g)}">${esc(c.groupName(g))}</a>`).join('、') + ' · ' : ''}` +
      `${x.docs ? `<a href="${plink(name, 'flow', 'a:' + x.key)}">文档 ${x.docs}</a> · ` : ''}${x.runs ? `<a href="${plink(name, 'runs', null, {att: x.key})}">运行 ${x.runs} →</a>` : '没有运行'}</div></div>`;
  const part = p => {
    const head = `<span class="ptitle">${p.track ? `<a href="${plink(name, 'flow', 't:' + idOf(p.track))}">${esc(p.title)}</a>` : esc(p.title)}</span> ${p.status ? B.pill(p.status) : ''}
      <span class="small">${p.rows.length} 个实验${p.rows.some(x => x.isNew) ? ` · ${p.rows.filter(x => x.isNew).length} 个新` : ''}</span>`;
    const body = `${p.about ? `<div class="pabout small">${esc(p.about)}</div>` : ''}${p.rows.map(rowHtml).join('') || '<div class="small faint">还没有实验</div>'}`;
    return p.closed ? `<details class="mpart" id="mpart-${esc(p.track ? idOf(p.track) : 'other')}"><summary>${head}</summary>${body}</details>`
      : `<div class="mpart"><div class="phd">${head}</div>${body}</div>`;
  };
  return `<div class="card map"><h2>项目地图 <span class="meta">从记录自动生成：方法的几步，方案分几块，每块的实验</span></h2><div class="pad">
    ${m.question ? `<div class="mq"><span class="k">研究问题</span>${esc(m.question)}</div>` : ''}${m.version ? `<div class="mq small"><span class="k">当前方案</span>${esc(m.version)}</div>` : ''}
    ${strip}${m.parts.map(part).join('')}</div></div>`;
}

// what waits for the user, in a fixed order: blocking to-dos, conclusions to confirm (in the map's order),
// decisions, then the rest
function homeWaiting(d) {
  const p = d.project || {}, name = d.name, answers = d.answers || {}, proj = {name, title: p.title};
  const todos = openTodos(d.todos).map(t => ({...t, answer: answers[String(t.id)] || null}));
  const order = new Map(projectMap(d).parts.flatMap(x => x.rows).map((x, i) => [x.a, i]));
  const pending = d.attempts.filter(a => pendingOf(a).includes('结论待确认') && typeof a.meta.id === 'string')
    .sort((x, y) => (order.get(x) ?? 1e9) - (order.get(y) ?? 1e9));
  const cands = work(d).decisions.filter(r => r.meta.status === '候选' || r.meta.status == null);
  return [
    ...todos.filter(t => t.kind === 'blocking').map(t => todoItem(t, proj, d.now)),
    ...pending.map(a => confirmItem(proj, {id: a.meta.id, title: attName(a), proposed: a.meta.proposed,
      conclusion: ((a.sections || {})['结论和原因'] || '').slice(0, 600), answer: answers['a:' + a.meta.id] || null}, d.now)),
    ...todos.filter(t => t.kind === 'decision').map(t => todoItem(t, proj, d.now)),
    ...cands.map(r => decisionItem(proj, {id: r.meta.id, title: r.meta.title || r.meta.id, decision: decLine(r), answer: answers['d:' + r.meta.id] || null, state: r.state}, d.now)),
    ...todos.filter(t => t.kind !== 'blocking' && t.kind !== 'decision').map(t => todoItem(t, proj, d.now))];
}

function tabHome(d) {
  const name = d.name, W = work(d), since = prevSeen(name, d.now);
  const fresh = since == null ? 0 : timelineOf(d).filter(e => e.t > since).length;
  const wait = homeWaiting(d);
  const open = W.tasks.filter(t => TASK_COLS.includes(t.meta.status)), running = d.runs.filter(x => (x.run || {}).status === '运行中');
  const going = `<div class="card"><h2>正在进行 <span class="meta">任务 ${open.length} · 运行 ${running.length}</span></h2><div class="pad">
    ${open.length ? `<ul class="hl">${open.map(t => `<li>${taskPill(t.meta.status)} <a href="${plink(name, 'tasks', t.meta.id)}">${esc(taskTitle(t))}</a>` +
      `${handoffNext(t) ? `<div class="small">下一步：${esc(handoffNext(t))}</div>` : ''}</li>`).join('')}</ul>` : '<div class="small faint">没有进行中的任务卡。</div>'}
    ${running.length ? `<ul class="hl">${running.map(x => `<li>${statePill(x.state)} <a href="${plink(name, 'runs', runKey(x))}">${esc(ctx(d).label(x))}</a></li>`).join('')}</ul>` : ''}
    <div class="small"><a href="${plink(name, 'timeline')}">${since == null ? '时间线：发生过的事 →' : `你上次来（${B.ago(since, d.now)}）之后有 ${fresh} 处变化 → 时间线`}</a></div></div></div>`;
  const waiting = `<div class="card"><h2>等你 <span class="meta">${wait.length} 条 · 卡着的在前，然后是要确认的结论、要做的决定</span></h2>${wait.length ? `<ul class="tlist">${wait.join('')}</ul>` : '<div class="empty">没有等你的事。</div>'}</div>`;
  return briefCard(d) + mapCard(d) + `<div class="homegrid"><div>${waiting}</div><div>${going}</div></div>`;
}
