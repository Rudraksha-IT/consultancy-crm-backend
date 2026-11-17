"""What the office logo may be (spec 99c): checked from its bytes, never from what the browser says.

PNG, JPG and WebP are known by their first bytes. An SVG is rebuilt from an allowlist of drawing
elements and attributes, so nothing in it can run a script or reach outside the file (it can be
opened on its own, not only in an `<img>`). Pure functions, no database or storage.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

MAX_LOGO_BYTES = 1024 * 1024
CHOOSE_LOGO_MESSAGE = "Choose a logo to upload."
LOGO_TOO_BIG_MESSAGE = "The logo is too big. Upload at most 1 MB."
LOGO_KIND_MESSAGE = "Upload a PNG, SVG, JPG or WebP image."

SVG_NAMESPACE = "http://www.w3.org/2000/svg"
XLINK_NAMESPACE = "http://www.w3.org/1999/xlink"
XML_NAMESPACE = "http://www.w3.org/XML/1998/namespace"
_BOM = b"\xef\xbb\xbf"
# Deeper than any real logo; stops a file made only of nested groups.
_MAX_DEPTH = 40

# Drawing elements only: no script, foreignObject, image, a, animation, filter or anything that
# can load another file. `style` is kept only when its rules stay inside the file.
_ELEMENTS = frozenset(
    {
        "svg", "g", "defs", "symbol", "use", "path", "rect", "circle", "ellipse", "line",
        "polyline", "polygon", "text", "tspan", "linearGradient", "radialGradient", "stop",
        "clipPath", "mask", "title", "desc", "style",
    }
)  # fmt: skip
_STYLE_ELEMENT = "style"
# Shape and paint attributes. No event handlers (on...), no data-..., no outside links.
_ATTRIBUTES = frozenset(
    {
        "id", "class", "style", "viewBox", "width", "height", "x", "y", "x1", "y1", "x2", "y2",
        "cx", "cy", "r", "rx", "ry", "fx", "fy", "d", "points", "dx", "dy", "rotate",
        "transform", "opacity", "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width",
        "stroke-linecap", "stroke-linejoin", "stroke-miterlimit", "stroke-dasharray",
        "stroke-dashoffset", "stroke-opacity", "clip-path", "clip-rule", "clipPathUnits",
        "mask", "maskUnits", "maskContentUnits", "offset", "stop-color", "stop-opacity",
        "gradientUnits", "gradientTransform", "spreadMethod", "preserveAspectRatio",
        "font-family", "font-size", "font-weight", "font-style", "text-anchor",
        "dominant-baseline", "letter-spacing", "word-spacing", "color", "display",
        "visibility", "version", "vector-effect", "paint-order",
    }
)  # fmt: skip
_HREF = "href"
_XLINK_HREF = f"{{{XLINK_NAMESPACE}}}href"
_XML_SPACE = f"{{{XML_NAMESPACE}}}space"
_LOCAL_REFERENCE = re.compile(r"#[A-Za-z0-9_.:-]{1,100}")
_LOCAL_URL = re.compile(r"url\(\s*['\"]?#[A-Za-z0-9_.:-]{1,100}['\"]?\s*\)")
# Anything in CSS or an attribute that could reach outside the file or run code.
_UNSAFE_VALUE = re.compile(r"url\(|@import|expression|javascript:|\\|<", re.IGNORECASE)
_DECLARATION = re.compile(rb"<!(DOCTYPE|ENTITY)", re.IGNORECASE)

ET.register_namespace("", SVG_NAMESPACE)
ET.register_namespace("xlink", XLINK_NAMESPACE)


class LogoRefusedError(ValueError):
    """The file can't be the office logo; the message says why, in words for the Admin."""


@dataclass(frozen=True)
class LogoKind:
    """A file type the logo may have, with the type and extension it is stored and served as."""

    content_type: str
    extension: str


PNG = LogoKind("image/png", ".png")
JPEG = LogoKind("image/jpeg", ".jpg")
WEBP = LogoKind("image/webp", ".webp")
SVG = LogoKind("image/svg+xml", ".svg")


