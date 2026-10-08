"""Generate an independent, two-page, text-only course PDF with known facts."""

from pathlib import Path
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

PAGES = [
    "COMP101 - Independent local test course\nPhotosynthesis converts light energy into chemical energy.\nChlorophyll absorbs sunlight in chloroplasts.\nThe course experiment uses blue light at 450 nanometres.\nThe measured oxygen production was 7 millilitres per hour.",
    "COMP101 - Assessment notes\nThe final project is worth 35 percent of the course grade.\nThe submission deadline is 18 November in this fictional course.\nStudents submit a 1200-word report and a five-minute presentation.",
]


def make_pdf(path):
    writer = PdfWriter()
    for text in PAGES:
        page = writer.add_blank_page(width=595, height=842)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {NameObject("/F1"): writer._add_object(font)}
                )
            }
        )
        stream = DecodedStreamObject()
        operations = ["BT /F1 12 Tf 50 780 Td"]
        for i, line in enumerate(text.splitlines()):
            line = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            if i:
                operations.append("0 -22 Td")
            operations.append(f"({line}) Tj")
        operations.append("ET")
        stream.set_data("\n".join(operations).encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    writer.write(path)
    return path


if __name__ == "__main__":
    print(make_pdf("../data/mvp-test/independent-course.pdf").resolve())
