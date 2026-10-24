"""Standard-library tests for the fileSearch tool."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_ROOT))

from file_search.config import Config, ConfigError  # noqa: E402
from file_search.converters import convert_file  # noqa: E402
from file_search.search import search_documents  # noqa: E402
from file_search.server import create_server  # noqa: E402
from file_search.store import DocumentStore  # noqa: E402

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def make_docx(path: Path) -> None:
    document = f"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="{W_NS}">
  <w:body>
    <w:p><w:r><w:t>第一段 docx</w:t></w:r></w:p>
    <w:p><w:r><w:t>第二段</w:t><w:br w:type="page"/><w:t>第二页 科学</w:t></w:r></w:p>
  </w:body>
</w:document>"""
    core = f"""<?xml version="1.0" encoding="UTF-8"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/">
  <dc:title>测试 DOCX</dc:title>
</cp:coreProperties>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)
        archive.writestr("docProps/core.xml", core)


def make_pptx(path: Path) -> None:
    presentation = f"""<?xml version="1.0" encoding="UTF-8"?>
<p:presentation xmlns:p="{P_NS}" xmlns:r="{R_NS}">
  <p:sldIdLst>
    <p:sldId id="256" r:id="rId1"/>
    <p:sldId id="257" r:id="rId2"/>
  </p:sldIdLst>
</p:presentation>"""
    rels = f"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="{REL_NS}">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide2.xml"/>
</Relationships>"""

    def slide(text: str) -> str:
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<p:sld xmlns:p="{P_NS}" xmlns:a="{A_NS}">
  <p:cSld><p:spTree>
    <p:sp><p:txBody><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp>
  </p:spTree></p:cSld>
</p:sld>"""

    core = f"""<?xml version="1.0" encoding="UTF-8"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/">
  <dc:title>测试 PPTX</dc:title>
</cp:coreProperties>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ppt/presentation.xml", presentation)
        archive.writestr("ppt/_rels/presentation.xml.rels", rels)
        archive.writestr("ppt/slides/slide1.xml", slide("第一张幻灯片"))
        archive.writestr("ppt/slides/slide2.xml", slide("第二张幻灯片 科学"))
        archive.writestr("docProps/core.xml", core)


def make_pdf(path: Path) -> None:
    """Create a tiny valid two-page PDF without external libraries."""

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 7 0 R >> >> >>"
        ),
    ]
    stream1 = b"BT /F1 18 Tf 72 720 Td (Page one science) Tj ET"
    objects.append(
        b"<< /Length " + str(len(stream1)).encode() + b" >>\nstream\n"
        + stream1 + b"\nendstream"
    )
    objects.append(
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 6 0 R /Resources << /Font << /F1 7 0 R >> >> >>"
    )
    stream2 = b"BT /F1 18 Tf 72 720 Td (Page two science target) Tj ET"
    objects.append(
        b"<< /Length " + str(len(stream2)).encode() + b" >>\nstream\n"
        + stream2 + b"\nendstream"
    )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"

    xref = len(data)
    data += f"xref\n0 {len(objects) + 1}\n".encode()
    data += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        data += f"{offset:010d} 00000 n \n".encode()
    data += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode()
    path.write_bytes(data)


class ConverterTests(unittest.TestCase):
    def test_plain_text_pages_and_encoding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "note.md"
            path.write_text("第一页\n跨行内容\n\f第二页\n科学", encoding="utf-8")
            converted = convert_file(path)
            self.assertEqual(converted.pages, ("第一页\n跨行内容", "第二页\n科学"))
            self.assertEqual(converted.encoding, "utf-8")

            path.write_text("只有一页\f\n", encoding="utf-8")
            self.assertEqual(convert_file(path).pages, ("只有一页",))

    def test_docx_pages_and_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.docx"
            make_docx(path)
            converted = convert_file(path)
            self.assertEqual(len(converted.pages), 2)
            self.assertIn("第二页 科学", converted.pages[1])
            self.assertEqual(converted.title, "测试 DOCX")

    def test_pptx_slide_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.pptx"
            make_pptx(path)
            converted = convert_file(path)
            self.assertEqual(len(converted.pages), 2)
            self.assertIn("第一张幻灯片", converted.pages[0])
            self.assertIn("科学", converted.pages[1])
            self.assertEqual(converted.title, "测试 PPTX")

    @unittest.skipUnless(shutil.which("pdftotext"), "需要 poppler-utils 的 pdftotext")
    def test_pdf_physical_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "paper.pdf"
            make_pdf(path)
            converted = convert_file(path)
            self.assertEqual(len(converted.pages), 2)
            self.assertIn("science", converted.pages[0])
            self.assertIn("target", converted.pages[1])

    def test_html_strips_markup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "page.html"
            path.write_text(
                "<html><head><title>标题</title><style>x</style></head>"
                "<body><h1>标题一</h1><p>hello <b>world</b></p>"
                "<script>bad()</script></body></html>",
                encoding="utf-8",
            )
            converted = convert_file(path)
            self.assertIn("hello world", converted.pages[0])
            self.assertNotIn("bad()", converted.pages[0])
            self.assertEqual(converted.title, "标题")


class StoreAndSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data_dir = self.root / ".fileSearch"
        self.store = DocumentStore(self.root, self.data_dir)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_note(self) -> Path:
        path = self.root / "notes.md"
        path.write_text(
            "第一页第一行\n"
            "这是一个学科科\n"
            "学\n"
            "下一段目标词\n"
            "\f"
            "第二页\n"
            "科学 在这里\n",
            encoding="utf-8",
        )
        return path

    def test_import_cross_line_search_and_reload(self) -> None:
        path = self._write_note()
        imported, errors = self.store.import_paths([str(path)])
        self.assertEqual(len(imported), 1)
        self.assertEqual(errors, [])
        meta = imported[0]
        self.assertEqual(meta.name, "notes.md")
        self.assertEqual(meta.page_count, 2)

        result = search_documents(
            self.store.snapshot(), "科学", context_chars=4
        )
        self.assertEqual(result["total"], 2)
        first = result["results"][0]
        self.assertEqual(first["file_name"], "notes.md")
        self.assertEqual(first["page"], 1)
        self.assertEqual(first["line"], 2)
        self.assertEqual(first["end_line"], 3)
        self.assertEqual(first["start_column"], 7)
        self.assertEqual(first["end_column"], 1)
        highlighted = first["snippet"][
            first["highlight"]["start"] : first["highlight"]["end"]
        ]
        self.assertEqual(highlighted.replace("\n", ""), "科学")

        self.assertEqual(
            self.store.get(meta.id).pages[1],
            "第二页\n科学 在这里",
        )

        reloaded = DocumentStore(self.root, self.data_dir)
        reloaded_result = search_documents(reloaded.snapshot(), "目标词")
        self.assertEqual(reloaded_result["total"], 1)
        self.assertEqual(reloaded_result["results"][0]["page"], 1)

    def test_page_and_line_lookup(self) -> None:
        path = self._write_note()
        imported, _ = self.store.import_paths([str(path)])
        document = self.store.get(imported[0].id)
        self.assertEqual(document.pages[0].split("\n")[1], "这是一个学科科")
        self.assertEqual(document.lines[0][1], "这是一个学科科")

    def test_blank_line_stops_cross_line_match(self) -> None:
        path = self.root / "blank.md"
        path.write_text("科\n\n学", encoding="utf-8")
        self.store.import_paths([str(path)])
        self.assertEqual(search_documents(self.store.snapshot(), "科学")["total"], 0)

    def test_case_insensitive_and_pagination(self) -> None:
        path = self.root / "english.txt"
        path.write_text("Alpha beta\nALPHA gamma\nalpha delta", encoding="utf-8")
        self.store.import_paths([str(path)])
        result = search_documents(
            self.store.snapshot(), "alpha", limit=1, offset=1
        )
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["returned"], 1)
        self.assertEqual(result["results"][0]["line"], 2)
        self.assertTrue(result["has_more"])

        sensitive = search_documents(
            self.store.snapshot(), "alpha", case_sensitive=True
        )
        self.assertEqual(sensitive["total"], 1)

    def test_docx_pptx_directory_import_and_delete(self) -> None:
        make_docx(self.root / "sample.docx")
        make_pptx(self.root / "sample.pptx")
        self._write_note()
        imported, errors = self.store.import_paths(["."])
        self.assertEqual(errors, [])
        self.assertEqual(len(imported), 3)
        names = {meta.name for meta in imported}
        self.assertEqual(names, {"notes.md", "sample.docx", "sample.pptx"})

        result = search_documents(self.store.snapshot(), "科学")
        self.assertEqual(
            {item["file_name"] for item in result["results"]},
            {"notes.md", "sample.docx", "sample.pptx"},
        )

        docx_meta = next(meta for meta in imported if meta.name == "sample.docx")
        self.assertEqual(self.store.get(docx_meta.id).pages[1], "第二页 科学")
        self.store.delete(docx_meta.id)
        self.assertNotIn(
            "sample.docx",
            {doc.meta.name for doc in self.store.all_documents()},
        )
        self.assertTrue((self.root / "sample.docx").exists())

    def test_cache_lives_under_data_dir_and_is_not_imported(self) -> None:
        self._write_note()
        self.store.import_paths(["."])
        self.assertTrue(any((self.data_dir / "cache").glob("*.txt")))
        imported, errors = self.store.import_paths(["."])
        self.assertEqual(len(imported), 1)
        self.assertTrue(
            all(".fileSearch" not in meta.path for meta in imported)
        )
        self.assertEqual(errors, [])

    def test_reindex_updates_changed_source(self) -> None:
        path = self._write_note()
        self.store.import_paths([str(path)])
        self.assertEqual(
            search_documents(self.store.snapshot(), "新的关键词")["total"], 0
        )
        path.write_text("新的关键词 已经替换", encoding="utf-8")
        result = self.store.reindex()
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["unchanged"], 0)
        hits = search_documents(self.store.snapshot(), "新的关键词")
        self.assertEqual(hits["total"], 1)
        self.assertEqual(hits["results"][0]["page"], 1)

    def test_unsupported_and_missing_paths(self) -> None:
        (self.root / "file.bin").write_bytes(b"abc")
        _, errors = self.store.import_paths(["file.bin", "missing.txt"])
        self.assertEqual(len(errors), 2)
        self.assertFalse(self.store.all_documents())


