"""Inventory TAPD work items from user text and local Excel sheets, without executing or fetching."""

from __future__ import annotations

import posixpath
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from sa_state import StateError


# Keep the whole URL before classifying its host; do not extract a plausible TAPD
# suffix embedded in a different URL. Chinese prose and Markdown can abut links.
_URL = re.compile(
    r"""(?:(?:https?://)|(?<![\w./@-])(?:www\.)?tapd\.cn/)"""
    r"""[^\s\x00-\x1f<>"'`，。；、！？（）【】《》“”‘’\[\]()|]+""", re.I
)
_HOSTS = {"tapd.cn", "www.tapd.cn"}
_FULL_ID = re.compile(r"[0-9]{16,20}\Z")
_CELL = re.compile(r"\$?[A-Z]+\$?([1-9][0-9]*)\Z", re.I)
_LITERAL = re.compile(r'^\s*=?\s*(?:_xlfn\.)?HYPERLINK\s*\(\s*"((?:[^"]|"")*)"\s*[,;)]', re.I)
_HYPERLINK = re.compile(r"\bHYPERLINK\s*\(", re.I)
_NEXT_LINK = re.compile(r"[,;]+(?=(?:https?://|(?:www\.)?tapd\.cn/))", re.I)
_WORKBOOK_LIMIT = 128 * 1024 * 1024  # total uncompressed zip size; do not unpack onto disk


def _url_tokens(text):
    return [m[0].rstrip(".,;:!?#") for m in _URL.finditer(_NEXT_LINK.sub(" ", text))]


def _parse_link(raw):
    """Return (workspace, kind, id). Ignore untrusted or ambiguous address parameters."""
    try:
        u = urlsplit(raw if "://" in raw else "https://" + raw)
        host = u.hostname or ""
        if (host not in _HOSTS or u.username is not None or u.password is not None
                or u.scheme.lower() not in {"http", "https"} or u.port not in {None, 80, 443}):
            return None, "non_tapd_url"
    except ValueError:
        return None, "unsupported_url"
    path = unquote(u.path).strip("/")
    m = re.fullmatch(
        r"(?:tapd_fe/)?([0-9]+)/(?:(?:prong/)?(stories|bugs|story|bug)/(?:view|detail)"
        r"|bugtrace/(bugs)/view|prong/bugtrace/(bugs)/view)(?:/([0-9]+))?", path, re.I)
    if not m:
        return None, "unresolved_url"
    workspace, family, entity_id = m[1], (m[2] or m[3] or m[4]).lower(), m[5]
    kind = "story" if family in {"story", "stories"} else "bug"
    params = parse_qs(u.query, keep_blank_values=True)
    ids = {v for k in ("id", f"{kind}_id") for v in params.get(k, [])}
    if entity_id:
        ids.add(entity_id)
    if len(ids) != 1 or not re.fullmatch(r"[0-9]+", next(iter(ids), "")):
        return None, "ambiguous_id"
    entity_id = ids.pop()
    if not _FULL_ID.fullmatch(entity_id):
        return None, "short_id"
    return (workspace, kind, entity_id), None


_DETAIL = {
    "non_tapd_url": "不是可直接识别的 TAPD 单据地址；如为短链请提供展开后的需求或缺陷链接。",
    "unresolved_url": "未识别的 TAPD 路径或短链，需要展开后的单据详情链接。",
    "unsupported_url": "链接格式不完整或不支持。",
    "short_id": "未提供完整 TAPD 数字 ID，不根据显示短号拼接。",
    "ambiguous_id": "缺少完整 ID 或链接中的 ID 相互冲突。",
    "kind_mismatch": "链接的对象类型与本次指定类型不符，未列入执行清单。",
    "dynamic_formula": "该超链接是动态公式；未执行，请提供静态 TAPD 详情链接。",
    "ambiguous_cell": "单元格显示的链接与实际目标不同；请确认要处理的单据。",
    "empty_sheet": "选定的表格范围内没有识别到 TAPD 链接。",
    "missing_sheet": "工作簿中没有指定的工作表。",
    "unsupported_file": "请将旧格式工作簿另存为 .xlsx，或直接粘贴 TAPD 链接。",
    "unreadable_file": "文件不存在、无权读取或不是可读取的 Excel 工作簿。",
    "unreadable_sheet": "工作表内容或链接映射无法完整读取，已保留其他工作表结果。",
}


