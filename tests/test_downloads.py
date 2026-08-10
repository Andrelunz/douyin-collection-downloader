from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from downloader_core import AssetPlan, download_asset


class FakeResponse:
    def __init__(self, chunks, content_type="application/octet-stream", status=200):
        self._chunks = chunks
        self.headers = {"Content-Type": content_type}
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size):
        yield from self._chunks

    def close(self):
        pass


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        return self.response


class DownloadAssetTests(unittest.TestCase):
    def test_album_image_extension_follows_response_content_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = AssetPlan("123", 1, "https://cdn/no-extension", "image", Path(tmp) / "001")
            session = FakeSession(FakeResponse([b"x" * 2048], "image/webp"))

            path = download_asset(plan, session, minimum_bytes=1024)

            self.assertEqual(path.suffix, ".webp")
            self.assertEqual(path.stat().st_size, 2048)

    def test_incomplete_download_does_not_replace_existing_good_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "video.mp4"
            path.write_bytes(b"g" * 2048)
            plan = AssetPlan("123", 1, "https://cdn/video", "video", path)
            session = FakeSession(FakeResponse([b"bad"], "video/mp4"))

            result = download_asset(plan, session, minimum_bytes=1024)

            self.assertEqual(result, path)
            self.assertEqual(path.stat().st_size, 2048)
            self.assertEqual(session.calls, 0)

    def test_small_new_download_is_deleted_and_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "video.mp4"
            plan = AssetPlan("123", 1, "https://cdn/video", "video", path)
            session = FakeSession(FakeResponse([b"bad"], "video/mp4"))

            with self.assertRaises(ValueError):
                download_asset(plan, session, minimum_bytes=1024)

            self.assertFalse(path.exists())
            self.assertFalse(path.with_suffix(".mp4.part").exists())


if __name__ == "__main__":
    unittest.main()
