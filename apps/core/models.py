from django.db import models


class TimeStampedModel(models.Model):
    """Abstract base adding self-maintaining created/updated timestamps.

    ``updated_at`` uses ``auto_now``, so it only advances on ``save()``. Bulk
    operations such as ``queryset.update()`` and ``bulk_update()`` bypass it —
    the sync services must set it explicitly when they use those paths.
    """

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