class _Inventory:
    def __init__(self, kind, text, files):
        self.kind = kind
        self.rows = {}
        self.issues = []
        self._reported = set()
        self.occurrences = 0
        self.text = text
        self.files = files

    def issue(self, reason, source, detail=None, hint=None):
        key = (reason, tuple(source.items()), detail, hint)
        if key in self._reported:
            return
        self._reported.add(key)
        problem = {"reason": reason, "source": source, "detail": detail or _DETAIL[reason]}
        if hint:
            problem["url"] = hint
        self.issues.append(problem)

    def url(self, raw, source):
        item, problem = _parse_link(raw)
        if problem:
            # Do not save arbitrary tracking query strings, credentials or workbook prose.
            hint = None
            if problem not in {"non_tapd_url", "unsupported_url"}:
                u = urlsplit(raw if "://" in raw else "https://" + raw)
                hint = "https://" + u.hostname + u.path
                # A short ID sometimes sits in the query, not the path.
                ids = [(key, val) for key, vals in parse_qs(u.query).items()
                       if key in {"id", "story_id", "bug_id"} for val in vals if re.fullmatch(r"[0-9]+", val)]
                if ids:
                    hint += "?" + "&".join(key + "=" + val for key, val in ids)
            self.issue(problem, source, hint=hint)
            return
        ws, kind, entity = item
        path = f"prong/stories/view/{entity}" if kind == "story" else f"bugtrace/bugs/view/{entity}"
        url = f"https://www.tapd.cn/{ws}/{path}"
        if self.kind != "auto" and kind != self.kind:
            self.issue("kind_mismatch", source, hint=url)
            return
        identity = f"tapd:{ws}:{kind}:{entity}"
        row = self.rows.setdefault(identity, {
            "key": identity, "workspace_id": ws, "kind": kind, "id": entity, "url": url, "sources": []})
        if source not in row["sources"]:
            row["sources"].append(source)
        self.occurrences += 1

    def cell(self, source, content="", target=None, formula=None):
        visible = _url_tokens(content)
        if formula is not None:
            literal = _LITERAL.match(formula)
            if literal:
                target = literal[1].replace('""', '"')
            elif _HYPERLINK.search(formula):
                self.issue("dynamic_formula", source)
                return
        if target is not None:
            # A label may be a human-readable alias. A different apparent URL must not
            # turn one clickable cell into two authorized work items.
            resolved = _parse_link(target)[0]
            if any(url != target and (not resolved or _parse_link(url)[0] != resolved) for url in visible):
                self.issue("ambiguous_cell", source)
                return
            self.url(target, source)
        else:
            for url in visible:
                self.url(url, source)

    def result(self):
        return {"kind": self.kind, "instruction": self.text, "files": self.files,
                "items": list(self.rows.values()), "unresolved": self.issues}


def _tag(node):
    return node.tag.rsplit("}", 1)[-1]


def _elements(node, tag):
    return (e for e in node.iter() if _tag(e) == tag)


def _attr_id(node):
    return next((v for k, v in node.attrib.items() if k.endswith("}id")), "")


def _strings(node):
    # Rich text contains one <t> per run. Phonetic guides are not cell content.
    return "".join(child.text or "" for part in node if _tag(part) in {"r", "t"}
                   for child in (part if _tag(part) == "r" else (part,)) if _tag(child) == "t")


def _xml(archive, name):
    with archive.open(name) as fp:
        return ET.parse(fp).getroot()


def _rels(archive, part):
    folder, base = posixpath.split(part)
    name = posixpath.join(folder, "_rels", base + ".rels")
    if name not in archive.namelist():
        return {}
    return {r.attrib["Id"]: (r.attrib.get("Type", "").rsplit("/", 1)[-1], r.attrib.get("TargetMode"),
                             r.attrib["Target"]) for r in _xml(archive, name)}


def _related(part, path):
    return posixpath.normpath(posixpath.join(posixpath.dirname(part), unquote(path))).lstrip("/")


def _row_from_ref(ref):
    m = _CELL.fullmatch(ref)
    if not m:
        raise ValueError("invalid cell reference")
    return int(m[1])


def _in_scope(ref, first, last):
    ends = ref.split(":")
    a, b = _row_from_ref(ends[0]), _row_from_ref(ends[-1])
    return b >= first and (last is None or a <= last)


