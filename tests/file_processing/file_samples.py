"""Small, generated upload samples shared by API specifications."""

from io import BytesIO

from PIL import Image
from pypdf import PdfWriter


def cover_bytes(format="PNG") -> bytes:
    with Image.new("RGB", (16, 24), "red") as image, BytesIO() as output:
        image.save(output, format)
        return output.getvalue()


def pdf_bytes(page_count=3, password=None, scanned=False, metadata_size=0) -> bytes:
    if scanned:
        with Image.new("RGB", (16, 24), "white") as image, BytesIO() as output:
            image.save(output, "PDF")
            return output.getvalue()
    with PdfWriter() as writer, BytesIO() as output:
        for _ in range(page_count):
            writer.add_blank_page(width=100, height=150)
        if password is not None:
            writer.encrypt(password, algorithm="RC4-128")
        if metadata_size:
            writer.add_metadata({"/Subject": "x" * metadata_size})
        writer.write(output)
        return output.getvalue()
