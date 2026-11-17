"""What an uploaded Document may be, from its bytes, and the name it is kept under (issue 16)."""

from __future__ import annotations

import pytest

from documents.files import FILE_NAME_MAX_CHARACTERS, JPEG, PDF, PNG, detect_kind, safe_file_name


class TestDetectKind:
    @pytest.mark.parametrize(
        ("start", "kind"),
        [
            (b"%PDF-1.4\n", PDF),
            (b"\xff\xd8\xff\xe0\x00\x10JF", JPEG),
            (b"\x89PNG\r\n\x1a\n", PNG),
        ],
    )
    def test_pdfs_and_photos_are_known_by_their_first_bytes(
        self, start: bytes, kind: object
    ) -> None:
        assert detect_kind(start) == kind

    @pytest.mark.parametrize(
        "start",
        [b"", b"%PDF", b"<html>", b"<svg ", b"GIF89a", b"PK\x03\x04", b"\x89PNG", b" %PDF-1.4"],
    )
    def test_anything_else_is_unknown(self, start: bytes) -> None:
        assert detect_kind(start) is None


class TestSafeFileName:
    @pytest.mark.parametrize(
        ("name", "kind", "kept"),
        [
            ("passport.pdf", PDF, "passport.pdf"),
            ("Passport.PDF", PDF, "Passport.pdf"),
            ("scan.jpeg", JPEG, "scan.jpg"),
            ("scan.JPG", JPEG, "scan.jpg"),
            ("photo", PNG, "photo.png"),
            ("bank statement.png.pdf", PDF, "bank statement.png.pdf"),
            ("letter.html", PDF, "letter.html.pdf"),
            ("letter.pdf", PNG, "letter.pdf.png"),
        ],
    )
    def test_the_extension_always_matches_what_the_file_is(
        self, name: str, kind: object, kept: str
    ) -> None:
        assert safe_file_name(name, kind) == kept  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("name", "kept"),
        [
            ("C:\\Users\\sita\\passport.pdf", "passport.pdf"),
            ("../../etc/passwd", "passwd.pdf"),
            ('a"b<c>d|e?f*g:h.pdf', "abcdefgh.pdf"),
            ("line\nbreak\x00.pdf", "linebreak.pdf"),
            ("", "document.pdf"),
            ("...", "document.pdf"),
            (".pdf", "pdf.pdf"),
            ("  spaced  .pdf", "spaced.pdf"),
            ("नागरिकता.pdf", "नागरिकता.pdf"),
        ],
    )
    def test_folders_and_unsafe_characters_are_dropped(self, name: str, kept: str) -> None:
        assert safe_file_name(name, PDF) == kept

    def test_a_long_name_is_cut_keeping_the_extension(self) -> None:
        kept = safe_file_name("x" * 400 + ".pdf", PDF)

        assert len(kept) == FILE_NAME_MAX_CHARACTERS
        assert kept.endswith("x.pdf")
