"""Near-duplicate signatures: shingles, exact overlap scores, MinHash LSH and thresholds."""

import unittest
from unittest.mock import patch

from backend.src.storage.content_signatures import (
    CONTAINED, NEAR_DUPLICATE, SIMILAR, Overlap, Thresholds, overlap, shingles, signature, words,
)


TEXT = ("Trục Z của máy phay số 3 kêu lạ khi chạy tốc độ cao vào ca đêm. Kỹ thuật viên kiểm tra "
        "dầu bôi trơn và thấy mức dầu bình thường. Sau đó thay vòng bi phía trước nhưng tiếng kêu "
        "vẫn còn. Chưa xác nhận nguyên nhân gốc; nghi ngờ khớp nối bị mòn. Cần đo độ rung trước "
        "khi thay khớp nối.")


class ShingleTest(unittest.TestCase):
    def test_words_ignore_case_punctuation_and_turn_labels(self):
        self.assertEqual(words("[T01] Speaker A: Trục Z kêu.\n[T02] Unknown speaker: Đã thay?"),
                         ["trục", "z", "kêu", "đã", "thay"])
        self.assertEqual(shingles("[T01] Speaker A: " + TEXT), shingles("[T07] Speaker C: " + TEXT.upper()))

    def test_shingles_are_five_word_runs_and_short_text_is_one_shingle(self):
        self.assertEqual(shingles("một hai ba bốn năm sáu"), {"một hai ba bốn năm", "hai ba bốn năm sáu"})
        self.assertEqual(shingles("Trục Z kêu."), {"trục z kêu"})
        self.assertEqual(shingles(" .. "), frozenset())

    def test_overlap_scores(self):
        new, earlier = frozenset("abc"), frozenset("abcdef")
        self.assertEqual(overlap(new, earlier), Overlap(0.5, 1.0, 0.5))
        self.assertEqual(overlap(frozenset("x"), earlier), Overlap(0.0, 0.0, 0.0))


class LshTest(unittest.TestCase):
    def test_near_duplicates_share_bands_and_unrelated_text_does_not(self):
        base = signature(TEXT)
        self.assertEqual(len(base.bands), 64)
        edited = signature(TEXT + " Đã báo trưởng ca.")
        unrelated = signature("Máy ép nhựa số 7 báo lỗi nhiệt độ khuôn; đã thay cảm biến và hiệu chỉnh lại "
                              "bộ điều khiển, sau hai ngày chưa thấy lỗi lặp lại ở ca sáng.")
        self.assertGreater(len(set(base.bands) & set(edited.bands)), 30)
        self.assertEqual(set(base.bands) & set(unrelated.bands), set())
        self.assertEqual(signature(TEXT).bands, base.bands)  # stable across calls and processes
        self.assertEqual(signature("").bands, [])


class ThresholdTest(unittest.TestCase):
    def test_levels(self):
        thresholds = Thresholds()
        self.assertEqual(thresholds.level(Overlap(0.95, 0.97, 0.97)), NEAR_DUPLICATE)
        self.assertEqual(thresholds.level(Overlap(0.4, 0.95, 0.4)), CONTAINED)
        self.assertEqual(thresholds.level(Overlap(0.4, 0.4, 0.7)), SIMILAR)
        self.assertIsNone(thresholds.level(Overlap(0.3, 0.5, 0.5)))

    def test_thresholds_come_from_the_environment(self):
        with patch.dict("os.environ", {"NEAR_DUPLICATE_JACCARD": "0.8", "NEAR_DUPLICATE_CONTAINMENT": "",
                                       "NEAR_DUPLICATE_INFO": "0.5"}):
            self.assertEqual(Thresholds.from_env(), Thresholds(jaccard=0.8, containment=0.9, info=0.5))
        with patch.dict("os.environ", {"NEAR_DUPLICATE_JACCARD": "90"}), self.assertRaises(ValueError):
            Thresholds.from_env()


if __name__ == "__main__":
    unittest.main()
