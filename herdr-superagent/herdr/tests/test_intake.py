import json
import sys
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as E
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from sa_intake import parse_intake
from sa_state import StateError

WS, A, B = "00000001", "1100000000000000001", "1100000000000000002"
BASE = "https://www.tapd.cn/"
BUG = BASE + WS + "/bugtrace/bugs/view?bug_id=" + A
ALIAS = BASE + "tapd_fe/" + WS + "/bug/detail/" + A
OTHER = BASE + WS + "/bug/detail/" + B
STORY = BASE + WS + "/prong/stories/view/" + A
NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"


def make_book(path, broken=False):
    """Create a compact real OOXML package containing URL, object, formula, inline and shared cells."""
    def xml(tag, attributes=None):
        return E.Element(tag, attributes or {})

    def sheet(cells, links=()):
        ws = xml("worksheet", {"xmlns": NS, "xmlns:r": R})
        sd = E.SubElement(ws, "sheetData")
        rows = {}
        for ref, typ, val in cells:
            r = "".join(d for d in ref if d.isdigit())
            if r not in rows:
                rows[r] = E.SubElement(sd, "row", r=r)
            c = E.SubElement(rows[r], "c", r=ref)
            if typ == "inline":
                c.set("t", "inlineStr")
                E.SubElement(E.SubElement(c, "is"), "t").text = val
            elif typ == "rich":
                c.set("t", "inlineStr")
                inline = E.SubElement(c, "is")
                for part in (val[:20], val[20:]):
                    E.SubElement(E.SubElement(inline, "r"), "t").text = part
            elif typ == "formula":
                E.SubElement(c, "f").text = val
            else:
                c.set("t", typ)
                E.SubElement(c, "v").text = val
        rels = xml("Relationships", {"xmlns": PR})
        if links:
            hl = E.SubElement(ws, "hyperlinks")
            for i, (ref, target) in enumerate(links):
                key = "rId" + str(i)
                E.SubElement(hl, "hyperlink", {"ref": ref, "r:id": key})
                E.SubElement(rels, "Relationship", Id=key, Type=R + "/hyperlink",
                             TargetMode="External", Target=target)
        return ws, rels

    one, one_links = sheet([
        ("A1", "inline", "secret project notes should never be serialized"),
        ("A2", "s", "0"), ("A3", "inline", "打开第一条"), ("A4", "formula", f'HYPERLINK("{OTHER}","第二条")'),
        ("B5", "formula", 'HYPERLINK(A1,"动态链接")'), ("A7", "rich", BUG),
        ("A8", "inline", BUG), ("A9", "inline", "no link"), ("A11", "inline", ALIAS)
    ], [("A3", ALIAS), ("C6", BUG), ("A8", OTHER), ("A11", BUG)])
    two, _ = sheet([("C2", "inline", STORY + "，" + OTHER)])
    ss = xml("sst", {"xmlns": NS})
    E.SubElement(E.SubElement(ss, "si"), "t").text = BUG
    wb = xml("workbook", {"xmlns": NS, "xmlns:r": R})
    sheets = E.SubElement(wb, "sheets")
    for i, name in enumerate(("缺陷一", "关联需求"), 1):
        E.SubElement(sheets, "sheet", {"name": name, "sheetId": str(i), "r:id": f"rId{i}"})
    wr = xml("Relationships", {"xmlns": PR})
    for i in (1, 2):
        E.SubElement(wr, "Relationship", Id=f"rId{i}", Type=R + "/worksheet", Target=f"worksheets/sheet{i}.xml")
    E.SubElement(wr, "Relationship", Id="ss", Type=R + "/sharedStrings", Target="/xl/sharedStrings.xml")
    roots = xml("Relationships", {"xmlns": PR})
    E.SubElement(roots, "Relationship", Id="rId1", Type=R + "/officeDocument", Target="xl/workbook.xml")
    ct = xml("Types", {"xmlns": "http://schemas.openxmlformats.org/package/2006/content-types"})
    E.SubElement(ct, "Default", Extension="rels", ContentType="application/vnd.openxmlformats-package.relationships+xml")
    E.SubElement(ct, "Default", Extension="xml", ContentType="application/xml")
    for part, typ in (("workbook", "sheet.main"), ("sharedStrings", "sharedStrings"),
                      ("worksheets/sheet2", "worksheet"), ("worksheets/sheet1", "worksheet")):
        E.SubElement(ct, "Override", PartName=f"/xl/{part}.xml",
                     ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml." + typ + "+xml")
    with ZipFile(path, "w") as out:
        for name, value in {"[Content_Types].xml": ct, "_rels/.rels": roots,
                "xl/workbook.xml": wb, "xl/_rels/workbook.xml.rels": wr, "xl/sharedStrings.xml": ss,
                "xl/worksheets/sheet1.xml": one, "xl/worksheets/_rels/sheet1.xml.rels": one_links,
                "xl/worksheets/sheet2.xml": two}.items():
            out.writestr(name, b"<bad" if broken and name == "xl/worksheets/sheet2.xml" else E.tostring(value))


class IntakeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="local-intake-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_message_aliases_identity_and_source_dedup(self):
        batch = parse_intake(f"启用superagent模式：<{BUG}>、{ALIAS}\n{STORY}；{BUG}")
        self.assertEqual([f"tapd:{WS}:bug:{A}", f"tapd:{WS}:story:{A}"], [i["key"] for i in batch["items"]])
        self.assertEqual([1, 2], [s["line"] for s in batch["items"][0]["sources"]])
        self.assertEqual([], batch["unresolved"])
        data = parse_intake(f'{STORY} {STORY.replace(WS + "/", "00000002/")} {ALIAS}?from=mail')
        self.assertEqual(3, len(data["items"]))
        self.assertNotIn("from=", data["items"][-1]["url"])

    def test_mismatch_short_id_shortlink_and_spoof_never_become_confirmed_links(self):
        other = BASE + WS + "/prong/stories/view/0000001"
        data = parse_intake(f"{BUG}\n{STORY}\n{other}\nhttps://tapd.cn/x/Test\nhttps://tapd.cn.example/{WS}/bug/detail/{A}", kind="bug")
        self.assertEqual([A], [i["id"] for i in data["items"]])
        self.assertEqual({"kind_mismatch", "short_id", "unresolved_url", "non_tapd_url"},
                         {i["reason"] for i in data["unresolved"]})
        data = parse_intake(f"{BUG}&id={B}")
        self.assertEqual("ambiguous_id", data["unresolved"][0]["reason"])
        redirect = parse_intake("https://example.invalid/go?next=" + BUG)
        self.assertEqual([], redirect["items"])
        self.assertEqual(["non_tapd_url"], [i["reason"] for i in redirect["unresolved"]])

    def test_prose_is_retained_but_not_invented_as_tapd_object(self):
        text = "启用SuperAgent模式做需求：示例项目做个只读分析，先别改。"
        self.assertEqual({"instruction": text, "items": [], "kind": "auto", "files": [], "unresolved": []},
                         parse_intake(text))

    def test_compact_list_separators_and_redacted_unresolved_links(self):
        d = parse_intake(f'{BUG},www.tapd.cn/{WS}/bug/detail/{B};https://tapd.cn/s/AbC?key=private\n'
                         f'https://tapd.cn/s/XyZ?ticket=private')
        self.assertEqual([A, B], [i["id"] for i in d["items"]])
        self.assertEqual(["https://tapd.cn/s/AbC", "https://tapd.cn/s/XyZ"],
                         [i["url"] for i in d["unresolved"]])
    def test_excel_scans_multisheet_multiple_forms_and_never_includes_private_prose(self):
        p = self.root / "links.xlsx"
        make_book(p)
        d = parse_intake(BUG, [p])
        self.assertEqual([("bug", A), ("bug", B), ("story", A)], [(x["kind"], x["id"]) for x in d["items"]])
        self.assertEqual({"dynamic_formula", "ambiguous_cell"}, {x["reason"] for x in d["unresolved"]})
        self.assertEqual({"A2", "A3", "C6", "A7", "A11"}, {s["cell"] for s in d["items"][0]["sources"] if "cell" in s})
        self.assertEqual({"A4", "C2"}, {s["cell"] for s in d["items"][1]["sources"]})
        self.assertTrue(all(Path(s["file"]) == p.resolve() for x in d["items"] for s in x["sources"] if "file" in s))
        self.assertNotIn("secret project", json.dumps(d, ensure_ascii=False))

    def test_sheet_row_filter_respects_empty_hyperlinks_and_duplicate_sources(self):
        p = self.root / "links.xlsm"
        make_book(p)
        d = parse_intake("", [p], kind="bug", sheet="缺陷一", first_row=3, last_row=6)
        self.assertEqual([("bug", A), ("bug", B)], [(x["kind"], x["id"]) for x in d["items"]])
        self.assertEqual({"A3", "C6"}, {s["cell"] for s in d["items"][0]["sources"]})
        self.assertEqual(["dynamic_formula"], [x["reason"] for x in d["unresolved"]])
        self.assertEqual(["missing_sheet"], [x["reason"] for x in parse_intake("", [p], sheet="else")["unresolved"]])
        self.assertEqual(["empty_sheet"], [x["reason"] for x in
                         parse_intake("", [p], sheet="缺陷一", first_row=30)["unresolved"]])
        with self.assertRaises(StateError):
            parse_intake("", [p], first_row=4, last_row=2)

    def test_corrupt_sheet_and_unsupported_workbook_leave_independent_items(self):
        p = self.root / "damaged.xlsx"
        make_book(p, True)
        binary = self.root / "old.xls"
        binary.write_bytes(b"fake legacy fixture")
        d = parse_intake("", [self.root / "missing.xlsx", p, binary])
        self.assertEqual({A, B}, {x["id"] for x in d["items"]})
        self.assertEqual({"unreadable_file", "unreadable_sheet", "unsupported_file", "dynamic_formula",
                          "ambiguous_cell"}, {x["reason"] for x in d["unresolved"]})


if __name__ == "__main__":
    unittest.main()
