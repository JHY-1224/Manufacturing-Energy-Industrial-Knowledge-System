import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.loaders import get_loader
from app.loaders.chatml_loader import ChatMLLoader
from app.loaders.csv_excel_loader import CsvExcelLoader
from app.loaders.docx_loader import DocxLoader
from app.loaders.html_loader import HtmlLoader
from app.loaders.pdf_loader import PdfLoader
from app.preprocessors.table_extractor import TableExtractor


class PdfFixture:
    def __init__(self, pages, encrypted=False):
        self.pages = pages
        self.needs_pass = encrypted
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def __len__(self):
        return len(self.pages)

    def __iter__(self):
        return iter(self.pages)


def pdf_page(text, images=False, drawings=False):
    def get_text(mode):
        if mode != "text":
            raise AssertionError("PDF loader must request supported plain text extraction")
        return text
    return SimpleNamespace(get_text=get_text, get_images=lambda: ["image"] if images else [], get_drawings=lambda: ["drawing"] if drawings else [])


class LoadersP1Test(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="p1-loader-test-"))

    def source(self, name, content=""):
        target = self.root / name
        target.write_text(content, encoding="utf-8")
        return target

    def test_missing_optional_parsers_are_errors_not_documents(self):
        for module, loader, name, package in (("fitz", PdfLoader(), "test.pdf", "pymupdf"), ("docx", DocxLoader(), "test.docx", "python-docx"), ("openpyxl", CsvExcelLoader(), "test.xlsx", "openpyxl")):
            with self.subTest(module=module), patch.dict(sys.modules, {module: None}):
                with self.assertRaisesRegex(ValueError, "install " + package):
                    loader.load(self.source(name))

    def test_legacy_office_formats_are_explicitly_unsupported(self):
        for name in ("old.doc", "old.xls"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "not supported|Only .csv"):
                get_loader(name).load(self.source(name, "not an Office document"))

    def test_pdf_extracts_actual_page_text_and_records_blank_page(self):
        pdf = PdfFixture([pdf_page("第一页工艺参数"), pdf_page(""), pdf_page("第三页说明", images=True)])
        with patch.dict(sys.modules, {"fitz": SimpleNamespace(open=lambda path: pdf)}):
            documents = PdfLoader().load(self.source("pages.pdf"))
        self.assertEqual([item.metadata["page"] for item in documents], [1, 2, 3])
        self.assertEqual(documents[0].content, "第一页工艺参数")
        self.assertTrue(documents[1].metadata["blank_page"])
        self.assertEqual(documents[1].content, "")
        self.assertFalse(documents[2].metadata["visual_content_processed"])
        self.assertTrue(pdf.closed)

    def test_pdf_scan_page_cannot_silently_become_successful_text(self):
        for pages in ([pdf_page("", images=True)], [pdf_page("valid"), pdf_page("", drawings=True)]):
            pdf = PdfFixture(pages)
            with patch.dict(sys.modules, {"fitz": SimpleNamespace(open=lambda path: pdf)}):
                with self.assertRaisesRegex(ValueError, "OCR is not supported"):
                    PdfLoader().load(self.source("scan.pdf"))
            self.assertTrue(pdf.closed)
        with patch.dict(sys.modules, {"fitz": SimpleNamespace(open=lambda path: PdfFixture([pdf_page("")]))}):
            with self.assertRaisesRegex(ValueError, "no readable text"):
                PdfLoader().load(self.source("blank.pdf"))

    @unittest.skipUnless(importlib.util.find_spec("fitz"), "PyMuPDF is optional")
    def test_real_pdf_text_blank_and_scanned_pages(self):
        import fitz
        text_path = self.root / "real-text.pdf"
        with fitz.open() as pdf:
            page = pdf.new_page()
            page.insert_text((72, 72), "Synthetic coating pressure: 0.2 MPa")
            preview = page.get_pixmap().tobytes("png")
            pdf.new_page()
            pdf.save(text_path)
        documents = PdfLoader().load(text_path)
        self.assertEqual(len(documents), 2)
        self.assertIn("0.2 MPa", documents[0].content)
        self.assertTrue(documents[1].metadata["blank_page"])
        scan_path = self.root / "real-scan.pdf"
        with fitz.open() as pdf:
            first = pdf.new_page()
            first.insert_text((72, 72), "Readable synthetic introduction")
            scanned = pdf.new_page()
            scanned.insert_image(scanned.rect, stream=preview)
            pdf.save(scan_path)
        with self.assertRaisesRegex(ValueError, "page 2.*OCR is not supported"):
            PdfLoader().load(scan_path)

    @unittest.skipUnless(importlib.util.find_spec("docx"), "python-docx is optional")
    def test_real_docx_retains_paragraph_table_order(self):
        from docx import Document
        original = Document()
        original.add_paragraph("前置工艺说明")
        table = original.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "参数"
        table.cell(0, 1).text = "单位"
        table.cell(1, 0).text = "压力"
        table.cell(1, 1).text = "MPa"
        original.add_paragraph("后置适用条件")
        path = self.root / "actual.docx"
        original.save(path)
        content = DocxLoader().load(path)[0].content
        self.assertLess(content.index("前置工艺说明"), content.index("压力"))
        self.assertLess(content.index("压力"), content.index("后置适用条件"))
        text, tables = TableExtractor().extract(content)
        self.assertEqual(len(tables), 1)
        self.assertIn("MPa", tables[0].content)
        self.assertIn("后置适用条件", text)

    def test_csv_preserves_quoted_multiline_cell_and_column_boundaries(self):
        source = self.source("sample.csv", '\ufeff参数,说明\n压力,"第一行\n第二行|阈值"\n')
        document = CsvExcelLoader().load(source)[0]
        self.assertIn("第一行<br>第二行&#124;阈值", document.content)
        self.assertNotIn("\ufeff", document.content)
        self.assertEqual(document.metadata["row_count"], 2)
        self.assertEqual(len(TableExtractor().extract(document.content)[1]), 1)
        with self.assertRaises(ValueError):
            CsvExcelLoader().load(self.source("broken.csv", 'a,b\n"unterminated'))

    def test_excel_keeps_sheets_separate_and_closes_workbook(self):
        workbook = SimpleNamespace(worksheets=[
            SimpleNamespace(title="材料", iter_rows=lambda **kwargs: iter([("牌号", "值"), ("A", 12)])),
            SimpleNamespace(title="设备", iter_rows=lambda **kwargs: iter([("型号", "公式"), ("B", "=1+2")])),
            SimpleNamespace(title="空表", iter_rows=lambda **kwargs: iter([(None, None)])),
        ])
        closed = []
        workbook.close = lambda: closed.append(True)
        def load_workbook(path, **kwargs):
            self.assertTrue(kwargs["read_only"])
            self.assertFalse(kwargs["data_only"])
            return workbook
        with patch.dict(sys.modules, {"openpyxl": SimpleNamespace(load_workbook=load_workbook)}):
            documents = CsvExcelLoader().load(self.source("sheets.xlsx"))
        self.assertEqual([item.metadata["sheet"] for item in documents], ["材料", "设备"])
        self.assertNotIn("型号", documents[0].content)
        self.assertIn("=1+2", documents[1].content)
        self.assertEqual(closed, [True])

    @unittest.skipUnless(importlib.util.find_spec("openpyxl"), "openpyxl is optional")
    def test_real_xlsx_preserves_sheets_values_formulas_and_empty_cells(self):
        from openpyxl import Workbook
        workbook = Workbook()
        materials = workbook.active
        materials.title = "材料"
        materials.append(["材料", "温度", "备注"])
        materials.append(["合成材料A", 0, None])
        equipment = workbook.create_sheet("设备")
        equipment.append(["设备", "计算式"])
        equipment.append(["合成设备B", "=1+2"])
        workbook.create_sheet("空表")
        path = self.root / "real-sheets.xlsx"
        workbook.save(path)
        workbook.close()
        documents = CsvExcelLoader().load(path)
        self.assertEqual([item.metadata["sheet"] for item in documents], ["材料", "设备"])
        self.assertIn("合成材料A | 0 |", documents[0].content)
        self.assertNotIn("None", documents[0].content)
        self.assertIn("=1+2", documents[1].content)
        self.assertTrue(all(len(TableExtractor().extract(item.content)[1]) == 1 for item in documents))

    def test_html_removes_active_content_but_preserves_extractable_table(self):
        html = '<html><style>.fake{content:"wrong"}</style><script>wrong()</script><body><p>工艺<strong>说明</strong></p><table onclick="bad()"><tr><th>参数</th><th>值</th></tr><tr><td>压力</td><td>2 &lt; 3<script>bad()</script></td></tr></table><p>结束</p></body></html>'
        document = HtmlLoader().load(self.source("sample.html", html))[0]
        self.assertNotIn("wrong", document.content)
        self.assertNotIn("bad", document.content)
        self.assertIn("工艺说明", document.content)
        text, tables = TableExtractor().extract(document.content)
        self.assertEqual(len(tables), 1)
        self.assertIn("<td>压力</td>", tables[0].content)
        self.assertIn("2 &lt; 3", tables[0].content)
        self.assertIn("结束", text)

    def test_chatml_list_and_object_are_complete_individual_cases(self):
        messages = [{"role": "system", "content": "必须引用"}, {"role": "user", "content": "问题\n补充"}, {"role": "assistant", "content": "回答"}]
        source = self.source("cases.jsonl", json.dumps(messages, ensure_ascii=False) + "\n" + json.dumps({"case_id": "explicit", "messages": messages}, ensure_ascii=False))
        documents = ChatMLLoader().load(source)
        self.assertEqual(len(documents), 2)
        self.assertTrue(all(item.metadata["category"] == "Case" and item.metadata["message_count"] == 3 for item in documents))
        self.assertIn("user: 问题\n补充\nassistant: 回答", documents[0].content)
        self.assertEqual(documents[1].metadata["case_id"], "explicit")

    def test_invalid_chatml_is_rejected_instead_of_empty_cases(self):
        for payload in (None, 1, {}, [], {"messages": {}}, {"messages": [None]}, {"messages": [{"role": [], "content": "x"}]}, {"messages": [{"role": "user", "content": []}]}, {"messages": [{"role": "user", "content": ""}]}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                ChatMLLoader().load(self.source("bad.jsonl", json.dumps(payload)))


if __name__ == "__main__":
    unittest.main()
