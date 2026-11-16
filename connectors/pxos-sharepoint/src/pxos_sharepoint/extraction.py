"""Text of the office formats a document library holds: Word, PowerPoint, Excel, PDF and
plain text.

The Office Open XML formats are zip packages of XML parts, read with the standard library;
only PDF needs a parser of its own (pypdf, pure Python). Microsoft Graph converts files
only to PDF, HTML or JPEG, never to text, so the text is extracted here. A part that
expands past the limit is refused before it is read, so a small file cannot exhaust
memory. A file that cannot be read raises UnreadableDocument, without its content.
"""

import io
import re
import zipfile
from collections.abc import Callable, Iterator
from pathlib import PurePosixPath
from xml.etree import ElementTree

from pypdf import PdfReader

from pxos_kernel.errors import PxosError

LIMIT = 64 * 1024 * 1024
"""The most bytes a document, or any part of its package, may expand to."""

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_NUMBER = re.compile(r"(\d+)\.xml$")


class UnreadableDocument(PxosError):
    """The file is not in a supported format, or its content cannot be read."""


def supported(name: str) -> bool:
    """Whether the file ``name`` is in a format whose text can be extracted."""
    return PurePosixPath(name).suffix.lower() in _READERS


def extract_text(name: str, content: bytes, *, limit: int = LIMIT) -> str:
    """The text of the file ``name``, chosen by its extension, stripped."""
    reader = _READERS.get(PurePosixPath(name).suffix.lower())
    if reader is None:
        raise UnreadableDocument("unsupported format")
    try:
        return reader(content, limit).strip()
    except UnreadableDocument:
        raise
    except Exception as error:
        raise UnreadableDocument(f"cannot read the file ({type(error).__name__})") from None


def _plain(content: bytes, limit: int) -> str:
    return content.decode("utf-8-sig", errors="replace")


def _pdf(content: bytes, limit: int) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(content)).pages)


def _docx(content: bytes, limit: int) -> str:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return _paragraphs(_part(archive, "word/document.xml", limit), f"{_W}p", f"{_W}t")


def _pptx(content: bytes, limit: int) -> str:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        slides = sorted(_numbered(archive, "ppt/slides/slide"), key=lambda pair: pair[0])
        return "\n".join(
            _paragraphs(_part(archive, name, limit), f"{_A}p", f"{_A}t") for _, name in slides
        )


def _xlsx(content: bytes, limit: int) -> str:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = _part(archive, "xl/sharedStrings.xml", limit)
            shared = [_joined(item, f"{_S}t") for item in strings.iter(f"{_S}si")]
        sheets = sorted(_numbered(archive, "xl/worksheets/sheet"), key=lambda pair: pair[0])
        rows = (
            "\t".join(_cells(row, shared))
            for _, name in sheets
            for row in _part(archive, name, limit).iter(f"{_S}row")
        )
        return "\n".join(row for row in rows if row)


def _cells(row: ElementTree.Element, shared: list[str]) -> Iterator[str]:
    for cell in row.iter(f"{_S}c"):
        if cell.get("t") == "inlineStr":
            yield _joined(cell, f"{_S}t")
            continue
        value = cell.findtext(f"{_S}v")
        if value is not None:
            yield shared[int(value)] if cell.get("t") == "s" else value


def _numbered(archive: zipfile.ZipFile, prefix: str) -> Iterator[tuple[int, str]]:
    for name in archive.namelist():
        match = _NUMBER.search(name)
        if name.startswith(prefix) and "/" not in name[len(prefix) :] and match:
            yield int(match.group(1)), name


def _part(archive: zipfile.ZipFile, name: str, limit: int) -> ElementTree.Element:
    if archive.getinfo(name).file_size > limit:
        raise UnreadableDocument("a part of the file is too large")
    return ElementTree.fromstring(archive.read(name))


def _paragraphs(root: ElementTree.Element, paragraph: str, text: str) -> str:
    return "\n".join(_joined(node, text) for node in root.iter(paragraph))


def _joined(node: ElementTree.Element, text: str) -> str:
    return "".join(run.text or "" for run in node.iter(text))


_READERS: dict[str, Callable[[bytes, int], str]] = {
    ".txt": _plain,
    ".md": _plain,
    ".pdf": _pdf,
    ".docx": _docx,
    ".pptx": _pptx,
    ".xlsx": _xlsx,
}
