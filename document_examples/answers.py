"""Document Examples as the API returns them, built by hand so the storage key never leaks."""

from __future__ import annotations

from collections.abc import Iterable

from document_examples.models import DocumentExample
from documents.bin_answers import bin_answer


def example_answer(example: DocumentExample) -> dict[str, object]:
    """One example file in use: its name, what it is, its size and when it was added."""
    return {
        "id": example.pk,
        "file_name": example.file_name,
        "content_type": example.content_type,
        "size": example.size,
        "uploaded_at": example.uploaded_at.isoformat(),
    }


def examples_answer(examples: Iterable[DocumentExample]) -> list[dict[str, object]]:
    """Example files in use, oldest first."""
    return [example_answer(example) for example in examples]


def binned_example_answer(example: DocumentExample) -> dict[str, object]:
    """An example file on the Bin tab, with its Document Type, who and why."""
    document_type = example.document_type
    return {
        "kind": "document_example",
        "document_example": {"id": example.pk, "file_name": example.file_name},
        "document_type": {"id": document_type.pk, "name": document_type.name},
        **bin_answer(example),
    }
