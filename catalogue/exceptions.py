from __future__ import annotations


class DuplicateNameError(Exception):
    """Another University (or another Course at the same University) already has this name."""


class NotArchivedError(Exception):
    """Only a University or Course saved as archived can be deleted (issue 92)."""


class InUseError(Exception):
    """Something still uses this Catalogue entry, so it stays archived (issue 92)."""

    def __init__(self, count: int, what: str) -> None:
        super().__init__(count, what)
        self.count = count
        self.what = what


class KeepsFilesError(Exception):
    """A Course here still has Course Files (even in the Bin), so it stays archived (spec 60)."""

    def __init__(self, count: int) -> None:
        super().__init__(count)
        self.count = count
