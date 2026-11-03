import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify_industry_collection import validate_reference_urls


class SourceReferenceTests(unittest.TestCase):
    def test_opaque_google_figure_is_preview_only(self):
        row = {"source_page": "https://example.org/manual.pdf", "original_url": "x-raw-image:///" + "a" * 64,
               "saved_kind": "google_preview", "saved_from": "inline Google thumbnail"}
        self.assertEqual(validate_reference_urls(row), "non_http_google_reference_preview_only")
        for replacement in ({"saved_kind": "source_image"}, {"original_url": "file:///private/example"},
                            {"source_page": "http://127.0.0.1/private"}, {"saved_from": "https://example.org/unknown.png"}):
            with self.assertRaises(ValueError):
                validate_reference_urls({**row, **replacement})


if __name__ == '__main__':
    unittest.main()
