// Shared helpers for index.html and project.html. No dependencies.
// Everything lives under `Board` so page scripts can keep their own short names.
const Board = (() => {
  // Platform name shown in the top bar and page titles; the only place to rename it.
  const APP_NAME = 'CD的研究控制台';

  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));

  // ---- time
  const span = s => s < 90 ? Math.round(s) + ' 秒' : s < 5400 ? Math.round(s / 60) + ' 分钟'
    : s < 172800 ? Math.round(s / 3600) + ' 小时' : Math.round(s / 86400) + ' 天';
  const nowSec = () => Date.now() / 1000;
  // epoch seconds -> "N 分钟前"
  const ago = (t, now) => typeof t === 'number' ? span(Math.max(0, (now ?? nowSec()) - t)) + '前' : '—';
  // ISO 8601 string -> local "MM-DD HH:mm" (falls back to the raw text)
  function when(iso) {
    if (typeof iso !== 'string' || !iso) return '';
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    const p = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }
  const day = iso => when(iso).slice(0, 10);

  // ---- status labels (record values are Chinese; unknown values still render)
  // values not listed here (the format may add more) get the neutral 'other' style
  const TONE = {'进行中': 'run', '运行中': 'run', '成功': 'ok', '完成': 'ok', '并入主线': 'ok', '部分成功': 'part',
    '失败': 'bad', '被中断': 'bad', '放弃': 'off', '暂停': 'off', '已归档': 'off', '待确认': 'todo'};
  const tone = s => TONE[s] || 'other';
  const pill = s => s == null || s === '' ? '' : `<span class="pill t-${tone(s)}">${esc(s)}</span>`;
  const LIVE = {running: ['确认在跑', 'ok'], maybe_interrupted: ['可能已中断', 'bad'], unknown: ['无法确认是否在跑', 'other']};
  const livePill = l => LIVE[l] ? `<span class="pill t-${LIVE[l][1]}">${LIVE[l][0]}</span>` : '';

  // ---- numbers: tables show 4 decimals (data keeps full precision); integers as they are
  function num(v) {
    if (typeof v !== 'number' || !isFinite(v)) return v == null ? '' : esc(typeof v === 'object' ? JSON.stringify(v) : v);
    const a = Math.abs(v);
    if (Number.isInteger(v)) return a < 1e15 ? String(v) : v.toExponential(3);
    return a < 1e-4 || a >= 1e6 ? v.toExponential(2) : v.toFixed(4);
  }
  // compact form for chart axes
  function short(v) {
    if (typeof v !== 'number' || !isFinite(v)) return '';
    const a = Math.abs(v);
    return a !== 0 && (a >= 1e5 || a < 1e-3) ? v.toExponential(1) : String(+v.toPrecision(4));
  }

  // ---- minimal Markdown: headings, paragraphs, lists, quotes, code, bold, links, tables.
  // Every piece of source text goes through esc() before any tag is added.
  function inline(text) {
    const codes = [];
    let s = esc(text).replace(/`([^`\n]+)`/g, (_, c) => { codes.push(c); return '\u0000' + (codes.length - 1) + '\u0000'; });
    s = s.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, (m, label, url) =>
      /^https?:\/\//i.test(url) ? `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>` : label);
    return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[+i]}</code>`);
  }
  const FENCE = /^\s*```/, HEADING = /^(#{1,6})\s+(.*)$/, ITEM = /^\s*([-*+]|\d+[.)])\s+/, QUOTE = /^\s*>/;
  const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;  // ---, ***, ___ (checked before list items: "* * *")
  const TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;
  const cells = l => l.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim());
  function md(src) {
    const lines = String(src ?? '').replace(/\r\n?/g, '\n').split('\n');
    const isTable = i => /^\s*\|/.test(lines[i]) && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1]);
    const startsBlock = i => FENCE.test(lines[i]) || RULE.test(lines[i]) || HEADING.test(lines[i]) || ITEM.test(lines[i]) || QUOTE.test(lines[i]) || isTable(i);
    const out = [];
    let i = 0, m;
    while (i < lines.length) {
      const l = lines[i];
      if (FENCE.test(l)) {
        const buf = [];
        for (i++; i < lines.length && !FENCE.test(lines[i]); i++) buf.push(lines[i]);
        i++;
        out.push(`<pre><code>${esc(buf.join('\n'))}</code></pre>`);
      } else if (RULE.test(l)) {
        out.push('<hr>');
        i++;
      } else if ((m = HEADING.exec(l))) {
        const n = Math.min(6, m[1].length + 2);  // page owns h1/h2
        out.push(`<h${n}>${inline(m[2])}</h${n}>`);
        i++;
      } else if (isTable(i)) {
        const head = cells(l), rows = [];
        for (i += 2; i < lines.length && /^\s*\|/.test(lines[i]); i++) rows.push(cells(lines[i]));
        // a column whose filled cells all look numeric ("0.81", "-3", "12%", "361/602") is right-aligned
        const NUMERIC = /^[-+]?(\d[\d,]*(\.\d+)?|\.\d+)(%|\s*\/\s*\d[\d,]*)?(\s*\(\s*[-+]?(\d+(\.\d+)?|\.\d+)%?\s*\))?$/;  // also "588/602 (0.977)"
        const isNum = head.map((_, k) => rows.some(r => (r[k] || '').trim()) && rows.every(r => !(r[k] || '').trim() || NUMERIC.test(r[k].replace(/`/g, '').trim())));
        const td = (tag, c, k) => `<${tag}${isNum[k] ? ' class="num"' : ''}>${inline(c)}</${tag}>`;
        out.push(`<div class="scroll"><table class="mdt"><thead><tr>${head.map((c, k) => td('th', c, k)).join('')}</tr></thead><tbody>` +
          rows.map(r => `<tr>${head.map((_, k) => td('td', r[k] ?? '', k)).join('')}</tr>`).join('') + '</tbody></table></div>');
      } else if (ITEM.test(l)) {
        const ordered = /^\s*\d/.test(l), items = [];
        while (i < lines.length && ITEM.test(lines[i])) {
          let item = lines[i++].replace(ITEM, '');
          while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !ITEM.test(lines[i])) item += '\n' + lines[i++].trim();
          items.push(`<li>${inline(item)}</li>`);
        }
        const tag = ordered ? 'ol' : 'ul';
        out.push(`<${tag}>${items.join('')}</${tag}>`);
      } else if (QUOTE.test(l)) {
        const buf = [];
        while (i < lines.length && QUOTE.test(lines[i])) buf.push(lines[i++].replace(/^\s*>\s?/, ''));
        out.push(`<blockquote>${md(buf.join('\n'))}</blockquote>`);
      } else if (!l.trim()) {
        i++;
      } else {
        const buf = [lines[i++]];  // always consume one line, so the loop always advances
        while (i < lines.length && lines[i].trim() && !startsBlock(i)) buf.push(lines[i++]);
        out.push(`<p>${inline(buf.join('\n'))}</p>`);
      }
    }
    return out.join('\n');
  }

  // ---- walkthrough: "short version / --- / # 完整原文 / full text". Returns {head, full} or null.
  // Prefers a --- line whose next non-blank line is a 完整原文 heading; else the first such heading.
  function splitFull(body) {
    const lines = String(body ?? '').replace(/\r\n?/g, '\n').split('\n');
    const isFull = l => /^#{1,6}\s*完整原文\s*$/.test((l || '').trim());
    for (let i = 0; i < lines.length; i++) {
      if (!/^\s*-{3,}\s*$/.test(lines[i])) continue;
      let j = i + 1;
      while (j < lines.length && !lines[j].trim()) j++;
      if (isFull(lines[j])) return {head: lines.slice(0, i).join('\n'), full: lines.slice(i).join('\n')};
    }
    const k = lines.findIndex(isFull);
    return k > 0 ? {head: lines.slice(0, k).join('\n'), full: lines.slice(k).join('\n')} : null;
  }

  // first sentence of a section (e.g. 假设) for one-line summaries
  function firstSentence(body, title, max = 80) {
    const lines = String(body ?? '').split('\n');
    const at = lines.findIndex(l => new RegExp('^##\\s*' + title + '\\s*$').test(l.trim()));
    let text = '';
    if (at >= 0) {
      for (let i = at + 1; i < lines.length && !/^##\s/.test(lines[i]); i++) if (lines[i].trim()) { text = lines[i].trim(); break; }
    }
    text = text.replace(new RegExp('^' + title + '[:：]\\s*'), '');
    const m = /^(.*?[。！？!?；;])/.exec(text);
    if (m) text = m[1];
    return text.length > max ? text.slice(0, max) + '…' : text;
  }

  // ---- inline SVG line chart for one metric key of a metrics.jsonl series
  function chart(points, key, w = 520, h = 150) {
    const pts = (points || []).filter(p => typeof p[key] === 'number' && isFinite(p[key]) && typeof p.step === 'number');
    if (!pts.length) return '';
    const xs = pts.map(p => p.step), ys = pts.map(p => p[key]);
    let x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    if (x0 === x1) x1 = x0 + 1;
    if (y0 === y1) { const pad = Math.abs(y0) * 0.05 || 1; y0 -= pad; y1 += pad; }
    const L = 58, R = 12, T = 10, B = 22;
    const X = x => (L + (x - x0) / (x1 - x0) * (w - L - R)).toFixed(1);
    const Y = y => (T + (1 - (y - y0) / (y1 - y0)) * (h - T - B)).toFixed(1);
    const d = pts.map((p, k) => (k ? 'L' : 'M') + X(p.step) + ' ' + Y(p[key])).join('');
    const last = pts[pts.length - 1];
    return `<figure class="chart"><figcaption><b>${esc(key)}</b>
        <span class="small">最新 ${num(last[key])}（step ${num(last.step)}）· 最小 ${num(Math.min(...ys))} · 最大 ${num(Math.max(...ys))}</span></figcaption>
      <svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(key)}">
        <line class="ax" x1="${L}" y1="${h - B}" x2="${w - R}" y2="${h - B}"/>
        <line class="ax" x1="${L}" y1="${T}" x2="${L}" y2="${h - B}"/>
        <text x="${L - 6}" y="${T + 8}" text-anchor="end">${short(y1)}</text>
        <text x="${L - 6}" y="${h - B}" text-anchor="end">${short(y0)}</text>
        <text x="${L}" y="${h - 6}">${short(x0)}</text>
        <text x="${w - R}" y="${h - 6}" text-anchor="end">${short(x1)}</text>
        ${pts.length > 1 ? `<path class="ln" d="${d}"/>` : `<circle class="pt" cx="${X(last.step)}" cy="${Y(last[key])}" r="3"/>`}
      </svg></figure>`;
  }

  // ---- metrics: nested objects -> "测试集.acc"; grouped by first-level key
  function flatten(obj, prefix = '', out = {}) {
    if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return out;
    for (const [k, v] of Object.entries(obj)) {
      const key = prefix ? prefix + '.' + k : k;
      if (v && typeof v === 'object' && !Array.isArray(v)) flatten(v, key, out);
      else out[key] = v;
    }
    return out;
  }
  const isObj = v => v && typeof v === 'object' && !Array.isArray(v);
  // One run's values in group g, keyed by column name relative to the group.
  // Groups are the first-level keys of metrics whose value is an object; top-level scalars form group ''.
  function groupValues(metrics, g) {
    if (!isObj(metrics)) return {};
    if (g) return isObj(metrics[g]) ? flatten(metrics[g]) : {};
    const out = {};
    for (const [k, v] of Object.entries(metrics)) if (!isObj(v)) out[k] = v;
    return out;
  }
  const hasNumber = vals => Object.values(vals).some(v => typeof v === 'number' && isFinite(v));
  // -> [{name, cols: [relative column names], runs: how many metrics objects have a number in it}]
  function metricGroups(metricsList) {
    const groups = new Map();
    for (const m of metricsList) {
      if (!isObj(m)) continue;
      const names = new Set(Object.entries(m).map(([k, v]) => isObj(v) ? k : ''));
      for (const g of names) {
        const vals = groupValues(m, g);
        if (!groups.has(g)) groups.set(g, {name: g, cols: new Set(), runs: 0});
        const e = groups.get(g);
        Object.keys(vals).forEach(c => e.cols.add(c));
        if (hasNumber(vals)) e.runs++;
      }
    }
    return [...groups.values()].map(e => ({name: e.name, cols: [...e.cols], runs: e.runs}));
  }

  // ---- project.json "display": optional presentation preferences of a project.
  //   default_group: string; group_order: [string]; primary_metrics: [string];
  //   ratio: {column: [numerator column, denominator column]}. Column names are relative to the group.
  // Anything unusable is dropped and reported in .problems; the page then falls back to its defaults.
  function parseDisplay(raw) {
    const out = {defaultGroup: null, order: [], primary: [], ratio: {}, headline: null, problems: []};
    if (raw == null) return out;
    if (!isObj(raw)) { out.problems.push('display 应为对象'); return out; }
    const strList = v => Array.isArray(v) && v.every(x => typeof x === 'string');
    for (const [k, v] of Object.entries(raw)) {
      if (k === 'default_group') typeof v === 'string' ? out.defaultGroup = v : out.problems.push('default_group 应为字符串');
      else if (k === 'group_order') strList(v) ? out.order = v : out.problems.push('group_order 应为字符串列表');
      else if (k === 'primary_metrics') strList(v) ? out.primary = v : out.problems.push('primary_metrics 应为字符串列表');
      else if (k === 'ratio') {
        if (!isObj(v)) { out.problems.push('ratio 应为对象'); continue; }
        for (const [col, pair] of Object.entries(v)) {
          if (strList(pair) && pair.length === 2) out.ratio[col] = pair;
          else out.problems.push(`ratio.${col} 应为 [分子列, 分母列]`);
        }
      } else if (k === 'headline') {
        if (isObj(v) && typeof v.group === 'string' && typeof v.metric === 'string' && (v.better === 'higher' || v.better === 'lower'))
          out.headline = {group: v.group, metric: v.metric, better: v.better};
        else out.problems.push('headline 应为 {group, metric, better: "higher" 或 "lower"}');
      } else out.problems.push(`不认识的字段 ${k}`);
    }
    return out;
  }

  // Groups in display order (group_order first, then by run count) and the default selection.
  function arrangeGroups(groups, disp) {
    const order = (disp && disp.order) || [];
    const listed = order.map(n => groups.find(g => g.name === n)).filter(Boolean);
    const rest = groups.filter(g => !order.includes(g.name))
      .sort((a, b) => b.runs - a.runs || (a.name === '') - (b.name === '') || a.name.localeCompare(b.name));
    const ordered = [...listed, ...rest];
    const want = disp && disp.defaultGroup;
    const max = Math.max(0, ...groups.map(g => g.runs));
    const preferred = want != null && groups.some(g => g.name === want) ? want : null;
    const fallback = (ordered.find(g => g.runs === max) || ordered[0] || {name: null}).name;
    return {ordered, defaultName: preferred ?? fallback, missingDefault: want != null && preferred == null ? want : null};
  }

  // ---- which recorded runs sit on GPU g of server `server` (match by pid, else by listed GPUs)
  function runsOnGpu(runs, server, g) {
    const pids = new Set((g.procs || []).map(p => p.pid));
    return (runs || []).filter(r => r.server === server && (
      (r.pid != null && pids.has(Number(r.pid))) ||
      (Array.isArray(r.gpus) && r.gpus.map(Number).includes(g.index))));
  }

  // ---- horizontal bars: items [{label, value, html?, href?, dim?, on?}]. better: 'higher' | 'lower' | null.
  // Sorted best first when the direction is known (largest first otherwise); the best non-dim bar is marked
  // only when the direction is known. dim = not comparable (e.g. a spot check), drawn hatched.
  function bars(items, better = null) {
    const ok = (items || []).filter(x => typeof x.value === 'number' && isFinite(x.value));
    if (!ok.length) return '';
    const vals = ok.map(x => x.value), lo = Math.min(0, ...vals), hi = Math.max(0, ...vals), span = hi - lo || 1;
    const sorted = [...ok].sort((a, b) => better === 'lower' ? a.value - b.value : b.value - a.value);
    const fair = sorted.filter(x => !x.dim);
    const best = better && fair.length ? fair[0] : null;
    return '<div class="bars">' + sorted.map(x => {
      const left = ((Math.min(x.value, 0) - lo) / span * 100).toFixed(1), width = (Math.abs(x.value) / span * 100).toFixed(1);
      const cls = 'brow' + (x === best ? ' best' : '') + (x.dim ? ' dim' : '') + (x.on ? ' on' : '');
      const tag = x.href ? 'a' : 'div';
      return `<${tag} class="${cls}"${x.href ? ` href="${esc(x.href)}"` : ''} title="${esc(x.label)}${x.dim ? '（不参与比较）' : ''}">` +
        `<span class="bl">${esc(x.label)}</span><span class="bt"><i style="margin-left:${left}%;width:${width}%"></i></span>` +
        `<span class="bv">${x.html ?? num(x.value)}</span></${tag}>`;
    }).join('') + '</div>';
  }

  // ---- objects as a two-column key/value table (never a one-line dict). Nested keys are dotted.
  function kvTable(obj) {
    if (!isObj(obj)) return obj == null ? '' : `<span>${esc(typeof obj === 'object' ? JSON.stringify(obj) : obj)}</span>`;
    const flat = flatten(obj);
    const keys = Object.keys(flat);
    if (!keys.length) return '<span class="faint">（空）</span>';
    const val = v => typeof v === 'number' ? num(v) : Array.isArray(v) ? v.map(x => esc(typeof x === 'object' ? JSON.stringify(x) : x)).join('、')
      : v == null ? '<span class="faint">null</span>' : esc(v);
    return '<table class="kv">' + keys.map(k => `<tr><th>${esc(k)}</th><td${typeof flat[k] === 'number' ? ' class="num"' : ''}>${val(flat[k])}</td></tr>`).join('') + '</table>';
  }

  // ---- the run that is best on display.headline: {run, value, vals} or null.
  // Not eligible: kind data or smoke, and spot checks that scored only part of a set (n < of in that group).
  function bestRun(runs, headline) {
    if (!headline) return null;
    let best = null;
    for (const r of runs || []) {
      const x = r.run || {};
      if (x.kind === 'data' || x.kind === 'smoke') continue;
      const vals = groupValues(x.metrics, headline.group), v = vals[headline.metric];
      if (typeof v !== 'number' || !isFinite(v)) continue;
      if (typeof vals.n === 'number' && typeof vals.of === 'number' && vals.n < vals.of) continue;
      if (!best || (headline.better === 'higher' ? v > best.value : v < best.value)) best = {run: r, value: v, vals};
    }
    return best;
  }

  // ---- recent activity derived from the records: [{t (epoch s), text, tab, item}], newest first
  function events(d) {
    const out = [], ts = iso => { const t = Date.parse(iso); return isNaN(t) ? null : t / 1000; };
    const add = (t, text, tab, item = null, kind = tab) => { if (typeof t === 'number' && isFinite(t)) out.push({t, text, tab, item, kind}); };
    const idOf = rec => rec.meta && rec.meta.id || rec.file.split('/').pop().replace(/\.md$/, '');
    if (d.plan) add(d.plan.mtime, '整体方案有更新', 'docs', 'plan');
    for (const v of d.versions || []) add(ts(v.meta.date) ?? v.mtime, `定版 ${idOf(v)}${v.meta.title ? '：' + v.meta.title : ''}`, 'roadmap', idOf(v));
    for (const t of d.tracks || []) {
      const name = t.meta.title || idOf(t);
      add(ts(t.meta.started), `开启支线「${name}」`, 'experiments', null);
      if (t.meta.ended) add(ts(t.meta.ended), `支线「${name}」结束${t.meta.status ? '（' + t.meta.status + '）' : ''}`, 'roadmap', idOf(t));
    }
    for (const a of d.attempts || []) add(a.mtime, `尝试「${idOf(a)}」${a.meta.status ? '：' + a.meta.status : ''}`, 'experiments', idOf(a), 'attempt');
    for (const r of d.runs || []) {
      const x = r.run || {}, id = x.id || r.id;
      const where = x.server ? `（${x.server}${Array.isArray(x.gpus) && x.gpus.length ? ' 卡 ' + x.gpus.join(',') : ''}）` : '';
      const start = ts(x.started), end = ts(x.ended);
      if (start != null) add(start, `运行 ${id} 开始${where}`, 'runs', id);
      if (end != null) add(end, `运行 ${id} 结束${x.status ? '：' + x.status : ''}`, 'runs', id);
      if (start == null && end == null) add(r.mtime, `运行 ${id} 有更新${x.status ? '：' + x.status : ''}`, 'runs', id);
    }
    return out.sort((a, b) => b.t - a.t);
  }
  // Fold a burst of attempt-record updates (e.g. a batch rewrite) into one line: within `gap` seconds of each other.
  function foldEvents(evs, gap = 600) {
    const out = [];
    for (const e of evs) {
      const prev = out[out.length - 1];
      if (e.kind === 'attempt' && prev && (prev.kind === 'attempt' || prev.kind === 'attempts') && prev.tLast - e.t <= gap) {
        if (prev.kind === 'attempt') Object.assign(prev, {kind: 'attempts', n: 1, item: null});
        prev.n++; prev.tLast = e.t; prev.text = `${prev.n} 条尝试记录有更新`;
      } else out.push({...e, tLast: e.t});
    }
    return out;
  }

  // ---- setup.json as lookups: datasets / metrics / methods by id, and the method of each run
  function setupIndex(setup) {
    const list = k => Array.isArray(setup && setup[k]) ? setup[k].filter(x => isObj(x) && typeof x.id === 'string') : [];
    const byId = xs => new Map(xs.map(x => [x.id, x]));
    const methods = list('methods'), runMethod = new Map();
    for (const m of methods) for (const r of Array.isArray(m.runs) ? m.runs : []) if (!runMethod.has(String(r))) runMethod.set(String(r), m);
    return {goal: setup && typeof setup.goal === 'string' ? setup.goal : '', datasets: byId(list('datasets')), models: list('models'),
      metrics: byId(list('metrics')), methods, runMethod, present: !!setup};
  }

  // ---- the roadmap tree: versions (by date) > tracks hung on them > attempts (by date).
  // A track hangs under its from_version; without a valid one, under the version that merged it.
  function roadmapTree(d) {
    const key = x => String(x.meta.date || x.meta.started || x.file);
    const id = x => typeof x.meta.id === 'string' ? x.meta.id : null;
    const versions = [...(d.versions || [])].sort((a, b) => key(a).localeCompare(key(b)));
    const tracks = [...(d.tracks || [])].sort((a, b) => key(a).localeCompare(key(b)));
    const vids = new Set(versions.map(id).filter(Boolean)), tids = new Set(tracks.map(id).filter(Boolean));
    const mergedInto = new Map();
    for (const v of versions) for (const t of Array.isArray(v.meta.merged_tracks) ? v.meta.merged_tracks : []) if (id(v) && !mergedInto.has(t)) mergedInto.set(t, id(v));
    const home = t => vids.has(t.meta.from_version) ? t.meta.from_version : mergedInto.get(id(t)) || null;
    const attemptsOf = t => id(t) ? (d.attempts || []).filter(a => a.meta.track === id(t)).sort((a, b) => key(a).localeCompare(key(b))) : [];
    const node = t => ({track: t, merged: mergedInto.get(id(t)) || null, attempts: attemptsOf(t)});
    return {
      versions: versions.map(v => ({version: v, tracks: tracks.filter(t => id(v) && home(t) === id(v)).map(node)})),
      loose: tracks.filter(t => !home(t)).map(node),
      orphans: (d.attempts || []).filter(a => !tids.has(a.meta.track)).sort((a, b) => key(a).localeCompare(key(b))),
    };
  }

  const projectUrl = (name, tab = 'overview', item = null) => '#/p/' + encodeURIComponent(name) + '/' + tab + (item != null ? '/' + encodeURIComponent(item) : '');

  // ---- GPUs: a card is free when no process is on it and almost no memory is used
  const FREE_MB = 500;
  const isFree = g => !(g.procs || []).length && (g.mem_used ?? 0) < FREE_MB;
  // free cards on servers whose latest poll succeeded (/api/status)
  const freeCount = status => (status && status.servers || [])
    .filter(s => s.status === 'ok').reduce((n, s) => n + (s.gpus || []).filter(isFree).length, 0);

  // display order of servers: h200, h100, a100, then the 6k machines (the user rarely uses those)
  const serverRank = name => { const n = String(name); const i = ['h200', 'h100', 'a100'].findIndex(k => n.includes(k)); return i < 0 ? 3 : i; };
  const sortServers = list => [...(list || [])].sort((a, b) => serverRank(a.name) - serverRank(b.name) || String(a.name).localeCompare(String(b.name)));

  return {APP_NAME, FREE_MB, isFree, freeCount, serverRank, sortServers, esc, span, ago, when, day, tone, pill, livePill, num, short, inline, md, splitFull, firstSentence, chart,
    flatten, groupValues, hasNumber, metricGroups, parseDisplay, arrangeGroups, runsOnGpu, projectUrl, kvTable, bestRun, events, foldEvents, bars, setupIndex, roadmapTree};
})();
