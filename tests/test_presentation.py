"""Tests for pure rate-limit presentation conversion."""

import unittest
from dataclasses import FrozenInstanceError
from zoneinfo import ZoneInfo

from codex_usage_tray.models import RateLimitSnapshot, RateLimitWindow
from codex_usage_tray.presentation import (
    DEFAULT_LIMIT_TITLE,
    EMPTY_LIMITS_MESSAGE,
    PresentedRateLimitWindow,
    RateLimitPresentation,
    format_remaining_percent,
    present_rate_limit_window,
    present_rate_limits,
    select_rate_limit_snapshot,
)

WARSAW = ZoneInfo("Europe/Warsaw")


class SnapshotSelectionTests(unittest.TestCase):
    def test_prefers_the_codex_limit_even_when_it_is_not_first(self) -> None:
        first = RateLimitSnapshot(
            limit_id="another-limit",
            primary=RateLimitWindow(10, 60),
        )
        codex = RateLimitSnapshot(
            limit_id="codex",
            primary=RateLimitWindow(20, 300),
        )

        self.assertIs(select_rate_limit_snapshot((first, codex)), codex)

    def test_falls_back_to_the_first_snapshot(self) -> None:
        first = RateLimitSnapshot(
            limit_id="first-limit",
            primary=RateLimitWindow(10, 60),
        )
        second = RateLimitSnapshot(
            limit_id="second-limit",
            primary=RateLimitWindow(20, 300),
        )

        self.assertIs(select_rate_limit_snapshot((first, second)), first)

    def test_handles_an_empty_snapshot_collection(self) -> None:
        presentation = present_rate_limits((), WARSAW)

        self.assertEqual(
            presentation,
            RateLimitPresentation(
                limit_id=None,
                title=DEFAULT_LIMIT_TITLE,
                windows=(),
                empty_message=EMPTY_LIMITS_MESSAGE,
            ),
        )


class WindowPresentationTests(unittest.TestCase):
    def test_presents_a_primary_window_only(self) -> None:
        snapshot = RateLimitSnapshot(
            limit_id="codex",
            primary=RateLimitWindow(10, 60),
        )

        presentation = present_rate_limits((snapshot,), WARSAW)

        self.assertEqual(len(presentation.windows), 1)
        self.assertEqual(presentation.windows[0].duration_label, "1 hour")

    def test_presents_a_secondary_window_only(self) -> None:
        snapshot = RateLimitSnapshot(
            limit_id="codex",
            secondary=RateLimitWindow(20, 120),
        )

        presentation = present_rate_limits((snapshot,), WARSAW)

        self.assertEqual(len(presentation.windows), 1)
        self.assertEqual(presentation.windows[0].duration_label, "2 hours")

    def test_presents_both_available_windows(self) -> None:
        snapshot = RateLimitSnapshot(
            limit_id="codex",
            primary=RateLimitWindow(10, 60),
            secondary=RateLimitWindow(20, 120),
        )

        presentation = present_rate_limits((snapshot,), WARSAW)

        self.assertEqual(
            tuple(window.duration_label for window in presentation.windows),
            ("1 hour", "2 hours"),
        )

    def test_derives_seven_day_duration_from_minutes(self) -> None:
        window = present_rate_limit_window(RateLimitWindow(4, 10_080), WARSAW)

        self.assertEqual(window.duration_label, "7 days")

    def test_derives_five_hour_duration_from_minutes(self) -> None:
        window = present_rate_limit_window(RateLimitWindow(4, 300), WARSAW)

        self.assertEqual(window.duration_label, "5 hours")

    def test_does_not_assign_semantics_from_window_position(self) -> None:
        snapshot = RateLimitSnapshot(
            limit_id="codex",
            primary=RateLimitWindow(4, 10_080),
            secondary=RateLimitWindow(5, 300),
        )

        presentation = present_rate_limits((snapshot,), WARSAW)

        self.assertEqual(presentation.windows[0].duration_label, "7 days")
        self.assertEqual(presentation.windows[1].duration_label, "5 hours")


