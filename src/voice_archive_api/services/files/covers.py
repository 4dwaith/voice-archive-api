import warnings
from tempfile import TemporaryFile
from typing import BinaryIO

from PIL import Image, ImageOps, UnidentifiedImageError

from voice_archive_api.core.errors import ApplicationError
from voice_archive_api.file_handling import BinarySource, Storage
from voice_archive_api.services.files.types import PreparedFile

ACCEPTED_FORMATS = {"JPEG", "PNG", "WEBP"}


class InvalidImage(ApplicationError):
    """The cover is empty, corrupt, unsafe to decode, or cannot become WebP."""

    code = "invalid_image"
    message = "Cover is empty, corrupt, or cannot be safely decoded and normalized"


class UnsupportedMediaType(ApplicationError):
    """The decoded image format is not an accepted cover format."""

    code = "unsupported_media_type"
    message = "Cover must be a JPEG, PNG, or WebP image"


def _decode_cover(source: BinaryIO) -> Image.Image:
    """Verify the container, apply EXIF orientation, and return a decoded image with pixel data."""
    try:
        # No warning list is collected; this context only scopes the filter.
        warning_context = warnings.catch_warnings(record=False)

        if warning_context is None:
            # The warning context is None, which means that the Python version does not support this
            raise SyntaxError

        with warning_context:
            # If an image is too large, Pillow will raise a DecompressionBombWarning.
            # This is a malicious attempt to cause a denial of service.
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source) as image:
                if image.format not in ACCEPTED_FORMATS:
                    raise UnsupportedMediaType
                image.verify()

            # In case the image has an animation, process only the first frame.
            source.seek(0)

            # verify() invalidates the image object; decoding needs a fresh one.
            with Image.open(source) as image:
                image.load()

                # EXIF orientation can cause an image to be rotated or flipped on display.
                # Reflect those changes in the pixel data before stripping the EXIF metadata.
                with ImageOps.exif_transpose(image) as oriented:
                    # Transparency may be an alpha channel or palette metadata.
                    transparent = "A" in oriented.getbands() or "transparency" in oriented.info
                    return oriented.convert("RGBA" if transparent else "RGB")
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ) as exc:
        raise InvalidImage from exc


def prepare_cover(source: BinarySource, storage: Storage, *, max_bytes: int) -> PreparedFile:
    """Stage, validate, normalize, then promote a detached static WebP cover.

    max_bytes limits the incoming file, not the re-encoded result. The caller
    owns the source. No client filenames or MIME declarations are used. Animated
    inputs contribute their first frame. Success transfers ownership of the
    promoted file to the caller.
    """
    original_key = storage.put_temporary(source, max_bytes=max_bytes)
    normalized_key = None
    promoted_key = None
    try:
        try:
            # Encoded output uses a disk-backed temporary file.
            # Decoded pixels are stored in memory.
            with storage.open(original_key) as original, TemporaryFile("w+b") as normalized:
                with _decode_cover(original) as cover:
                    width, height = cover.size
                    try:
                        cover.save(
                            normalized,
                            "WEBP",
                            quality=85,
                            exif=b"",
                            icc_profile=b"",
                            xmp=b"",
                        )
                    except ValueError as exc:
                        # For example, dimensions unsupported by WebP encoding.
                        raise InvalidImage from exc
                byte_size = normalized.tell()
                normalized.seek(0)
                normalized_key = storage.put_temporary(normalized, max_bytes=byte_size)
        finally:
            # Finish input cleanup before promotion, so cleanup failure cannot
            # strand a successfully promoted file whose key was never returned.
            storage.delete(original_key)

        promoted_key = storage.promote(normalized_key)
        return PreparedFile(promoted_key, byte_size, {"width": width, "height": height})
    except BaseException:
        if promoted_key is not None:
            storage.delete(promoted_key)
        if normalized_key is not None:
            storage.delete(normalized_key)
        raise
