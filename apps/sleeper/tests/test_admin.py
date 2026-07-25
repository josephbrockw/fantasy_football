from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory, TestCase

from apps.sleeper.admin import SyncRunAdmin
from apps.sleeper.models import SyncRun


class SyncRunAdminTests(TestCase):
    def setUp(self) -> None:
        self.admin = SyncRunAdmin(SyncRun, AdminSite())
        self.request = RequestFactory().get("/admin/")

    def test_sync_runs_cannot_be_created_by_hand(self) -> None:
        """They are an audit trail — only the sync services write them."""
        self.assertFalse(self.admin.has_add_permission(self.request))

    def test_every_field_is_read_only(self) -> None:
        self.assertEqual(
            set(self.admin.readonly_fields),
            {f.name for f in SyncRun._meta.fields},
        )
