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


def pdf_bytes_of_size(byte_size: int) -> bytes:
    """Pad a generated PDF with comments to reach an exact byte size.

    Comments go before the final startxref/EOF block, after the cross-reference
    data, so existing PDF object offsets stay valid. Numbered comments give range
    comparisons distinct content at different positions instead of blank padding.
    """
    payload = pdf_bytes()
    padding_size = byte_size - len(payload)
    if padding_size < 0:
        raise ValueError(f"Requested PDF size must be at least {len(payload)} bytes")
    if padding_size == 0:
        return payload
    padding = bytearray()
    while len(padding) < padding_size:
        padding.extend(f"% padding {len(padding)}\n".encode())
    # End even a truncated comment with a newline before startxref.
    padding = padding[:padding_size - 1] + b"\n"
    marker = payload.rindex(b"startxref")
    return payload[:marker] + padding + payload[marker:]
