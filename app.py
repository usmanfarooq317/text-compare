"""
Text Compare
------------
Side-by-side text comparison with automatic format detection.

Detects:  JSON, SOAP (1.1 / 1.2), XML, HTML, YAML, Form data, Plain text
Cleans:   escaped quotes (\\"), escaped new lines (\\n), stray wrapping quotes
Formats:  JSON (indented), XML / SOAP (indented, including XML inside CDATA)

Run:
    pip install -r requirements.txt
    python app.py
Then open http://127.0.0.1:5000
"""

import difflib
import json
import re
from urllib.parse import parse_qsl
from xml.dom import minidom
from xml.parsers.expat import ExpatError

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024  # 4 MB per request

SOAP_NAMESPACES = {
    "http://schemas.xmlsoap.org/soap/envelope/": "SOAP 1.1",
    "http://www.w3.org/2003/05/soap-envelope": "SOAP 1.2",
}
XML_DECL_RE = re.compile(r"^\s*(<\?xml[^>]*\?>)", re.IGNORECASE)
INDENT = "  "


# =====================================================================
#  Cleaning: undo escaping that comes from logs / Java / JSON strings
# =====================================================================
def clean_text(text: str):
    """Return (cleaned_text, notes). Only changes text that looks escaped."""
    notes = []
    t = text.strip()

    # Whole text wrapped in quotes: "...."
    if len(t) >= 2 and t[0] == t[-1] == '"' and ('\\"' in t or "\\n" in t):
        t = t[1:-1]
        notes.append("Removed wrapping quotes")

    if '\\"' in t:
        t = t.replace('\\"', '"')
        notes.append('Unescaped \\" to "')
    if "\\n" in t and "\n" not in t:
        t = t.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t")
        notes.append("Converted \\n to real line breaks")

    # A stray quote left at the start or end (e.g. copied from a Java string)
    if t.endswith('"') and not t.startswith('"') and t[:-1].rstrip().endswith(">"):
        t = t[:-1].rstrip()
        notes.append("Removed a stray quote at the end")
    if t.startswith('"') and not t.endswith('"') and t[1:].lstrip().startswith("<"):
        t = t[1:].lstrip()
        notes.append("Removed a stray quote at the start")

    return t, notes


# =====================================================================
#  Detection
# =====================================================================
def _try_json(t):
    try:
        return json.loads(t)
    except (ValueError, TypeError):
        return None


def _try_xml(t):
    try:
        return minidom.parseString(t.encode("utf-8"))
    except (ExpatError, ValueError):
        return None


def _looks_like_yaml(t):
    lines = [l for l in t.splitlines() if l.strip() and not l.strip().startswith("#")]
    if len(lines) < 2:
        return False
    hits = sum(1 for l in lines if re.match(r"^\s*(-\s+)?[\w\"'.-]+\s*:(\s|$)", l) or re.match(r"^\s*-\s+\S", l))
    return hits / len(lines) >= 0.8 and not t.lstrip().startswith(("{", "[", "<"))


def _looks_like_form(t):
    if "\n" in t.strip() or "=" not in t or " " in t.strip():
        return False
    pairs = parse_qsl(t, keep_blank_values=True)
    return len(pairs) >= 2 or (len(pairs) == 1 and "&" not in t and t.count("=") == 1 and len(t) < 500)


def detect(raw: str):
    """Return dict: format, label, cleaned, notes, valid, error."""
    if not raw or not raw.strip():
        return {"format": "empty", "label": "Empty", "cleaned": "", "notes": [], "valid": True, "error": None}

    cleaned, notes = clean_text(raw)
    t = cleaned

    # JSON
    if t[:1] in "{[":
        if _try_json(t) is not None:
            return _result("json", "JSON", t, notes)
        return _result("json", "JSON", t, notes, valid=False, error=_json_error(t))

    # XML family
    if t.startswith("<"):
        low = t[:600].lower()
        if low.startswith(("<!doctype html", "<html")) or (
                not low.startswith("<?xml") and re.search(r"<(head|body|div|span|p|table)[\s>]", low)):
            return _result("html", "HTML", t, notes)
        doc = _try_xml(t)
        if doc is None:
            kind, label = ("soap", "SOAP") if re.search(r"<\w*:?envelope[\s>]", low) else ("xml", "XML")
            return _result(kind, label, t, notes, valid=False, error=_xml_error(t))
        root = doc.documentElement
        if root.localName == "Envelope" and root.namespaceURI in SOAP_NAMESPACES:
            return _result("soap", SOAP_NAMESPACES[root.namespaceURI], t, notes)
        if root.localName == "Envelope":
            return _result("soap", "SOAP", t, notes)
        return _result("xml", "XML", t, notes)

    if _looks_like_yaml(t):
        return _result("yaml", "YAML", t, notes)
    if _looks_like_form(t):
        return _result("form", "Form data", t, notes)
    return _result("text", "Plain text", t, notes)


