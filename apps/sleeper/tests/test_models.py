from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.sleeper.models import SyncRun


class SyncRunTests(TestCase):
    def test_track_records_success(self) -> None:
        with SyncRun.track(SyncRun.Kind.PLAYERS) as run:
            run.records_written = 7
            run.records_skipped = 3

        run.refresh_from_db()
        self.assertEqual(run.status, SyncRun.Status.SUCCESS)
        self.assertEqual(run.records_written, 7)
        self.assertEqual(run.records_skipped, 3)
        self.assertIsNotNone(run.finished_at)
        self.assertEqual(run.error, "")

    def test_track_records_failure_and_reraises(self) -> None:
        with (
            self.assertRaises(ZeroDivisionError),
            SyncRun.track(SyncRun.Kind.LEAGUE),
        ):
            raise ZeroDivisionError("nope")

        run = SyncRun.objects.get(kind=SyncRun.Kind.LEAGUE)
        self.assertEqual(run.status, SyncRun.Status.FAILED)
        self.assertIn("ZeroDivisionError", run.error)
        self.assertIn("nope", run.error)
        self.assertIsNotNone(run.finished_at)

    def test_error_text_is_truncated(self) -> None:
        run = SyncRun.objects.create(kind=SyncRun.Kind.PLAYERS)
        run.mark_failed("x" * 9000)
        run.refresh_from_db()
        self.assertEqual(len(run.error), 4000)

    def test_last_success_ignores_failures_and_other_kinds(self) -> None:
        now = timezone.now()
        SyncRun.objects.create(
            kind=SyncRun.Kind.PLAYERS,
            status=SyncRun.Status.FAILED,
            finished_at=now,
        )
        SyncRun.objects.create(
            kind=SyncRun.Kind.LEAGUE,
            status=SyncRun.Status.SUCCESS,
            finished_at=now,
        )
        older = SyncRun.objects.create(
            kind=SyncRun.Kind.PLAYERS,
            status=SyncRun.Status.SUCCESS,
            finished_at=now - timedelta(days=2),
        )
        newest = SyncRun.objects.create(
            kind=SyncRun.Kind.PLAYERS,
            status=SyncRun.Status.SUCCESS,
            finished_at=now - timedelta(hours=1),
        )

        found = SyncRun.last_success(SyncRun.Kind.PLAYERS)
        self.assertEqual(found, newest)
        self.assertNotEqual(found, older)

    def test_last_success_returns_none_when_never_run(self) -> None:
        self.assertIsNone(SyncRun.last_success(SyncRun.Kind.PLAYERS))

    def test_str_is_readable(self) -> None:
        run = SyncRun.objects.create(kind=SyncRun.Kind.PLAYERS)
        self.assertIn("players", str(run))
        self.assertIn("running", str(run))

    def test_stats_kind_available(self) -> None:
        self.assertEqual(SyncRun.Kind.STATS, "stats")
