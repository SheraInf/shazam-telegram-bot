import unittest
from core import keyboard, label, media_url, normalize


class CoreTests(unittest.TestCase):
    def test_supported_urls(self):
        for url in ["https://www.youtube.com/watch?v=abcdefghijk", "https://vm.tiktok.com/abc",
                    "https://www.instagram.com/reel/abc/"]:
            self.assertEqual(media_url(url), url)

    def test_reject_local_or_disguised_urls(self):
        for url in ["http://youtube.com/x", "https://127.0.0.1/x", "file:///etc/passwd",
                    "https://youtube.com.evil.test/x", "https://youtube.com@evil.test/x",
                    "https://evil.test@youtube.com/x", "https://youtube.com:8000/x",
                    "https://localhost/x", "https://youtube.com:invalid/x"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                media_url(url)

    def test_labels(self):
        self.assertEqual(label({"title": "Six Days", "duration": 233}), "• 3:53 • Six Days")
        self.assertEqual(label({"title": "Song", "duration": None}), "Song")
        self.assertLessEqual(len(label({"title": "Я" * 200, "duration": 233})), 200)
        self.assertEqual(label({"title": "a\nb", "duration": float("nan")}), "a b")

    def test_filter_and_deduplicate(self):
        tracks = normalize([{"id": "abcdefghijk", "title": "Song"},
                            {"id": "abcdefghijk", "title": "Duplicate"},
                            {"id": "abcdefghij1", "live_status": "is_live"},
                            {"id": "file:///secret", "title": "Invalid"}])
        self.assertEqual([t["title"] for t in tracks], ["Song"])

    def test_pages_and_callback_limits(self):
        tracks = [{"id": str(i), "title": f"Track {i}", "duration": 10} for i in range(25)]
        first = keyboard("123456abcdef", tracks, 0, True)["inline_keyboard"]
        self.assertEqual(len(first), 14)
        self.assertEqual(first[-2][0]["style"], "success")
        self.assertEqual(first[-2][0]["callback_data"], "more:123456abcdef:1")
        self.assertEqual(first[-1][0]['switch_inline_query_current_chat'], '')
        second = keyboard("123456abcdef", tracks, 1, True)["inline_keyboard"]
        self.assertEqual(second[0][0]["callback_data"], "play:123456abcdef:12")
        self.assertEqual(len(keyboard("123456abcdef", tracks, 2, False)["inline_keyboard"]), 2)
        for row in first + second:
            if 'callback_data' in row[0]:
                self.assertLessEqual(len(row[0]["callback_data"].encode()), 64)

    def test_youtube_music_metadata(self):
        result = normalize([{'videoId':'W3_c4-4OMbM','title':'Six Days', 'artists':[{'name':'DJ Shadow'}],
                             'duration':'3:53','duration_seconds':233}])[0]
        self.assertEqual(label(result),'• 3:53 • Six Days — DJ Shadow')


if __name__ == "__main__":
    unittest.main()
