from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from downloader_core import (
    CollectionSelectionError,
    StateStore,
    build_asset_plans,
    dedupe_works,
    discover_existing_asset,
    parse_aweme,
    safe_name,
    select_collection,
)


class ParseAwemeTests(unittest.TestCase):
    def test_album_keeps_images_and_live_photo_videos_but_ignores_slideshow_music(self):
        aweme = {
            "aweme_id": "1234567890123456789",
            "desc": "夏日/图集",
            "images": [
                {
                    "url_list": ["https://cdn/low-1", "https://cdn/high-1"],
                    "video": {"play_addr": {"url_list": ["https://cdn/live-1.mp4"]}},
                },
                {"url_list": ["https://cdn/low-2", "https://cdn/high-2"]},
            ],
            "video": {"play_addr": {"url_list": ["https://cdn/slideshow.mp4"]}},
            "music": {"play_url": {"url_list": ["https://cdn/music.mp3"]}},
        }

        work = parse_aweme(aweme)

        self.assertIsNotNone(work)
        self.assertEqual(work.kind, "album")
        self.assertEqual(
            work.urls,
            ("https://cdn/high-1", "https://cdn/live-1.mp4", "https://cdn/high-2"),
        )
        self.assertNotIn("https://cdn/slideshow.mp4", work.urls)
        self.assertNotIn("https://cdn/music.mp3", work.urls)

    def test_video_uses_play_address_when_no_album_images_exist(self):
        aweme = {
            "aweme_id": "2234567890123456789",
            "desc": "普通视频",
            "video": {
                "play_addr": {
                    "url_list": ["https://cdn/video.mp4", "https://cdn/video-backup.mp4"]
                }
            },
        }

        work = parse_aweme(aweme)

        self.assertEqual(work.kind, "video")
        self.assertEqual(work.urls, ("https://cdn/video.mp4",))


class PathPlanningTests(unittest.TestCase):
    def test_each_album_has_its_own_folder_and_numbered_image_stems(self):
        work = parse_aweme(
            {
                "aweme_id": "3234567890123456789",
                "desc": "同名作品",
                "images": [
                    {"url_list": ["https://cdn/a"]},
                    {"url_list": ["https://cdn/b"]},
                ],
            }
        )
        root = Path("downloads")

        plans = build_asset_plans(work, root)

        expected_folder = root / "图集" / "同名作品_3234567890123456789"
        self.assertEqual([p.base_path for p in plans], [expected_folder / "001", expected_folder / "002"])
        self.assertTrue(all(p.media_type == "image" for p in plans))

    def test_live_photo_video_is_stored_next_to_its_image(self):
        work = parse_aweme(
            {
                "aweme_id": "7234567890123456789",
                "desc": "动图图集",
                "images": [
                    {
                        "url_list": ["https://cdn/low-1.jpg", "https://cdn/high-1.jpg"],
                        "video": {"play_addr": {"url_list": ["https://cdn/live-1.mp4"]}},
                    },
                    {"url_list": ["https://cdn/high-2.jpg"]},
                ],
                "video": {"play_addr": {"url_list": ["https://cdn/slideshow.mp4"]}},
            }
        )

        plans = build_asset_plans(work, Path("downloads"))

        expected_folder = Path("downloads") / "图集" / "动图图集_7234567890123456789"
        self.assertEqual(
            [(p.media_type, p.base_path) for p in plans],
            [
                ("image", expected_folder / "001"),
                ("video", expected_folder / "001_动图.mp4"),
                ("image", expected_folder / "002"),
            ],
        )

    def test_same_title_with_different_ids_never_collides(self):
        root = Path("downloads")
        first = parse_aweme(
            {
                "aweme_id": "4234567890123456789",
                "desc": "同名作品",
                "images": [{"url_list": ["https://cdn/a"]}],
            }
        )
        second = parse_aweme(
            {
                "aweme_id": "5234567890123456789",
                "desc": "同名作品",
                "images": [{"url_list": ["https://cdn/b"]}],
            }
        )

        self.assertNotEqual(
            build_asset_plans(first, root)[0].base_path.parent,
            build_asset_plans(second, root)[0].base_path.parent,
        )

    def test_video_is_stored_under_video_folder_with_id_in_filename(self):
        work = parse_aweme(
            {
                "aweme_id": "6234567890123456789",
                "desc": "一个视频",
                "video": {"play_addr": {"url_list": ["https://cdn/v"]}},
            }
        )

        plan = build_asset_plans(work, Path("downloads"))[0]

        self.assertEqual(plan.base_path, Path("downloads") / "视频" / "一个视频_6234567890123456789.mp4")
        self.assertEqual(plan.media_type, "video")

    def test_safe_name_removes_windows_illegal_characters(self):
        value = safe_name(' A/B:C*D?E"F<G>H|I\n ')
        self.assertNotRegex(value, r'[\\/:*?"<>|\r\n]')
        self.assertTrue(value)

    def test_existing_image_can_be_found_regardless_of_real_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            stem = Path(tmp) / "001"
            image = stem.with_suffix(".webp")
            image.write_bytes(b"x" * 2048)
            self.assertEqual(discover_existing_asset(stem, "image", 1024), image)


class DeduplicationTests(unittest.TestCase):
    def test_duplicate_api_items_are_removed_by_work_id(self):
        one = parse_aweme(
            {
                "aweme_id": "7234567890123456789",
                "desc": "first",
                "video": {"play_addr": {"url_list": ["https://cdn/1"]}},
            }
        )
        duplicate = parse_aweme(
            {
                "aweme_id": "7234567890123456789",
                "desc": "second",
                "video": {"play_addr": {"url_list": ["https://cdn/2"]}},
            }
        )
        self.assertEqual(dedupe_works([one, duplicate]), [one])

    def test_completed_work_is_skipped_only_while_all_files_still_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = StateStore(root / "state.sqlite3")
            file_path = root / "视频" / "work.mp4"
            file_path.parent.mkdir(parents=True)
            file_path.write_bytes(b"x" * 2048)
            state.mark_complete("collection-1", "8234567890123456789", "video", "work", [file_path])

            self.assertTrue(state.is_complete("collection-1", "8234567890123456789", 1024))
            file_path.unlink()
            self.assertFalse(state.is_complete("collection-1", "8234567890123456789", 1024))
            state.close()

    def test_completed_work_is_retried_when_expected_asset_count_grows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = StateStore(root / "state.sqlite3")
            image = root / "图集" / "work" / "001.jpg"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"x" * 2048)
            state.mark_complete(
                "collection-1",
                "9234567890123456789",
                "album",
                "work",
                [image],
            )

            self.assertTrue(
                state.is_complete(
                    "collection-1",
                    "9234567890123456789",
                    1024,
                    expected_files=1,
                )
            )
            self.assertFalse(
                state.is_complete(
                    "collection-1",
                    "9234567890123456789",
                    1024,
                    expected_files=2,
                )
            )
            state.close()


class CollectionSelectionTests(unittest.TestCase):
    collections = [
        {"id": "1", "name": "音乐"},
        {"id": "2", "name": "音乐收藏"},
        {"id": "3", "name": "风景"},
    ]

    def test_exact_name_wins_over_fuzzy_matches(self):
        self.assertEqual(select_collection(self.collections, "音乐")["id"], "1")

    def test_unique_fuzzy_name_is_allowed(self):
        self.assertEqual(select_collection(self.collections, "风")["id"], "3")

    def test_ambiguous_fuzzy_name_is_rejected(self):
        with self.assertRaises(CollectionSelectionError):
            select_collection(self.collections, "音")


if __name__ == "__main__":
    unittest.main()
