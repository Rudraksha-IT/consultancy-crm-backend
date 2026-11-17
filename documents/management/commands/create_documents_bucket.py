"""Create the private Documents bucket if it is missing (local development with MinIO)."""

from __future__ import annotations

from config.commands import LoggedCommand
from documents.storage import S3DocumentStorage


class Command(LoggedCommand):
    """Make sure DOCUMENTS_BUCKET exists. In AWS the bucket is made with the infrastructure."""

    help = "Create the private Documents bucket if it is missing."

    def handle(self, *args: object, **options: object) -> None:
        """Create the bucket, or say it is already there."""
        storage = S3DocumentStorage()
        made = storage.ensure_bucket()
        self.stdout.write("Documents bucket created." if made else "Documents bucket exists.")