class PercentagePresentationTests(unittest.TestCase):
    def test_formats_whole_percentage_without_a_decimal_suffix(self) -> None:
        self.assertEqual(format_remaining_percent(90.0), "90%")

    def test_preserves_meaningful_fractional_percentage(self) -> None:
        self.assertEqual(format_remaining_percent(74.5), "74.5%")

    def test_formats_clamped_percentages(self) -> None:
        fully_remaining = present_rate_limit_window(
            RateLimitWindow(used_percent=-10, window_duration_mins=300),
            WARSAW,
        )
        fully_used = present_rate_limit_window(
            RateLimitWindow(used_percent=150, window_duration_mins=300),
            WARSAW,
        )

        self.assertEqual(fully_remaining.remaining_percent, 100.0)
        self.assertEqual(fully_remaining.remaining_percent_text, "100%")
        self.assertEqual(fully_used.remaining_percent, 0.0)
        self.assertEqual(fully_used.remaining_percent_text, "0%")


class ResetTimePresentationTests(unittest.TestCase):
    def test_formats_reset_time_in_europe_warsaw(self) -> None:
        window = present_rate_limit_window(
            RateLimitWindow(4, 10_080, 1_784_889_359),
            WARSAW,
        )

        self.assertEqual(window.reset_time_text, "2026-07-24 12:35:59 CEST")

    def test_handles_a_missing_reset_timestamp(self) -> None:
        window = present_rate_limit_window(RateLimitWindow(4, 300), WARSAW)

        self.assertIsNone(window.reset_time_text)


class TimezoneValidationTests(unittest.TestCase):
    def test_window_rejects_none_without_a_reset_timestamp(self) -> None:
        with self.assertRaises(TypeError) as context:
            present_rate_limit_window(
                RateLimitWindow(4, 300),
                None,  # type: ignore[arg-type]
            )

        self.assertEqual(
            str(context.exception),
            "local_timezone must be a tzinfo instance",
        )

    def test_empty_snapshot_collection_rejects_none(self) -> None:
        with self.assertRaises(TypeError) as context:
            present_rate_limits((), None)  # type: ignore[arg-type]

        self.assertEqual(
            str(context.exception),
            "local_timezone must be a tzinfo instance",
        )

    def test_snapshot_without_windows_rejects_none(self) -> None:
        with self.assertRaises(TypeError) as context:
            present_rate_limits(
                (RateLimitSnapshot(limit_id="codex"),),
                None,  # type: ignore[arg-type]
            )

        self.assertEqual(
            str(context.exception),
            "local_timezone must be a tzinfo instance",
        )

    def test_rejects_another_non_timezone_value(self) -> None:
        with self.assertRaises(TypeError) as context:
            present_rate_limits((), "Europe/Warsaw")  # type: ignore[arg-type]

        self.assertEqual(
            str(context.exception),
            "local_timezone must be a tzinfo instance",
        )


class PresentationValueTests(unittest.TestCase):
    def test_handles_a_missing_limit_name_and_identifier(self) -> None:
        snapshot = RateLimitSnapshot(primary=RateLimitWindow(4, 300))

        presentation = present_rate_limits((snapshot,), WARSAW)

        self.assertIsNone(presentation.limit_id)
        self.assertEqual(presentation.title, DEFAULT_LIMIT_TITLE)
        self.assertIsNone(presentation.empty_message)

    def test_presentation_objects_are_immutable(self) -> None:
        window = PresentedRateLimitWindow(
            duration_label="5 hours",
            remaining_percent=90.0,
            remaining_percent_text="90%",
            reset_time_text=None,
        )
        presentation = RateLimitPresentation(
            limit_id="codex",
            title="codex",
            windows=(window,),
            empty_message=None,
        )

        with self.assertRaises(FrozenInstanceError):
            presentation.title = "changed"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            window.duration_label = "changed"  # type: ignore[misc]
        with self.assertRaises(AttributeError):
            presentation.windows.append(window)  # type: ignore[attr-defined]


if __name__ == "__main__":
    unittest.main()
