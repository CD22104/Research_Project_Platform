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
