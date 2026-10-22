"""Bounded MP3 preparation; preserve exact bytes and inspect MPEG headers."""

import math

from mutagen import MutagenError
from mutagen.mp3 import MPEGInfo

from voice_archive_api.core.errors import ApplicationError
from voice_archive_api.domain.files.model import PreparedFile
from voice_archive_api.file_handling import BinarySource, Storage


class InvalidAudio(ApplicationError):
    code = "invalid_audio"
    message = "Source must be readable MPEG Layer III audio with a finite positive duration"


def prepare_audio(source: BinarySource, storage: Storage, *, max_bytes: int) -> PreparedFile:
    temporary_key = storage.put_temporary(source, max_bytes=max_bytes)
    promoted_key = None
    try:
        with storage.open(temporary_key) as staged:
            staged.seek(0, 2)
            byte_size = staged.tell()
            staged.seek(0)
            try:
                # MPEGInfo skips ID3 tags without loading artwork/tag payloads.
                # Xing/VBRI headers supply variable-bitrate duration when present.
                info = MPEGInfo(staged)
                if info.layer != 3 or info.sketchy or not math.isfinite(info.length) or info.length <= 0:
                    raise InvalidAudio
                duration_ms = round(info.length * 1000)
                if not 0 < duration_ms <= 9_223_372_036_854_775_807:
                    raise InvalidAudio
            except (MutagenError, ValueError, EOFError) as exc:
                raise InvalidAudio from exc
        promoted_key = storage.promote(temporary_key)
        return PreparedFile(
            promoted_key,
            byte_size,
            {"duration_ms": duration_ms, "sample_rate": info.sample_rate, "channels": info.channels},
        )
    except BaseException:
        if promoted_key is not None:
            storage.delete(promoted_key)
        storage.delete(temporary_key)
        raise
