"""Project boards: parse each project's board/ directory on the shared disk.

Read-only. Understands record format v0 (see a project's board/FORMAT.md) and is
deliberately lenient: missing fields, extra fields and missing files must never
raise; callers get empty values instead.
"""
import json
import re

MAX_BODY = 200 * 1024  # bytes of a Markdown file kept in the cache
HEAD_ROWS = 20         # rows kept from samples.jsonl / probe.jsonl
MAX_POINTS = 1000      # points kept per metrics.jsonl after downsampling

_COMMENT = re.compile(r"\s+#.*$")  # " # ..." ends a front-matter line; "C#" is kept


def _read_bytes(path, limit=None):
    """Read a file (at most limit bytes). Single choke point for disk reads."""
    with open(path, "rb") as f:
        return f.read() if limit is None else f.read(limit)


def read_text(path, limit=MAX_BODY):
    """Return (text, truncated). Invalid UTF-8 is replaced, never raised."""
    data = _read_bytes(path, limit + 1)
    return data[:limit].decode("utf-8", "replace"), len(data) > limit


def parse_value(raw):
    """Front-matter value: 'null' or empty -> None, '[a, b]' -> list, else text."""
    v = raw.strip()
    if v in ("", "null"):
        return None
    if v.startswith("[") and v.endswith("]"):
        return [x.strip() for x in v[1:-1].split(",") if x.strip()]
    return v


def parse_front_matter(text):
    """Split '---' / 'key: value' lines / '---' from the body.

    Without an opening '---', or without a closing one, the whole text is body.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text
    for end in range(1, len(lines)):
        if lines[end].strip() == "---":
            break
    else:
        return {}, text
    meta = {}
    for line in lines[1:end]:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        key, sep, val = _COMMENT.sub("", s).partition(":")
        if sep and key.strip():
            meta[key.strip()] = parse_value(val)
    return meta, "\n".join(lines[end + 1:])


def split_sections(body):
    """Map each '## title' to the text under it (fenced code blocks respected)."""
    sections, title, buf, fenced = {}, None, [], False
    for line in body.split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        if not fenced and line.startswith("## "):
            if title is not None:
                sections[title] = "\n".join(buf).strip()
            title, buf = line[3:].strip(), []
        elif title is not None:
            buf.append(line)
    if title is not None:
        sections[title] = "\n".join(buf).strip()
    return sections


DONE = ("成功", "失败", "放弃", "待确认")


def needs_input(meta, sections):
    """Labels of what the user still has to fill in for an attempt."""
    out = []
    if not sections.get("预期", "").strip():
        out.append("预期")
    if meta.get("status") in DONE and not sections.get("结论和原因", "").strip():
        out.append("结论和原因")
    if meta.get("status") == "待确认":
        out.append("结论待确认")
    return out


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _complete_lines(path):
    """Yield complete lines (bytes); a last line without newline is still being written."""
    with open(path, "rb") as f:
        for line in f:
            if line.endswith(b"\n"):
                yield line


def read_metrics(path, max_points=MAX_POINTS):
    """Parse metrics.jsonl into a bounded, downsampled series.

    Returns {keys, points, total, bad_lines, downsampled}; points are dicts with
    'step' and numeric values only. Memory stays bounded for any file size.
    """
    points, keys, total, bad, stride, seen, last = [], {}, 0, 0, 1, 0, None
    for line in _complete_lines(path):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if not isinstance(row, dict):
            bad += 1
            continue
        p = {k: v for k, v in row.items() if k != "step" and _is_num(v)}
        if not p:
            continue
        for k in p:
            keys.setdefault(k, None)
        p["step"] = row["step"] if _is_num(row.get("step")) else total
        total += 1
        last = p
        seen += 1
        if seen >= stride:  # keep every stride-th point while reading
            seen = 0
            points.append(p)
            if len(points) >= 4 * max_points:
                points, stride = points[::2], stride * 2
    if last is not None and (not points or points[-1] is not last):
        points.append(last)  # always keep the latest point
    downsampled = len(points) < total
    if len(points) > max_points:
        n = len(points)
        idx = sorted({round(i * (n - 1) / (max_points - 1)) for i in range(max_points)})
        points, downsampled = [points[i] for i in idx], True
    return {"keys": list(keys), "points": points, "total": total,
            "bad_lines": bad, "downsampled": downsampled}


def read_jsonl_head(path, n=HEAD_ROWS):
    """First n complete rows of a .jsonl file; unparsable rows come back as {'_raw': text}."""
    rows = []
    for line in _complete_lines(path):
        if len(rows) >= n:
            break
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            rows.append({"_raw": line.decode("utf-8", "replace").rstrip("\n")})
    return rows