@dataclass(frozen=True)
class CheckedLogo:
    """A logo that passed the checks: its kind and the bytes to store (an SVG, sanitised)."""

    kind: LogoKind
    content: bytes


def check_logo(content: bytes) -> CheckedLogo:
    """Return the logo to store, or refuse it.

    Raises:
        LogoRefusedError: It is empty, over 1 MB, or not a PNG, JPG, WebP or plain SVG.
    """
    if not content:
        raise LogoRefusedError(CHOOSE_LOGO_MESSAGE)
    if len(content) > MAX_LOGO_BYTES:
        raise LogoRefusedError(LOGO_TOO_BIG_MESSAGE)
    raster = _raster_kind(content)
    if raster is not None:
        return CheckedLogo(raster, content)
    if not content.removeprefix(_BOM).lstrip().startswith(b"<"):
        raise LogoRefusedError(LOGO_KIND_MESSAGE)
    return CheckedLogo(SVG, sanitise_svg(content))


def _raster_kind(content: bytes) -> LogoKind | None:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if content.startswith(b"\xff\xd8\xff"):
        return JPEG
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return WEBP
    return None


def sanitise_svg(content: bytes) -> bytes:
    """Rebuild an SVG from its safe drawing elements and attributes only.

    Raises:
        LogoRefusedError: It isn't UTF-8, isn't well-formed, isn't an SVG, has a DOCTYPE or
            entities, or is nested deeper than any logo needs.
    """
    if _DECLARATION.search(content):
        raise LogoRefusedError(LOGO_KIND_MESSAGE)
    try:
        text = content.removeprefix(_BOM).decode("utf-8")
        root = ET.fromstring(text)  # noqa: S314  # no DOCTYPE, so no entities to expand
    except (UnicodeDecodeError, ET.ParseError) as error:
        raise LogoRefusedError(LOGO_KIND_MESSAGE) from error
    if root.tag != f"{{{SVG_NAMESPACE}}}svg":
        raise LogoRefusedError(LOGO_KIND_MESSAGE)
    clean = _clean_element(root, depth=0)
    if clean is None:  # pragma: no cover  # the root is an svg, which is always kept
        raise LogoRefusedError(LOGO_KIND_MESSAGE)
    serialised: bytes = ET.tostring(clean, encoding="utf-8", xml_declaration=False)
    return serialised


def _clean_element(element: ET.Element, depth: int) -> ET.Element | None:
    if depth > _MAX_DEPTH:
        raise LogoRefusedError(LOGO_KIND_MESSAGE)
    name = _svg_name(element.tag)
    if name not in _ELEMENTS:
        return None
    clean = ET.Element(element.tag, _clean_attributes(element.attrib))
    if name == _STYLE_ELEMENT:
        return _clean_style(clean, element.text or "")
    clean.text = element.text
    _add_clean_children(clean, element, depth)
    return clean


def _add_clean_children(clean: ET.Element, element: ET.Element, depth: int) -> None:
    for child in element:
        clean_child = _clean_element(child, depth + 1)
        if clean_child is not None:
            clean_child.tail = child.tail
            clean.append(clean_child)


def _svg_name(tag: str) -> str | None:
    prefix = f"{{{SVG_NAMESPACE}}}"
    return tag.removeprefix(prefix) if tag.startswith(prefix) else None


def _clean_attributes(attributes: dict[str, str]) -> dict[str, str]:
    return {name: value for name, value in attributes.items() if _is_safe_attribute(name, value)}


def _is_safe_attribute(name: str, value: str) -> bool:
    if name in (_HREF, _XLINK_HREF):
        return _LOCAL_REFERENCE.fullmatch(value.strip()) is not None
    if name == _XML_SPACE:
        return value in ("default", "preserve")
    return name in _ATTRIBUTES and _is_safe_value(value)


def _is_safe_value(value: str) -> bool:
    """No link outside the file, import, script or escape; `url(#id)` stays inside it."""
    return _UNSAFE_VALUE.search(_LOCAL_URL.sub("", value)) is None


def _clean_style(clean: ET.Element, css: str) -> ET.Element | None:
    if not _is_safe_value(css):
        return None
    clean.text = css
    return clean
