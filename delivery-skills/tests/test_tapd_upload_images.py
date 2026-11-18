"""Offline checks for the public screenshot helper; no network or credentials required."""

from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tapd-wrapup" / "scripts"))
from tapd_upload_images import NoRedirect, endpoint, upload


class ScreenshotUpload(unittest.TestCase):
    def test_only_a_trusted_https_base_can_be_used(self):
        self.assertEqual(endpoint("https://example.invalid/v1/"), "https://example.invalid/v1/files/upload_image")
        for base in ("https://user:password@example.invalid", "http://example.invalid", "file:///tmp/base", "https://example.invalid/?token=x"):
            with self.subTest(base=base), self.assertRaises(ValueError):
                endpoint(base)
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.invalid/else"))

    def test_preserves_per_image_receipts_and_sends_binary_with_the_right_fields(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            files = [root / name for name in ("first.png", "missing.png", "empty.png", "last.png")]
            for file in (files[0], files[2], files[3]):
                file.write_bytes(b"\x89PNG\r\n\x1a\n")
            calls = []
            def remote(req, timeout):
                calls.append(req)
                self.assertEqual(timeout, 60)
                self.assertIn(b'name="workspace_id"\r\n\r\n123\r\n', req.data)
                self.assertIn(b'name="image"; filename="selftest-', req.data)
                self.assertIn(b'\x89PNG\r\n\x1a\n', req.data)
                self.assertEqual(req.get_header("Authorization"), "Bearer fixture")
                if len(calls) == 3:
                    raise HTTPError(req.full_url, 403, "forbidden", {}, BytesIO(b"irrelevant private detail"))
                src = "/tfl/pictures/synthetic.png" if len(calls) == 1 else "/tfl/"
                return BytesIO(('{"data":{"image_src":"' + src + '"}}').encode())
            result = upload(files, "123", endpoint("https://example.invalid"), "fixture", remote)
            self.assertEqual([row["file"] for row in result], [file.name for file in files])
            self.assertEqual([row["image_src"] for row in result], ["/tfl/pictures/synthetic.png", None, None, None])
            self.assertIn("403", result[-1]["info"])
            self.assertNotIn("irrelevant private detail", repr(result))
            self.assertEqual(3, len(calls))


if __name__ == "__main__":
    unittest.main()
