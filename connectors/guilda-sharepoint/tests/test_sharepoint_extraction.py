"""Text extraction from the office formats a document library holds.

Each fixture is built here with the parts the format's specification requires, so the
tests state which parts of a file the text comes from.
"""

import io
import zipfile

import pytest

from guilda_sharepoint.extraction import UnreadableDocument, extract_text

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'


def package(parts: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def pdf(text: str) -> bytes:
    """A one-page PDF showing ``text`` in Helvetica, with a correct cross-reference table."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    body, offsets = b"%PDF-1.4\n", []
    for number, content in enumerate(objects, start=1):
        offsets.append(len(body))
        body += b"%d 0 obj\n" % number + content + b"\nendobj\n"
    xref = len(body)
    body += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    body += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    body += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return body


def test_word_paragraphs_become_lines() -> None:
    document = package(
        {
            "word/document.xml": f"<w:document {W}><w:body>"
            "<w:p><w:r><w:t>Política de </w:t></w:r><w:r><w:t>viagens</w:t></w:r></w:p>"
            "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Teto: R$ 500</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
            "</w:body></w:document>"
        }
    )

    assert extract_text("politica.docx", document) == "Política de viagens\nTeto: R$ 500"


def test_slides_are_read_in_their_order() -> None:
    def slide(text: str) -> str:
        return f"<p:sld {A}><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:sld>".replace(
            "<p:sld", '<p:sld xmlns:p="urn:p"'
        )

    deck = package(
        {
            "ppt/slides/slide10.xml": slide("Décimo"),
            "ppt/slides/slide2.xml": slide("Segundo"),
            "ppt/slides/slide1.xml": slide("Primeiro"),
            "ppt/slides/_rels/slide1.xml.rels": "<Relationships/>",
        }
    )

    assert extract_text("onboarding.PPTX", deck) == "Primeiro\nSegundo\nDécimo"


def test_sheet_rows_resolve_shared_and_inline_strings() -> None:
    workbook = package(
        {
            "xl/sharedStrings.xml": f"<sst {S}><si><t>Cargo</t></si>"
            "<si><r><t>Ana</t></r><r><t>lista</t></r></si></sst>",
            "xl/worksheets/sheet1.xml": f"<worksheet {S}><sheetData>"
            '<row><c t="s"><v>0</v></c><c t="inlineStr"><is><t>Salário</t></is></c></row>'
            '<row><c t="s"><v>1</v></c><c><v>4200</v></c><c/></row>'
            "</sheetData></worksheet>",
        }
    )

    assert extract_text("cargos.xlsx", workbook) == "Cargo\tSalário\nAnalista\t4200"


def test_a_workbook_without_shared_strings_is_read() -> None:
    workbook = package(
        {
            "xl/worksheets/sheet1.xml": f"<worksheet {S}><sheetData>"
            "<row><c><v>7</v></c></row></sheetData></worksheet>"
        }
    )

    assert extract_text("numeros.xlsx", workbook) == "7"


def test_pdf_text_is_extracted_page_by_page() -> None:
    assert "Manual do colaborador" in extract_text("manual.pdf", pdf("Manual do colaborador"))


@pytest.mark.parametrize("name", ["leia.txt", "guia.md"])
def test_plain_text_is_decoded_as_utf8(name: str) -> None:
    assert extract_text(name, "﻿Benefícios\n".encode()) == "Benefícios"


def test_an_unsupported_format_is_unreadable() -> None:
    with pytest.raises(UnreadableDocument, match="unsupported format"):
        extract_text("foto.png", b"\x89PNG")


def test_a_corrupt_file_is_unreadable_without_its_content() -> None:
    with pytest.raises(UnreadableDocument) as raised:
        extract_text("politica.docx", b"segredo corporativo")

    assert "segredo" not in str(raised.value)
    assert raised.value.__cause__ is None


def test_a_part_that_expands_past_the_limit_is_unreadable() -> None:
    bomb = package({"word/document.xml": f"<w:document {W}>" + " " * 2_000 + "</w:document>"})

    with pytest.raises(UnreadableDocument, match="too large"):
        extract_text("bomba.docx", bomb, limit=1_000)