def _worksheet(inv, z, part, sheet_source, strings, first, last):
    doc, links = _xml(z, part), _rels(z, part)
    targets = {}
    for h in _elements(doc, "hyperlink"):
        ref, key = h.attrib["ref"], _attr_id(h)
        if not key or not _in_scope(ref, first, last):
            continue
        if key not in links:
            inv.issue("unreadable_sheet", dict(sheet_source, cell=ref))
            continue
        relation, mode, target = links[key]
        if relation == "hyperlink" and mode == "External":
            targets[ref] = target
    for cell in _elements(doc, "c"):
        ref = cell.attrib["r"]
        if not _in_scope(ref, first, last):
            continue
        v = next(_elements(cell, "v"), None)
        formula = next(_elements(cell, "f"), None)
        content = ""
        if formula is None:
            if cell.get("t") == "s" and v is not None:
                index = int(v.text)
                if index < 0 or index >= len(strings):
                    raise ValueError("invalid shared string")
                content = strings[index]
            elif cell.get("t") == "inlineStr":
                inline = next(_elements(cell, "is"), None)
                if inline is not None:
                    content = _strings(inline)
            elif cell.get("t") == "str" and v is not None:
                content = v.text or ""
        inv.cell(dict(sheet_source, cell=ref), content, targets.pop(ref, None),
                 None if formula is None else formula.text or "")
    # Excel hyperlinks can be attached to empty cells and ranges.
    for ref, target in targets.items():
        inv.cell(dict(sheet_source, cell=ref), target=target)


_READ_ERRORS = (BadZipFile, OSError, EOFError, ValueError, TypeError, IndexError, KeyError, RuntimeError,
                ET.ParseError)


def _workbook(inv, path, sheet, first, last):
    src = {"type": "file", "file": str(path)}
    if path.suffix.lower() not in {".xlsx", ".xlsm"}:
        inv.issue("unsupported_file", src)
        return
    with ZipFile(path) as z:
        if sum(part.file_size for part in z.infolist()) > _WORKBOOK_LIMIT:
            inv.issue("unreadable_file", src, "工作簿解压后超过 128 MiB；请另存只含 TAPD 链接的表。")
            return
        part = "xl/workbook.xml"
        wb, rels = _xml(z, part), _rels(z, part)
        shared = next((t for typ, mode, t in rels.values() if typ == "sharedStrings" and mode != "External"), None)
        strings = [_strings(si) for si in _elements(_xml(z, _related(part, shared)), "si")] if shared else []
        matched = False
        for node in _elements(wb, "sheet"):
            name = node.attrib["name"]
            if sheet is not None and sheet != name:
                continue
            loc = dict(src, sheet=name)
            previous = (inv.occurrences, len(inv.issues))
            try:
                kind, mode, target = rels[_attr_id(node)]
                if kind == "chartsheet" and sheet is None:
                    continue
                matched = True
                if kind != "worksheet" or mode == "External":
                    inv.issue("unreadable_sheet", loc)
                else:
                    _worksheet(inv, z, _related(part, target), loc, strings, first, last)
                if previous == (inv.occurrences, len(inv.issues)) and sheet is not None:
                    inv.issue("empty_sheet", loc)
            except _READ_ERRORS:
                matched = True
                inv.issue("unreadable_sheet", loc)
        if not matched:
            inv.issue("missing_sheet" if sheet is not None else "empty_sheet", dict(src, sheet=sheet) if sheet else src)


def parse_intake(text, files=(), kind="auto", *, sheet=None, first_row=None, last_row=None):
    """Local, side-effect-free inventory; attachments furnish references, never instructions."""
    if kind not in {"auto", "story", "bug"}:
        raise StateError("kind must be auto, story or bug")
    first = 1 if first_row is None else first_row
    if type(first) is not int or first < 1 or (last_row is not None and
            (type(last_row) is not int or last_row < first)):
        raise StateError("row scope must be positive; last_row cannot precede first_row")
    if sheet is not None and (not isinstance(sheet, str) or not sheet.strip()):
        raise StateError("sheet name must be nonempty")
    if isinstance(files, (str, Path)):
        files = [files]
    paths = list(dict.fromkeys(Path(p).expanduser().resolve() for p in files))
    inv = _Inventory(kind, text or "", [str(p) for p in paths])
    for n, line in enumerate(inv.text.splitlines(), 1):
        for url in _url_tokens(line):
            inv.url(url, {"type": "message", "line": n})
    for path in paths:
        src = {"type": "file", "file": str(path)}
        before = (inv.occurrences, len(inv.issues))
        try:
            _workbook(inv, path, sheet, first, last_row)
        except _READ_ERRORS:
            inv.issue("unreadable_file", src)
        if before == (inv.occurrences, len(inv.issues)):
            inv.issue("empty_sheet", src)
    return inv.result()
