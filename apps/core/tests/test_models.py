from django.db import connection, models
from django.test import TestCase
from django.test.utils import isolate_apps

from apps.core.models import TimeStampedModel


class TimeStampedModelTests(TestCase):
    """Exercise the abstract base through a throwaway concrete subclass."""

    @staticmethod
    def _drop(model: type[models.Model]) -> None:
        with connection.schema_editor() as editor:
            editor.delete_model(model)

    @isolate_apps("apps.core")
    def test_stamps_on_create_and_bumps_on_save(self) -> None:
        class Thing(TimeStampedModel):
            name = models.CharField(max_length=20, default="")
            # django-stubs only synthesises `objects` for models it sees at
            # module level, so declare it for this function-local one.
            objects: models.Manager["Thing"]

            class Meta:
                app_label = "core"

        with connection.schema_editor() as editor:
            editor.create_model(Thing)
        self.addCleanup(self._drop, Thing)

        thing = Thing.objects.create(name="first")
        created, first_updated = thing.created_at, thing.updated_at
        self.assertIsNotNone(created)
        self.assertIsNotNone(first_updated)

        thing.name = "second"
        thing.save()
        thing.refresh_from_db()

        # created_at is pinned; updated_at moves.
        self.assertEqual(thing.created_at, created)
        self.assertGreater(thing.updated_at, first_updated)

    def test_is_abstract(self) -> None:
        self.assertTrue(TimeStampedModel._meta.abstract)

    def test_field_configuration(self) -> None:
        created = TimeStampedModel._meta.get_field("created_at")
        updated = TimeStampedModel._meta.get_field("updated_at")
        self.assertTrue(created.auto_now_add)
        self.assertFalse(created.auto_now)
        self.assertTrue(updated.auto_now)
        self.assertFalse(updated.auto_now_add)