def _result(fmt, label, cleaned, notes, valid=True, error=None):
    return {"format": fmt, "label": label, "cleaned": cleaned, "notes": notes, "valid": valid, "error": error}


def _json_error(t):
    try:
        json.loads(t)
    except ValueError as e:
        return f"Invalid JSON: {e}"
    return None


def _xml_error(t):
    try:
        minidom.parseString(t.encode("utf-8"))
    except ExpatError as e:
        return f"Invalid XML: {e}"
    return None


# =====================================================================
#  Pretty formatting
# =====================================================================
def format_json(t, sort_keys=False):
    return json.dumps(json.loads(t), indent=2, ensure_ascii=False, sort_keys=sort_keys)


def format_xml(t):
    decl = XML_DECL_RE.match(t)
    doc = minidom.parseString(t.encode("utf-8"))
    lines = [decl.group(1)] if decl else []
    _xml_node(doc.documentElement, 0, lines)
    return "\n".join(lines)


def _attrs(el):
    return "".join(f' {a.name}="{_esc_attr(a.value)}"' for a in el.attributes.values()) if el.attributes else ""


def _esc_text(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _esc_attr(s):
    return _esc_text(s).replace('"', "&quot;")


def _xml_node(node, level, out):
    pad = INDENT * level
    if node.nodeType == node.ELEMENT_NODE:
        kids = [c for c in node.childNodes
                if not (c.nodeType == c.TEXT_NODE and not c.data.strip())]
        open_tag = f"<{node.tagName}{_attrs(node)}"
        if not kids:
            out.append(f"{pad}{open_tag}/>")
        elif len(kids) == 1 and kids[0].nodeType == kids[0].TEXT_NODE:
            out.append(f"{pad}{open_tag}>{_esc_text(kids[0].data.strip())}</{node.tagName}>")
        else:
            out.append(f"{pad}{open_tag}>")
            for c in kids:
                _xml_node(c, level + 1, out)
            out.append(f"{pad}</{node.tagName}>")
    elif node.nodeType == node.CDATA_SECTION_NODE:
        inner = node.data.strip()
        out.append(f"{pad}<![CDATA[")
        if inner.startswith("<") and _try_xml(inner) is not None:
            for line in format_xml(inner).split("\n"):
                out.append(f"{pad}{INDENT}{line}")
        elif inner[:1] in "{[" and _try_json(inner) is not None:
            for line in format_json(inner).split("\n"):
                out.append(f"{pad}{INDENT}{line}")
        else:
            out.append(f"{pad}{INDENT}{inner}")
        out.append(f"{pad}]]>")
    elif node.nodeType == node.TEXT_NODE:
        out.append(f"{pad}{_esc_text(node.data.strip())}")
    elif node.nodeType == node.COMMENT_NODE:
        out.append(f"{pad}<!--{node.data}-->")


def format_text(info, sort_keys=False):
    """Pretty format when possible; otherwise return the cleaned text."""
    t = info["cleaned"]
    if not info["valid"]:
        return t
    try:
        if info["format"] == "json":
            return format_json(t, sort_keys)
        if info["format"] in ("xml", "soap"):
            return format_xml(t)
        if info["format"] == "form":
            return "\n".join(f"{k} = {v}" for k, v in parse_qsl(t, keep_blank_values=True))
    except Exception:
        return t
    return t


# =====================================================================
#  Diff
# =====================================================================
def _key(line, ignore_ws, ignore_case):
    if ignore_ws:
        line = re.sub(r"\s+", "", line)
    if ignore_case:
        line = line.lower()
    return line


def _prefix_suffix(a, b):
    """Highlight only the middle part that differs (keeps the output readable)."""
    p = 0
    while p < min(len(a), len(b)) and a[p] == b[p]:
        p += 1
    s = 0
    while s < min(len(a), len(b)) - p and a[len(a) - 1 - s] == b[len(b) - 1 - s]:
        s += 1
    def segs(x):
        out = []
        if p: out.append([0, x[:p]])
        if len(x) - s > p: out.append([1, x[p:len(x) - s]])
        if s: out.append([0, x[len(x) - s:]])
        return out
    return segs(a), segs(b)


def _inline(a, b):
    """Character-level segments for a changed pair of lines."""
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    ops = sm.get_opcodes()

    # Mostly different values (e.g. two unrelated URLs): one solid highlight is clearer
    p, s = 0, 0
    while p < min(len(a), len(b)) and a[p] == b[p]: p += 1
    while s < min(len(a), len(b)) - p and a[-1 - s] == b[-1 - s]: s += 1
    mid_a, mid_b = a[p:len(a) - s], b[p:len(b) - s]
    if mid_a and mid_b and difflib.SequenceMatcher(None, mid_a, mid_b, autojunk=False).ratio() < 0.6:
        return _prefix_suffix(a, b)

    # Fold tiny equal fragments (1-2 chars) that sit BETWEEN two changes into one change
    ops = [list(o) for o in ops]
    for k in range(1, len(ops) - 1):
        op, i1, i2 = ops[k][0], ops[k][1], ops[k][2]
        if op == "equal" and (i2 - i1) < 3 and ops[k - 1][0] != "equal" and ops[k + 1][0] != "equal":
            ops[k][0] = "replace"
    merged = []
    for op, i1, i2, j1, j2 in ops:
        if merged and op != "equal" and merged[-1][0] != "equal":
            _, pi1, _, pj1, _ = merged.pop()
            merged.append(("replace", pi1, i2, pj1, j2))
        else:
            merged.append((op, i1, i2, j1, j2))

    left, right = [], []
    for op, i1, i2, j1, j2 in merged:
        if op == "equal":
            left.append([0, a[i1:i2]]); right.append([0, b[j1:j2]])
        else:
            if i2 > i1: left.append([1, a[i1:i2]])
            if j2 > j1: right.append([1, b[j1:j2]])
    return left, right


def build_diff(a_text, b_text, ignore_ws=False, ignore_case=False):
    a_lines, b_lines = a_text.split("\n"), b_text.split("\n")
    a_keys = [_key(l, ignore_ws, ignore_case) for l in a_lines]
    b_keys = [_key(l, ignore_ws, ignore_case) for l in b_lines]

    sm = difflib.SequenceMatcher(None, a_keys, b_keys, autojunk=False)
    rows, added, removed, changed = [], 0, 0, 0

    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                rows.append({"t": "eq", "ln": i1 + k + 1, "rn": j1 + k + 1,
                             "l": [[0, a_lines[i1 + k]]], "r": [[0, b_lines[j1 + k]]]})
        elif op == "delete":
            for k in range(i1, i2):
                rows.append({"t": "del", "ln": k + 1, "rn": None, "l": [[1, a_lines[k]]], "r": None})
            removed += i2 - i1
        elif op == "insert":
            for k in range(j1, j2):
                rows.append({"t": "ins", "ln": None, "rn": k + 1, "l": None, "r": [[1, b_lines[k]]]})
            added += j2 - j1
        else:  # replace: pair lines, then leftovers
            n = min(i2 - i1, j2 - j1)
            for k in range(n):
                l, r = _inline(a_lines[i1 + k], b_lines[j1 + k])
                rows.append({"t": "chg", "ln": i1 + k + 1, "rn": j1 + k + 1, "l": l, "r": r})
            changed += n
            for k in range(i1 + n, i2):
                rows.append({"t": "del", "ln": k + 1, "rn": None, "l": [[1, a_lines[k]]], "r": None})
                removed += 1
            for k in range(j1 + n, j2):
                rows.append({"t": "ins", "ln": None, "rn": k + 1, "l": None, "r": [[1, b_lines[k]]]})
                added += 1

    a_join, b_join = "\n".join(a_keys), "\n".join(b_keys)
    if len(a_join) + len(b_join) <= 120_000:
        similarity = difflib.SequenceMatcher(None, a_join, b_join, autojunk=False).ratio()
    else:
        similarity = sm.ratio()

    return {
        "rows": rows,
        "stats": {
            "similarity": round(similarity * 100, 1),
            "added": added, "removed": removed, "changed": changed,
            "identical": added == removed == changed == 0,
            "left_lines": len(a_lines), "right_lines": len(b_lines),
        },
    }


# =====================================================================
#  Routes
# =====================================================================
@app.route("/")
def index():
    return render_template("index.html")


@app.post("/api/detect")
def api_detect():
    data = request.get_json(silent=True) or {}
    info = detect(data.get("text", ""))
    info.pop("cleaned", None)
    return jsonify(info)


@app.post("/api/format")
def api_format():
    data = request.get_json(silent=True) or {}
    info = detect(data.get("text", ""))
    return jsonify(format=info["label"], valid=info["valid"], error=info["error"],
                   notes=info["notes"], result=format_text(info, data.get("sortKeys", False)))


@app.post("/api/compare")
def api_compare():
    data = request.get_json(silent=True) or {}
    left, right = data.get("left", ""), data.get("right", "")
    if not left.strip() and not right.strip():
        return jsonify(error="Paste some text on both sides to compare."), 400

    opts = data.get("options", {})
    li, ri = detect(left), detect(right)

    if opts.get("format", True):
        lt, rt = format_text(li, opts.get("sortKeys", False)), format_text(ri, opts.get("sortKeys", False))
    elif opts.get("clean", True):
        lt, rt = li["cleaned"], ri["cleaned"]
    else:
        lt, rt = left.strip("\n"), right.strip("\n")

    diff = build_diff(lt, rt, opts.get("ignoreWhitespace", False), opts.get("ignoreCase", False))
    strip = lambda d: {k: d[k] for k in ("format", "label", "notes", "valid", "error")}
    return jsonify(left=strip(li), right=strip(ri), **diff)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)