import unittest
from types import SimpleNamespace

from app.services.dubbing_service import DubbingService


def segment(start, end, text):
    return SimpleNamespace(start=start, end=end, text=text)


class DubbingGroupEnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.service = object.__new__(DubbingService)

    def test_nested_sample_preserves_full_time_budget_and_source_rows(self):
        # Timing shape from the supplied Roadster sample, rows 181 and 182.
        rows = [segment(897, 901.86, "First fragment"),
                segment(898, 900, "second fragment.")]
        before = [vars(row).copy() for row in rows]
        groups = self.service._group_segments_for_dubbing(rows)

        self.assertEqual(len(groups), 1)
        self.assertEqual((groups[0].start, groups[0].end), (897, 901.86))
        self.assertEqual(groups[0].segment_indices, (0, 1))
        self.assertEqual(groups[0].text, "First fragment second fragment.")
        self.assertEqual([vars(row) for row in rows], before)
        self.assertTrue(self.service._natural_timeline_fits(
            groups, [6.0], 1.0, 0.32, 910, 0.0,
        ))

    def test_gap_is_measured_from_group_end_not_nested_last_row(self):
        groups = self.service._group_segments_for_dubbing([
            segment(0, 10, "First"), segment(2, 4, "nested"),
            segment(10.5, 11, "last."),
        ])
        self.assertEqual([group.segment_indices for group in groups], [(0, 1, 2)])
        self.assertEqual((groups[0].start, groups[0].end), (0, 11))

    def test_group_start_covers_out_of_order_members_without_sorting_text(self):
        groups = self.service._group_segments_for_dubbing([
            segment(4, 8, "First"), segment(2, 3, "second."),
        ])
        self.assertEqual((groups[0].start, groups[0].end), (2, 8))
        self.assertEqual(groups[0].text, "First second.")
        self.assertEqual(groups[0].segment_indices, (0, 1))

    def test_group_span_includes_earliest_start_when_adding_earlier_row(self):
        groups = self.service._group_segments_for_dubbing([
            segment(10, 11, "First"), segment(0, 1, "earlier"),
            segment(11.1, 12.2, "last."),
        ])
        self.assertEqual([group.segment_indices for group in groups], [(0, 1), (2,)])
        self.assertEqual((groups[0].start, groups[0].end), (0, 11))

    def test_overlong_single_row_is_not_extended_by_a_nested_fragment(self):
        groups = self.service._group_segments_for_dubbing([
            segment(0, 14, "Long fragment"), segment(1, 2, "nested."),
        ])
        self.assertEqual([group.segment_indices for group in groups], [(0,), (1,)])
        self.assertEqual((groups[0].start, groups[0].end), (0, 14))

    def test_exact_span_and_gap_boundaries_keep_blank_row_mapping(self):
        groups = self.service._group_segments_for_dubbing([
            segment(0, 10, "First"), segment(1, 30, "  "),
            segment(10.75, 12, "last."),
            segment(12.8, 13, "Next"), segment(13, 14, "sentence."),
        ])
        self.assertEqual([group.segment_indices for group in groups], [(0, 2), (3, 4)])
        self.assertEqual([(g.start, g.end) for g in groups], [(0, 12), (12.8, 14)])


if __name__ == "__main__":
    unittest.main()