class ConfigTests(unittest.TestCase):
    def test_defaults_and_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            loaded = Config.load(path)
            self.assertEqual(loaded.host, "127.0.0.1")
            self.assertEqual(loaded.port, 5872)
            self.assertEqual(loaded.context_chars, 60)

            loaded.port = 6000
            loaded.context_chars = 10
            loaded.save(path)
            again = Config.load(path)
            self.assertEqual(again.port, 6000)
            self.assertEqual(again.context_chars, 10)
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["host"], "127.0.0.1")

    def test_invalid_values(self) -> None:
        config = Config()
        config.port = 70000
        with self.assertRaises(ConfigError):
            config.validate()
        with self.assertRaises(ConfigError):
            Config.from_dict({"context_chars": -1})
        with self.assertRaises(ConfigError):
            Config.from_dict({"host": "http://127.0.0.1"})


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data_dir = self.root / ".fileSearch"
        self.store = DocumentStore(self.root, self.data_dir)
        self.note = self.root / "note.md"
        self.note.write_text(
            "第一段\n这是一个学科科\n学\n\f第二页\n目标词 科学",
            encoding="utf-8",
        )
        imported, errors = self.store.import_paths(["note.md"])
        self.assertEqual(len(imported), 1)
        self.assertEqual(errors, [])
        self.meta = imported[0]

        self.config = Config(context_chars=6)
        self.server = create_server(self.store, self.config, port=0)
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    def _request(self, method: str, path: str, payload: object | None = None):
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base + path,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read())
            finally:
                exc.close()

    def test_files_and_health(self) -> None:
        status, body = self._request("GET", "/api/files")
        self.assertEqual(status, 200)
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["files"][0]["name"], "note.md")
        self.assertTrue(body["files"][0]["cache_file"].endswith(".txt"))

        status, health = self._request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["files"], 1)

    def test_search_page_and_lines(self) -> None:
        query = urllib.parse.urlencode(
            {"q": "科学", "context": "5", "limit": "1"}
        )
        status, body = self._request("GET", f"/api/search?{query}")
        self.assertEqual(status, 200)
        result = body["results"][0]
        self.assertEqual(result["file_name"], "note.md")
        self.assertEqual(result["page"], 1)
        self.assertEqual(result["line"], 2)
        self.assertEqual(result["end_line"], 3)
        self.assertEqual(
            result["snippet"][
                result["highlight"]["start"] : result["highlight"]["end"]
            ].replace("\n", ""),
            "科学",
        )

        status, page = self._request(
            "GET", f"/api/files/{self.meta.id}/pages/1"
        )
        self.assertEqual(status, 200)
        self.assertIn("这是一个学科科", page["content"])
        self.assertEqual(page["lines"][1]["line"], 2)

        status, lines = self._request(
            "GET",
            f"/api/files/{self.meta.id}/lines?page=1&start=2&end=3",
        )
        self.assertEqual(status, 200)
        self.assertEqual(lines["line_count"], 2)
        self.assertEqual(lines["content"], "这是一个学科科\n学")

        status, one_line = self._request(
            "GET", f"/api/files/{self.meta.id}/lines?page=1&line=2"
        )
        self.assertEqual(status, 200)
        self.assertEqual(one_line["lines"], [{"line": 2, "text": "这是一个学科科"}])

    def test_post_search_and_import(self) -> None:
        status, body = self._request(
            "POST",
            "/api/search",
            {"q": "目标词", "context": 1, "file": self.meta.id},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["results"][0]["page"], 2)

        second = self.root / "second.txt"
        second.write_text("第二份文件 alpha", encoding="utf-8")
        status, imported = self._request(
            "POST", "/api/import", {"path": "second.txt"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(imported["count"], 1)
        self.assertEqual(imported["imported"][0]["name"], "second.txt")

        status, files = self._request("GET", "/api/files")
        self.assertEqual(status, 200)
        self.assertEqual(files["count"], 2)

        status, reindexed = self._request("POST", "/api/reindex")
        self.assertEqual(status, 200)
        self.assertEqual(reindexed["scanned"], 2)

    def test_errors_and_no_auth(self) -> None:
        status, body = self._request("GET", "/api/search")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "bad_request")

        status, body = self._request(
            "GET", f"/api/files/{self.meta.id}/pages/999"
        )
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "page_not_found")

        status, body = self._request("GET", "/api/nope")
        self.assertEqual(status, 404)

        status, body = self._request(
            "POST", "/api/import", {"path": ".fileSearch"}
        )
        self.assertEqual(status, 400)

    def test_delete_keeps_source(self) -> None:
        status, body = self._request(
            "DELETE", f"/api/files/{self.meta.id}"
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["source_preserved"])
        self.assertTrue(self.note.exists())
        status, files = self._request("GET", "/api/files")
        self.assertEqual(files["count"], 0)


if __name__ == "__main__":
    unittest.main()
