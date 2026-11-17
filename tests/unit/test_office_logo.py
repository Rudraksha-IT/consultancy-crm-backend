"""The office logo's checks (spec 99c): what it may be, and how an SVG is made safe."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest

from tenancy.logo import (
    CHOOSE_LOGO_MESSAGE,
    JPEG,
    LOGO_KIND_MESSAGE,
    LOGO_TOO_BIG_MESSAGE,
    MAX_LOGO_BYTES,
    PNG,
    SVG,
    WEBP,
    LogoRefusedError,
    check_logo,
    sanitise_svg,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32
WEBP_BYTES = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32
SVG_NS = "http://www.w3.org/2000/svg"
SIMPLE_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40">'
    b'<rect width="40" height="40" rx="9" fill="#0b3d5c"/>'
    b'<path d="M20 7v26" stroke="#fff" stroke-width="2.6"/></svg>'
)


def parsed(svg: bytes) -> ET.Element:
    """Parse our own output."""
    return ET.fromstring(svg)  # noqa: S314  # our own sanitised output


def tags_of(svg: bytes) -> set[str]:
    """Every element name in the SVG, without its namespace."""
    return {element.tag.split("}")[-1] for element in parsed(svg).iter()}


def attributes_of(svg: bytes) -> set[str]:
    """Every attribute name in the SVG."""
    return {name for element in parsed(svg).iter() for name in element.attrib}


class TestCheckLogo:
    @pytest.mark.parametrize(
        ("content", "kind"),
        [(PNG_BYTES, PNG), (JPEG_BYTES, JPEG), (WEBP_BYTES, WEBP)],
    )
    def test_images_are_known_by_their_bytes(self, content: bytes, kind: object) -> None:
        checked = check_logo(content)

        assert checked.kind == kind
        assert checked.content == content

    def test_an_svg_is_sanitised(self) -> None:
        checked = check_logo(SIMPLE_SVG)

        assert checked.kind == SVG
        assert tags_of(checked.content) == {"svg", "rect", "path"}

    def test_the_types_and_extensions(self) -> None:
        assert (PNG.content_type, PNG.extension) == ("image/png", ".png")
        assert (JPEG.content_type, JPEG.extension) == ("image/jpeg", ".jpg")
        assert (WEBP.content_type, WEBP.extension) == ("image/webp", ".webp")
        assert (SVG.content_type, SVG.extension) == ("image/svg+xml", ".svg")

    @pytest.mark.parametrize(
        "content",
        [
            b"%PDF-1.7 a document",
            b"GIF89a" + b"\x00" * 20,
            b"RIFF\x24\x00\x00\x00WAVEfmt ",
            b"just some text",
            b"<html><script>alert(1)</script></html>",
            b"<svg",
            b"\xff\xfe\x00<\x00s",
        ],
    )
    def test_anything_else_is_refused(self, content: bytes) -> None:
        with pytest.raises(LogoRefusedError, match=re.escape(LOGO_KIND_MESSAGE)):
            check_logo(content)

    def test_an_empty_file_is_refused(self) -> None:
        with pytest.raises(LogoRefusedError, match=re.escape(CHOOSE_LOGO_MESSAGE)):
            check_logo(b"")

    def test_at_most_1_mb(self) -> None:
        check_logo(PNG_BYTES + b"\x00" * (MAX_LOGO_BYTES - len(PNG_BYTES)))

        with pytest.raises(LogoRefusedError, match=re.escape(LOGO_TOO_BIG_MESSAGE)):
            check_logo(PNG_BYTES + b"\x00" * (MAX_LOGO_BYTES - len(PNG_BYTES) + 1))

    def test_1_mb_is_1024_kb(self) -> None:
        assert MAX_LOGO_BYTES == 1024 * 1024


class TestSanitiseSvg:
    def test_keeps_a_plain_drawing(self) -> None:
        clean = sanitise_svg(SIMPLE_SVG)

        assert parsed(clean).get("viewBox") == "0 0 40 40"
        rect = parsed(clean).find(f"{{{SVG_NS}}}rect")
        assert rect is not None
        assert rect.get("fill") == "#0b3d5c"

    @pytest.mark.parametrize(
        "element",
        [
            "<script>alert(1)</script>",
            '<foreignObject><div xmlns="http://www.w3.org/1999/xhtml">x</div></foreignObject>',
            '<image href="https://evil.test/x.png"/>',
            '<a href="javascript:alert(1)"><rect width="1" height="1"/></a>',
            '<animate attributeName="href" to="javascript:alert(1)"/>',
            '<set attributeName="onclick" to="alert(1)"/>',
            '<iframe src="https://evil.test"/>',
            '<metadata><x xmlns="urn:x">y</x></metadata>',
        ],
    )
    def test_drops_unsafe_elements_with_their_content(self, element: str) -> None:
        svg = f'<svg xmlns="{SVG_NS}"><rect width="2" height="2"/>{element}</svg>'.encode()

        clean = sanitise_svg(svg)

        assert tags_of(clean) == {"svg", "rect"}
        assert b"alert" not in clean
        assert b"evil" not in clean

    @pytest.mark.parametrize(
        "attribute",
        [
            'onload="alert(1)"',
            'onclick="alert(1)"',
            'style="background:url(https://evil.test/x)"',
            'href="https://evil.test/x"',
            'xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="javascript:alert(1)"',
            'fill="url(https://evil.test/#x)"',
            'data-x="1"',
        ],
    )
    def test_drops_unsafe_attributes(self, attribute: str) -> None:
        svg = f'<svg xmlns="{SVG_NS}"><rect width="2" height="2" {attribute}/></svg>'.encode()

        clean = sanitise_svg(svg)

        assert attributes_of(clean) == {"width", "height"}
        assert b"alert" not in clean
        assert b"evil" not in clean

    def test_keeps_local_references(self) -> None:
        svg = (
            f'<svg xmlns="{SVG_NS}" xmlns:xlink="http://www.w3.org/1999/xlink">'
            '<defs><linearGradient id="g"><stop offset="0" stop-color="#fff"/></linearGradient>'
            '<path id="p" d="M0 0h1"/></defs>'
            '<rect width="2" height="2" fill="url(#g)"/><use xlink:href="#p"/><use href="#p"/>'
            "</svg>"
        ).encode()

        clean = sanitise_svg(svg)

        assert b'fill="url(#g)"' in clean
        assert clean.count(b'href="#p"') == 2

    def test_keeps_safe_inline_style(self) -> None:
        svg = f'<svg xmlns="{SVG_NS}"><rect style="fill:#123456;opacity:0.5"/></svg>'.encode()

        clean = sanitise_svg(svg)

        assert b'style="fill:#123456;opacity:0.5"' in clean

    def test_keeps_a_style_sheet_without_outside_links(self) -> None:
        svg = (
            f'<svg xmlns="{SVG_NS}"><style>.cls-1{{fill:#0b3d5c}}</style>'
            '<rect class="cls-1" width="2" height="2"/></svg>'
        ).encode()

        clean = sanitise_svg(svg)

        assert tags_of(clean) == {"svg", "style", "rect"}
        assert b"cls-1{fill:#0b3d5c}" in clean

    @pytest.mark.parametrize(
        "css",
        [
            "@import url(https://evil.test/x.css);",
            ".a{background:url(https://evil.test/x)}",
            ".a{behavior:expression(alert(1))}",
            ".a{fill:\\75rl(x)}",
        ],
    )
    def test_drops_a_style_sheet_that_reaches_outside(self, css: str) -> None:
        svg = f'<svg xmlns="{SVG_NS}"><style>{css}</style><rect/></svg>'.encode()

        clean = sanitise_svg(svg)

        assert tags_of(clean) == {"svg", "rect"}
        assert b"evil" not in clean
        assert b"expression" not in clean

    @pytest.mark.parametrize(
        "svg",
        [
            b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "aaaa">]>'
            b'<svg xmlns="http://www.w3.org/2000/svg"><text>&a;</text></svg>',
            b'<!DOCTYPE svg SYSTEM "file:///etc/passwd"><svg xmlns="http://www.w3.org/2000/svg"/>',
            b'<svg xmlns="http://www.w3.org/2000/svg"><rect></svg>',
            b'<html xmlns="http://www.w3.org/2000/svg"/>',
            b"<svg/>",
            b"\xff\xfe not utf-8 \x80",
        ],
    )
    def test_refuses_what_is_not_a_plain_svg(self, svg: bytes) -> None:
        with pytest.raises(LogoRefusedError):
            sanitise_svg(svg)

    def test_keeps_text_and_drops_comments(self) -> None:
        svg = f'<svg xmlns="{SVG_NS}"><!-- made by x --><text x="1">PE</text></svg>'.encode()

        clean = sanitise_svg(svg)

        assert b"PE" in clean
        assert b"made by" not in clean

    def test_a_bom_and_an_xml_declaration_are_fine(self) -> None:
        svg = b'\xef\xbb\xbf<?xml version="1.0" encoding="UTF-8"?>\n' + SIMPLE_SVG

        assert tags_of(sanitise_svg(svg)) == {"svg", "rect", "path"}

    def test_refuses_a_file_nested_deeper_than_any_logo(self) -> None:
        svg = f'<svg xmlns="{SVG_NS}">{"<g>" * 60}{"</g>" * 60}</svg>'.encode()

        with pytest.raises(LogoRefusedError, match=re.escape(LOGO_KIND_MESSAGE)):
            sanitise_svg(svg)

    def test_keeps_only_known_xml_space_values(self) -> None:
        kept = sanitise_svg(
            f'<svg xmlns="{SVG_NS}"><text xml:space="preserve">A</text></svg>'.encode()
        )
        dropped = sanitise_svg(f'<svg xmlns="{SVG_NS}"><text xml:space="x">A</text></svg>'.encode())

        assert b'xml:space="preserve"' in kept
        assert b"xml:space" not in dropped
