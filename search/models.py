"""What the global search keeps: only how many searches each person made this minute."""

from __future__ import annotations

from django.db import models

SEARCHER_MAX_LENGTH = 40


class SearchAllowance(models.Model):
    """One searcher's count in the current minute, so a script can't page through everyone.

    `searcher` is "staff:<id>", or "platform:<id>" for a Platform Admin in Support Access. The
    search text itself is never stored.
    """

    searcher = models.CharField(max_length=SEARCHER_MAX_LENGTH, unique=True)
    window_started_at = models.DateTimeField()
    used = models.PositiveSmallIntegerField(default=0)

    def __str__(self) -> str:
        return self.searcher
