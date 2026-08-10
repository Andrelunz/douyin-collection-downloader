from __future__ import annotations

import unittest

from douyin_api import extract_collections, fetch_collection_works


class FakePage:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []

    def fetch_json(self, url):
        self.urls.append(url)
        return self.responses.pop(0)


class DouyinApiTests(unittest.TestCase):
    def test_extract_collections_trims_names_and_uses_stable_id(self):
        data = {
            "collects_list": [
                {"collects_id": "123", "collects_name": " 风景\r\n", "total_number": 12},
            ]
        }
        self.assertEqual(
            extract_collections(data),
            [{"id": "123", "name": "风景", "count": 12}],
        )

    def test_collection_pagination_deduplicates_awemes(self):
        first = {
            "aweme_list": [
                {
                    "aweme_id": "1234567890123456789",
                    "desc": "one",
                    "video": {"play_addr": {"url_list": ["https://cdn/1"]}},
                }
            ],
            "has_more": True,
            "cursor": 18,
        }
        second = {
            "aweme_list": [
                {
                    "aweme_id": "1234567890123456789",
                    "desc": "duplicate",
                    "video": {"play_addr": {"url_list": ["https://cdn/dup"]}},
                },
                {
                    "aweme_id": "2234567890123456789",
                    "desc": "album",
                    "images": [{"url_list": ["https://cdn/img"]}],
                },
            ],
            "has_more": False,
            "cursor": 36,
        }
        page = FakePage([first, second])

        works, _ = fetch_collection_works(page, "collection-id", page_delay=0)

        self.assertEqual([w.work_id for w in works], ["1234567890123456789", "2234567890123456789"])
        self.assertIn("cursor=0", page.urls[0])
        self.assertIn("cursor=18", page.urls[1])

    def test_repeated_cursor_stops_instead_of_looping_forever(self):
        page = FakePage(
            [
                {"aweme_list": [], "has_more": True, "cursor": 0},
            ]
        )
        works, _ = fetch_collection_works(page, "id", page_delay=0)
        self.assertEqual(works, [])
        self.assertEqual(len(page.urls), 1)


if __name__ == "__main__":
    unittest.main()
