// Shared helpers for index.html and project.html. No dependencies.
// Everything lives under `Board` so page scripts can keep their own short names.
const Board = (() => {
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
  const TONE = {'进行中': 'run', '运行中': 'run', '成功': 'ok', '完成': 'ok', '并入主线': 'ok',
    '失败': 'bad', '被中断': 'bad', '放弃': 'off', '暂停': 'off', '已归档': 'off', '待确认': 'todo'};
  const pill = s => s == null || s === '' ? '' : `<span class="pill t-${TONE[s] || 'other'}">${esc(s)}</span>`;
  const LIVE = {running: ['确认在跑', 'ok'], maybe_interrupted: ['可能已中断', 'bad'], unknown: ['无法确认是否在跑', 'other']};
  const livePill = l => LIVE[l] ? `<span class="pill t-${LIVE[l][1]}">${LIVE[l][0]}</span>` : '';

  // ---- numbers
  function num(v) {
    if (typeof v !== 'number' || !isFinite(v)) return v == null ? '' : esc(typeof v === 'object' ? JSON.stringify(v) : v);
    const a = Math.abs(v);
    if (Number.isInteger(v) && a < 1e7) return String(v);
    return a !== 0 && (a >= 1e5 || a < 1e-3) ? v.toExponential(2) : String(+v.toPrecision(4));
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
  const TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;
  const cells = l => l.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim());
  function md(src) {
    const lines = String(src ?? '').replace(/\r\n?/g, '\n').split('\n');
    const isTable = i => /^\s*\|/.test(lines[i]) && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1]);
    const startsBlock = i => FENCE.test(lines[i]) || HEADING.test(lines[i]) || ITEM.test(lines[i]) || QUOTE.test(lines[i]) || isTable(i);
    const out = [];
    let i = 0, m;
    while (i < lines.length) {
      const l = lines[i];
      if (FENCE.test(l)) {
        const buf = [];
        for (i++; i < lines.length && !FENCE.test(lines[i]); i++) buf.push(lines[i]);
        i++;
        out.push(`<pre><code>${esc(buf.join('\n'))}</code></pre>`);
      } else if ((m = HEADING.exec(l))) {
        const n = Math.min(6, m[1].length + 2);  // page owns h1/h2
        out.push(`<h${n}>${inline(m[2])}</h${n}>`);
        i++;
      } else if (isTable(i)) {
        const head = cells(l), rows = [];
        for (i += 2; i < lines.length && /^\s*\|/.test(lines[i]); i++) rows.push(cells(lines[i]));
        out.push(`<div class="scroll"><table><tr>${head.map(c => `<th>${inline(c)}</th>`).join('')}</tr>` +
          rows.map(r => `<tr>${r.map(c => `<td>${inline(c)}</td>`).join('')}</tr>`).join('') + '</table></div>');
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
        <text x="${L - 6}" y="${T + 8}" text-anchor="end">${num(y1)}</text>
        <text x="${L - 6}" y="${h - B}" text-anchor="end">${num(y0)}</text>
        <text x="${L}" y="${h - 6}">${num(x0)}</text>
        <text x="${w - R}" y="${h - 6}" text-anchor="end">${num(x1)}</text>
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
  // -> [{name, cols: [full keys]}]; top-level scalars form the group named ''
  function metricGroups(metricsList) {
    const groups = new Map();
    for (const m of metricsList) {
      if (!m || typeof m !== 'object' || Array.isArray(m)) continue;
      for (const [k, v] of Object.entries(m)) {
        const g = v && typeof v === 'object' && !Array.isArray(v) ? k : '';
        if (!groups.has(g)) groups.set(g, new Set());
        for (const col of Object.keys(g ? flatten(v, k) : {[k]: v})) groups.get(g).add(col);
      }
    }
    const out = [...groups].map(([name, cols]) => ({name, cols: [...cols]}));
    return out.sort((a, b) => (a.name === '') - (b.name === ''));  // named groups first
  }

  // ---- which recorded runs sit on GPU g of server `server` (match by pid, else by listed GPUs)
  function runsOnGpu(runs, server, g) {
    const pids = new Set((g.procs || []).map(p => p.pid));
    return (runs || []).filter(r => r.server === server && (
      (r.pid != null && pids.has(Number(r.pid))) ||
      (Array.isArray(r.gpus) && r.gpus.map(Number).includes(g.index))));
  }

  const projectUrl = name => 'project.html?p=' + encodeURIComponent(name);

  return {esc, span, ago, when, day, pill, livePill, num, inline, md, chart, flatten, metricGroups, runsOnGpu, projectUrl};
})();
