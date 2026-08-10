from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from downloader_core import StateStore, safe_name
from reorganizer import (
    extract_work_id,
    reorganize_by_author,
    scan_local_files,
)


class WorkIdExtractionTests(unittest.TestCase):
    def test_extracts_id_from_video_filename(self):
        self.assertEqual(
            extract_work_id("男生变帅很简单_7397702039507143986.mp4"),
            "7397702039507143986",
        )

    def test_extracts_id_from_album_folder_name(self):
        self.assertEqual(
            extract_work_id("想要奖励吗_cos_枣子姐_7634431189185036794"),
            "7634431189185036794",
        )

    def test_returns_none_when_no_19_digit_id(self):
        self.assertIsNone(extract_work_id("no_video_id_here.mp4"))


class ScanLocalFilesTests(unittest.TestCase):
    def test_scans_videos_and_albums(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "视频").mkdir()
            (root / "图集").mkdir()
            video = root / "视频" / f"test_7234567890123456789.mp4"
            video.write_bytes(b"x" * 2048)
            album = root / "图集" / f"album_8234567890123456789"
            album.mkdir()
            (album / "001.jpg").write_bytes(b"y" * 2048)
            (album / "001_动图.mp4").write_bytes(b"z" * 2048)

            result = scan_local_files(root)

            self.assertEqual(
                sorted(wid for wid, _ in result),
                [
                    "7234567890123456789",
                    "8234567890123456789",
                    "8234567890123456789",
                ],
            )


class ReorganizeByAuthorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.video_dir = self.root / "视频"
        self.album_dir = self.root / "图集"
        self.state_path = self.root / "state.sqlite3"

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def _make_video(self, work_id: str, title: str) -> Path:
        self.video_dir.mkdir(exist_ok=True)
        path = self.video_dir / f"{safe_name(title)}_{work_id}.mp4"
        path.write_bytes(b"v" * 2048)
        return path

    def _make_album(self, work_id: str, title: str, image_count: int = 2) -> list[Path]:
        folder = self.album_dir / f"{safe_name(title)}_{work_id}"
        folder.mkdir(parents=True, exist_ok=True)
        paths = []
        for i in range(1, image_count + 1):
            p = folder / f"{i:03d}.jpg"
            p.write_bytes(b"a" * 2048)
            paths.append(p)
        return paths

    def _make_live_album(self, work_id: str, title: str) -> list[Path]:
        folder = self.album_dir / f"{safe_name(title)}_{work_id}"
        folder.mkdir(parents=True, exist_ok=True)
        image = folder / "001.jpg"
        live_video = folder / "001_动图.mp4"
        image.write_bytes(b"a" * 2048)
        live_video.write_bytes(b"v" * 2048)
        return [image, live_video]

    def _seed_state(self, work_id: str, kind: str, title: str, paths: list[Path]):
        state = StateStore(self.state_path)
        state.mark_complete("test-collection", work_id, kind, title, paths)
        state.close()

    def test_author_with_two_or_fewer_works_is_not_moved(self):
        author_map = {"1111111111111111111": "小明", "2222222222222222222": "小明"}
        paths = [
            self._make_video("1111111111111111111", "one"),
            self._make_video("2222222222222222222", "two"),
        ]
        self._seed_state("1111111111111111111", "video", "one", [paths[0]])
        self._seed_state("2222222222222222222", "video", "two", [paths[1]])
        state = StateStore(self.state_path)

        result = reorganize_by_author(self.root, author_map, state, "test-collection")

        self.assertEqual(result.get("小明", 0), 0, "authors with ≤2 works should not be moved")
        self.assertTrue(paths[0].exists(), "files should stay in place")

    def test_album_with_many_images_counts_as_one_work(self):
        """An album with 5 images is 1 work, not 5; so 1 album + 1 video = 2 works, stays."""
        author_map = {"1111111111111111111": "小红", "2222222222222222222": "小红"}
        v = self._make_video("1111111111111111111", "video")
        imgs = self._make_album("2222222222222222222", "album", image_count=5)
        self._seed_state("1111111111111111111", "video", "video", [v])
        self._seed_state("2222222222222222222", "album", "album", imgs)
        state = StateStore(self.state_path)

        result = reorganize_by_author(self.root, author_map, state, "test-collection")

        self.assertEqual(result.get("小红", 0), 0,
                         "1 album(5 images) + 1 video = 2 works, should not move")
        self.assertTrue(v.exists(), "files should stay")

    def test_album_plus_two_videos_counts_as_three_works_and_moves(self):
        """1 album (any image count) + 2 videos = 3 works, should move."""
        author_map = {
            "1111111111111111111": "小刚",
            "2222222222222222222": "小刚",
            "3333333333333333333": "小刚",
        }
        v1 = self._make_video("1111111111111111111", "a")
        v2 = self._make_video("2222222222222222222", "b")
        imgs = self._make_album("3333333333333333333", "c", image_count=5)
        self._seed_state("1111111111111111111", "video", "a", [v1])
        self._seed_state("2222222222222222222", "video", "b", [v2])
        self._seed_state("3333333333333333333", "album", "c", imgs)
        state = StateStore(self.state_path)

        result = reorganize_by_author(self.root, author_map, state, "test-collection")

        self.assertGreaterEqual(result.get("小刚", 0), 3)
        author_dir = self.root / "作者" / safe_name("小刚")
        self.assertTrue(author_dir.is_dir())
        # All 3 works moved (1+1+5=7 files)
        self.assertEqual(len(list(author_dir.iterdir())), 7)

    def test_author_with_three_works_is_moved_and_album_files_get_flattened(self):
        author_map = {
            "3333333333333333333": "小华",
            "4444444444444444444": "小华",
            "5555555555555555555": "小华",
        }
        v1 = self._make_video("3333333333333333333", "a")
        v2 = self._make_video("4444444444444444444", "b")
        imgs = self._make_album("5555555555555555555", "c", image_count=2)
        self._seed_state("3333333333333333333", "video", "a", [v1])
        self._seed_state("4444444444444444444", "video", "b", [v2])
        self._seed_state("5555555555555555555", "album", "c", imgs)
        state = StateStore(self.state_path)

        result = reorganize_by_author(self.root, author_map, state, "test-collection")

        self.assertGreaterEqual(result.get("小华", 0), 3)
        author_dir = self.root / "作者" / safe_name("小华")
        self.assertTrue(author_dir.is_dir())
        moved_names = {p.name for p in author_dir.iterdir() if p.is_file()}
        self.assertIn("a_3333333333333333333.mp4", moved_names)
        self.assertIn("b_4444444444444444444.mp4", moved_names)
        self.assertIn("c_5555555555555555555_001.jpg", moved_names)
        self.assertIn("c_5555555555555555555_002.jpg", moved_names)
        # Album folder should be cleaned up
        self.assertFalse(
            (self.album_dir / f"c_5555555555555555555").exists(),
            "empty album folder should be removed",
        )

    def test_live_album_counts_as_one_work_and_moves_all_assets(self):
        author_map = {
            "6666666666666666666": "小青",
            "7777777777777777777": "小青",
            "8888888888888888888": "小青",
        }
        v1 = self._make_video("6666666666666666666", "a")
        v2 = self._make_video("7777777777777777777", "b")
        live_assets = self._make_live_album("8888888888888888888", "live")
        self._seed_state("6666666666666666666", "video", "a", [v1])
        self._seed_state("7777777777777777777", "video", "b", [v2])
        self._seed_state("8888888888888888888", "album", "live", live_assets)
        state = StateStore(self.state_path)

        result = reorganize_by_author(self.root, author_map, state, "test-collection")

        self.assertEqual(result.get("小青"), 3)
        author_dir = self.root / "作者" / safe_name("小青")
        moved_names = {p.name for p in author_dir.iterdir() if p.is_file()}
        self.assertIn("live_8888888888888888888_001.jpg", moved_names)
        self.assertIn("live_8888888888888888888_001_动图.mp4", moved_names)
        self.assertFalse((self.album_dir / "live_8888888888888888888").exists())
        kind = state._connection.execute(
            "SELECT kind FROM completed_works WHERE collection_id = ? AND work_id = ?",
            ("test-collection", "8888888888888888888"),
        ).fetchone()
        self.assertEqual(kind[0], "album")

    def test_same_size_different_content_is_renamed_instead_of_deleted(self):
        author_map = {
            "1111111111111111111": "小白",
            "2222222222222222222": "小白",
            "3333333333333333333": "小白",
        }
        source = self._make_video("1111111111111111111", "same")
        other1 = self._make_video("2222222222222222222", "b")
        other2 = self._make_video("3333333333333333333", "c")
        self._seed_state("1111111111111111111", "video", "same", [source])
        self._seed_state("2222222222222222222", "video", "b", [other1])
        self._seed_state("3333333333333333333", "video", "c", [other2])
        author_dir = self.root / "作者" / safe_name("小白")
        author_dir.mkdir(parents=True)
        existing = author_dir / source.name
        existing.write_bytes(b"x" * source.stat().st_size)
        state = StateStore(self.state_path)

        reorganize_by_author(self.root, author_map, state, "test-collection")

        self.assertEqual(existing.read_bytes(), b"x" * 2048)
        renamed = author_dir / f"{source.stem}_1{source.suffix}"
        self.assertTrue(renamed.exists())
        self.assertEqual(renamed.read_bytes(), b"v" * 2048)


if __name__ == "__main__":
    unittest.main()
