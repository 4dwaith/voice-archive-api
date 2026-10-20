from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from voice_archive_api.book_files.covers import InvalidImage, UnsupportedMediaType, prepare_cover
from voice_archive_api.storage import FileTooLarge, LocalStorage


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "uploads")


def image_bytes(format="PNG", mode="RGB", size=(32, 24), **options) -> bytes:
    with Image.new(mode, size, "red") as image, BytesIO() as output:
        image.save(output, format, **options)
        return output.getvalue()


def remaining_files(storage) -> list[Path]:
    return [path for path in storage.root.rglob("*") if path.is_file()]


@pytest.mark.parametrize("format", ["JPEG", "PNG", "WEBP"])
def test_accepted_covers_become_detached_static_webp(storage, format):
    payload = image_bytes(format)
    source = BytesIO(payload)
    prepared = prepare_cover(source, storage, max_bytes=len(payload))
    assert not source.closed
    assert prepared.storage_key.startswith("promoted/")
    assert prepared.media_type == "image/webp"
    assert (prepared.width, prepared.height) == (32, 24)
    assert len(remaining_files(storage)) == 1
    with storage.open(prepared.storage_key) as output:
        assert len(output.read()) == prepared.byte_size
        output.seek(0)
        with Image.open(output) as image:
            assert image.format == "WEBP"
            # Frame indexes start at zero; a static image has no frame 1.
            with pytest.raises(EOFError):
                image.seek(1)
            assert image.size == (32, 24)
            image.load()


@pytest.mark.parametrize("payload", [b"", b"not an image", b"\x89PNG\r\n\x1a\ncorrupt"])
def test_invalid_images_leave_no_files(storage, payload):
    with pytest.raises(InvalidImage):
        prepare_cover(BytesIO(payload), storage, max_bytes=100)
    assert remaining_files(storage) == []


@pytest.mark.parametrize("format", ["GIF", "BMP", "TIFF"])
def test_other_image_formats_are_rejected(storage, format):
    with pytest.raises(UnsupportedMediaType):
        prepare_cover(BytesIO(image_bytes(format)), storage, max_bytes=100_000)
    assert remaining_files(storage) == []


def test_truncated_image_is_not_promoted(storage):
    payload = image_bytes("PNG")[:-20]
    with patch.object(storage, "promote", wraps=storage.promote) as promote:
        with pytest.raises(InvalidImage):
            prepare_cover(BytesIO(payload), storage, max_bytes=100_000)
        promote.assert_not_called()
    assert remaining_files(storage) == []


def test_input_limit_plus_one_leaves_no_files(storage):
    payload = image_bytes()
    with pytest.raises(FileTooLarge):
        prepare_cover(BytesIO(payload), storage, max_bytes=len(payload) - 1)
    assert remaining_files(storage) == []


@pytest.mark.parametrize("pixel_limit", [500, 300], ids=["bomb-warning", "bomb-error"])
def test_decompression_bomb_warnings_and_errors_are_rejections(storage, monkeypatch, pixel_limit):
    payload = image_bytes()  # 768 pixels: above the warning/error thresholds below.
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", pixel_limit)
    with pytest.raises(InvalidImage):
        prepare_cover(BytesIO(payload), storage, max_bytes=100_000)
    assert remaining_files(storage) == []


def test_exif_orientation_is_applied_then_metadata_is_removed(storage):
    exif = Image.Exif()
    exif[274] = 6  # Rotate 90 degrees clockwise.
    exif[270] = "private description"
    payload = image_bytes("JPEG", exif=exif, icc_profile=b"private profile")
    prepared = prepare_cover(BytesIO(payload), storage, max_bytes=100_000)
    assert (prepared.width, prepared.height) == (24, 32)
    with storage.open(prepared.storage_key) as output, Image.open(output) as image:
        assert image.size == (24, 32)
        assert not image.getexif()
        assert not any(image.info.get(key) for key in ["exif", "icc_profile", "xmp"])


@pytest.mark.parametrize("format", ["PNG", "WEBP"])
def test_transparency_survives_normalization(storage, format):
    with Image.new("RGBA", (4, 4), (255, 0, 0, 64)) as image, BytesIO() as buffer:
        image.save(buffer, format, lossless=True)
        prepared = prepare_cover(BytesIO(buffer.getvalue()), storage, max_bytes=100_000)
    with storage.open(prepared.storage_key) as output, Image.open(output) as image:
        assert image.convert("RGBA").getpixel((0, 0))[3] == 64


def test_animated_webp_becomes_its_first_frame(storage):
    with Image.new("RGB", (8, 8), "red") as first, Image.new("RGB", (8, 8), "blue") as second:
        with BytesIO() as buffer:
            # Add both images to the buffer.
            first.save(buffer, "WEBP", save_all=True, append_images=[second], duration=100, lossless=True)

            prepared = prepare_cover(BytesIO(buffer.getvalue()), storage, max_bytes=100_000)
    with storage.open(prepared.storage_key) as output, Image.open(output) as image:
        with pytest.raises(EOFError):
            # Seek to the second frame should fail.
            image.seek(1)
        red, _, blue = image.convert("RGB").getpixel((0, 0))
        assert red > 200 and blue < 30


def test_palette_transparency_survives_normalization(storage):
    palette_index = 0
    # In P mode, pixels contain palette indexes rather than RGB colors.
    with Image.new("P", (4, 4), color=palette_index) as image, BytesIO() as buffer:
        image.putpalette([255, 0, 0])  # Palette entry 0 is red.
        image.save(buffer, "PNG", transparency=palette_index)  # Make entry 0 transparent.
        prepared = prepare_cover(BytesIO(buffer.getvalue()), storage, max_bytes=100_000)
    with storage.open(prepared.storage_key) as output, Image.open(output) as image:
        with image.convert("RGBA") as rgba, rgba.getchannel("A") as alpha:
            # Minimum and maximum alpha are both zero: every pixel is transparent.
            assert alpha.getextrema() == (0, 0)


def test_encoding_failure_removes_staged_input(storage):
    payload = image_bytes()

    with patch.object(Image.Image, "save", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            prepare_cover(BytesIO(payload), storage, max_bytes=100_000)

    assert remaining_files(storage) == []


def test_promotion_failure_leaves_no_staged_files(storage):
    with patch.object(storage, "promote", side_effect=OSError("promotion failed")):
        with pytest.raises(OSError, match="promotion failed"):
            prepare_cover(BytesIO(image_bytes()), storage, max_bytes=100_000)
    assert remaining_files(storage) == []


def test_streaming_input_needs_no_seek_or_filename(storage):
    payload = image_bytes()

    class Source:
        offset = 0

        def read(self, size: int, /) -> bytes:
            size = min(size, 17)
            chunk = payload[self.offset:self.offset + size]
            self.offset += len(chunk)
            return chunk

    prepared = prepare_cover(Source(), storage, max_bytes=len(payload))
    assert storage.exists(prepared.storage_key)


@pytest.mark.parametrize("size", [(16_384, 1), (1, 16_384)], ids=["width", "height"])
def test_webp_dimension_limit_is_reported_as_invalid_image(storage, size):
    # Static WebP supports at most 16,383 pixels per axis. PNG allows this
    # input, but WebP encoding must fail even though its total pixel count is small.
    payload = image_bytes(size=size)
    with pytest.raises(InvalidImage):
        prepare_cover(BytesIO(payload), storage, max_bytes=100_000)
    assert remaining_files(storage) == []
