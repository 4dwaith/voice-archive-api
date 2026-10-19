import os
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import pytest

from voice_archive_api.storage import BinarySource, FileTooLarge, InvalidStorageKey, LocalStorage, Storage
from voice_archive_api.storage.local import CHUNK_SIZE, STALE_AFTER_SECONDS


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "uploads")


def files(storage: LocalStorage) -> list[Path]:
    return [path for path in storage.root.rglob("*") if path.is_file()]


def test_temporary_file_round_trip_and_immutable_promotion(storage):
    source = BytesIO(b"validated bytes")
    temporary = storage.put_temporary(source, max_bytes=100)
    assert temporary.startswith("temporary/")
    assert storage.exists(temporary)
    with storage.open(temporary) as incoming:
        assert incoming.read() == b"validated bytes"

    promoted = storage.promote(temporary)
    assert promoted.startswith("promoted/")
    assert not storage.exists(temporary)
    with storage.open(promoted) as incoming:
        assert incoming.read() == b"validated bytes"
    assert not source.closed
    assert len(files(storage)) == 1


def test_promoted_stream_can_seek_directly_to_later_bytes(storage):
    payload = bytes(range(256)) * 10_000
    key = storage.promote(storage.put_temporary(BytesIO(payload), max_bytes=len(payload)))
    with storage.open(key) as incoming:
        incoming.seek(2_000_000)
        assert incoming.read(4096) == payload[2_000_000:2_004_096]
        incoming.seek(-32, 2)
        assert incoming.read() == payload[-32:]


@pytest.mark.parametrize("size", [0, 31, 32])
def test_empty_and_within_limit_streams_can_be_staged(storage, size):
    # Empty-file/media rejection belongs to the validation service, not storage.
    key = storage.put_temporary(BytesIO(b"a" * size), max_bytes=32)
    with storage.open(key) as incoming:
        assert incoming.read() == b"a" * size


def test_limit_plus_one_removes_partial_file(storage):
    with pytest.raises(FileTooLarge):
        storage.put_temporary(BytesIO(b"a" * (CHUNK_SIZE + 1)), max_bytes=CHUNK_SIZE)
    assert files(storage) == []


def test_reads_are_bounded_and_stop_at_limit_plus_one(storage):
    class GeneratedStream:
        total = 0

        def read(self, size: int, /) -> bytes:
            assert 0 < size <= CHUNK_SIZE
            self.total += size
            return b"x" * size

    source = GeneratedStream()
    typed_source: BinarySource = source
    with pytest.raises(FileTooLarge):
        storage.put_temporary(typed_source, max_bytes=2 * CHUNK_SIZE + 10)
    assert source.total == 2 * CHUNK_SIZE + 11
    assert files(storage) == []


@pytest.mark.parametrize("limit", [0, -1, None, True, 1.5])
def test_invalid_limit_does_not_create_files(storage, limit):
    with pytest.raises(ValueError, match="positive integer"):
        storage.put_temporary(BytesIO(b"x"), max_bytes=limit)
    assert not storage.root.exists()


@pytest.mark.parametrize("failure", [OSError("source failed"), KeyboardInterrupt()])
def test_source_failure_or_interruption_removes_partial_file(storage, failure):
    class FailingStream:
        reads = 0

        def read(self, size):
            self.reads += 1
            if self.reads == 1:
                return b"partial"
            raise failure

    with pytest.raises(type(failure)):
        storage.put_temporary(FailingStream(), max_bytes=100)
    assert files(storage) == []


def test_write_failure_removes_partial_file(storage):
    original_open = Path.open

    class FailingOutput:
        def __init__(self, output):
            self.output = output

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.output.close()

        def write(self, data):
            self.output.write(data[:1])
            raise OSError("disk full")

    def open_with_failure(path, mode):
        return FailingOutput(original_open(path, mode))

    with patch.object(Path, "open", open_with_failure):
        with pytest.raises(OSError, match="disk full"):
            storage.put_temporary(BytesIO(b"payload"), max_bytes=100)
    assert files(storage) == []


def test_failed_promotion_preserves_temporary_file(storage):
    temporary = storage.put_temporary(BytesIO(b"valid"), max_bytes=100)
    with patch("voice_archive_api.storage.local.os.link", side_effect=OSError("failed")):
        with pytest.raises(OSError, match="failed"):
            storage.promote(temporary)
    assert storage.exists(temporary)
    assert len(files(storage)) == 1


def test_failed_temporary_unlink_rolls_back_promotion(storage):
    temporary = storage.put_temporary(BytesIO(b"valid"), max_bytes=100)
    original_unlink = Path.unlink

    def unlink_with_failure(path, **kwargs):
        if path.parent.name == "temporary":
            raise OSError("unlink failed")

        # Allows the unlinking to proceed from the promoted folder
        return original_unlink(path, **kwargs)

    with patch.object(Path, "unlink", unlink_with_failure):
        with pytest.raises(OSError, match="unlink failed"):
            storage.promote(temporary)
    assert storage.exists(temporary)
    assert len(files(storage)) == 1


def test_key_collision_never_overwrites_or_deletes_an_existing_file(storage):
    with patch("voice_archive_api.storage.local.uuid4", return_value=UUID(int=1)):
        # No collision in temporary
        temporary = storage.put_temporary(BytesIO(b"first"), max_bytes=100)
        with pytest.raises(FileExistsError):
            storage.put_temporary(BytesIO(b"second"), max_bytes=100)

        # No collision during promotion
        asset = storage.promote(temporary)
        second = storage.put_temporary(BytesIO(b"second"), max_bytes=100)
        with pytest.raises(FileExistsError):
            storage.promote(second)

    with storage.open(asset) as incoming:
        assert incoming.read() == b"first"
    with storage.open(second) as incoming:
        assert incoming.read() == b"second"


def test_delete_is_idempotent_for_temporary_and_promoted_files(storage):
    temporary = storage.put_temporary(BytesIO(b"x"), max_bytes=100)
    storage.delete(temporary)
    storage.delete(temporary)
    assert not storage.exists(temporary)

    asset = storage.promote(storage.put_temporary(BytesIO(b"y"), max_bytes=100))
    storage.delete(asset)
    storage.delete(asset)
    assert not storage.exists(asset)

    assert files(storage) == []


def test_missing_files_raise_on_read_or_promotion(storage):
    key = "temporary/" + "0" * 32
    assert not storage.exists(key)
    with pytest.raises(FileNotFoundError):
        storage.open(key)
    with pytest.raises(FileNotFoundError):
        storage.promote(key)


def test_promoted_asset_cannot_be_promoted_again(storage):
    key = storage.promote(storage.put_temporary(BytesIO(b"x"), max_bytes=100))
    with pytest.raises(InvalidStorageKey, match="Only temporary"):
        storage.promote(key)
    assert storage.exists(key)


def test_promotion_links_the_same_file_before_removing_the_temporary_name(storage):
    temporary = storage.put_temporary(BytesIO(b"complete"), max_bytes=100)
    original_link = os.link

    def observe_link(source, destination):
        original_link(source, destination)
        assert source.exists() and destination.exists()
        assert os.path.samefile(source, destination)
        assert source.stat().st_nlink == 2
        assert destination.read_bytes() == b"complete"

    with patch("voice_archive_api.storage.local.os.link", side_effect=observe_link):
        promoted = storage.promote(temporary)
    assert not storage.exists(temporary)
    assert (storage.root / promoted).stat().st_nlink == 1
    with storage.open(promoted) as incoming:
        assert incoming.read() == b"complete"


@pytest.mark.parametrize(
    "key",
    [
        "", ".", "../outside", "/tmp/outside", "C:/outside", "C:\\outside",
        "promoted/../outside", "promoted//" + "0" * 32,
        "promoted\\" + "0" * 32, "promoted/" + "0" * 32 + "/extra",
        "promoted/" + "0" * 32 + ":stream", "promoted/cover.png", None,
    ],
)
def test_invalid_keys_are_rejected_by_every_key_operation(storage, key):
    for operation in (storage.open, storage.delete, storage.exists, storage.promote):
        with pytest.raises(InvalidStorageKey):
            operation(key)
    assert not storage.root.exists()


@pytest.mark.parametrize("redirect_namespace", [False, True])
def test_symlinks_cannot_redirect_storage_operations(storage, tmp_path, redirect_namespace):
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / ("a" * 32)
    target.write_bytes(b"keep")
    storage.root.mkdir()
    namespace = storage.root / "promoted"
    try:
        if redirect_namespace:
            # The entire directory is linked outside the storage root
            namespace.symlink_to(outside, target_is_directory=True)
        else:
            # The target file is linked outside the storage root
            namespace.mkdir()
            (namespace / target.name).symlink_to(target)
    except OSError as exc:
        pytest.skip(f"Creating symlinks is unavailable: {exc}")

    key = "promoted/" + target.name
    for operation in (storage.open, storage.delete, storage.exists):
        with pytest.raises(InvalidStorageKey):
            operation(key)
    assert target.read_bytes() == b"keep"


def test_non_seekable_stream_satisfies_the_storage_contract(storage):
    class StreamingSource:
        def __init__(self):
            self.chunks = iter([b"first", b"second", b""])

        def read(self, size: int, /) -> bytes:
            return next(self.chunks)

    source: BinarySource = StreamingSource()
    backend: Storage = storage
    key = backend.put_temporary(source, max_bytes=100)
    with backend.open(key) as incoming:
        assert incoming.read() == b"firstsecond"


def test_clear_stale_preserves_new_temp_files_and_promoted_files(storage):
    now = 2_000_000_000
    cutoff = now - STALE_AFTER_SECONDS
    old = storage.put_temporary(BytesIO(b"old"), max_bytes=100)
    boundary = storage.put_temporary(BytesIO(b"boundary"), max_bytes=100)
    updated = storage.put_temporary(BytesIO(b"updated"), max_bytes=100)
    asset = storage.promote(storage.put_temporary(BytesIO(b"asset"), max_bytes=100))
    for key, timestamp in [(old, cutoff - 1), (boundary, cutoff), (updated, now), (asset, 1)]:
        os.utime(storage.root / key, (timestamp, timestamp))

    with patch("voice_archive_api.storage.local.time", return_value=now):
        assert storage.clear_stale() == 1
        assert storage.clear_stale() == 0
    assert not storage.exists(old)
    assert all(storage.exists(key) for key in [boundary, updated, asset])


def test_clear_stale_does_not_create_missing_storage(storage):
    assert storage.clear_stale() == 0
    assert not storage.root.exists()


def test_clear_stale_ignores_unknown_files_and_directories(storage):
    namespace = storage.root / "temporary"
    namespace.mkdir(parents=True)
    unknown = namespace / "unrelated.txt"
    unknown.write_bytes(b"keep")
    directory = namespace / ("b" * 32)
    directory.mkdir()
    for path in [unknown, directory]:
        os.utime(path, (1, 1))
    assert storage.clear_stale() == 0
    assert unknown.read_bytes() == b"keep"
    assert directory.is_dir()


def test_clear_stale_tolerates_a_file_removed_by_another_operation(storage):
    key = storage.put_temporary(BytesIO(b"old"), max_bytes=100)
    os.utime(storage.root / key, (1, 1))
    original_unlink = Path.unlink

    def competing_unlink(path, **kwargs):
        original_unlink(path, **kwargs)
        raise FileNotFoundError

    with patch.object(Path, "unlink", competing_unlink):
        assert storage.clear_stale() == 0
    assert not storage.exists(key)


@pytest.mark.parametrize("redirect_namespace", [False, True])
def test_clear_stale_never_follows_symlinks(storage, tmp_path, redirect_namespace):
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / ("a" * 32)
    target.write_bytes(b"keep")
    os.utime(target, (1, 1))
    storage.root.mkdir()
    namespace = storage.root / "temporary"
    try:
        if redirect_namespace:
            namespace.symlink_to(outside, target_is_directory=True)
        else:
            namespace.mkdir()
            (namespace / target.name).symlink_to(target)
    except OSError as exc:
        pytest.skip(f"Creating symlinks is unavailable: {exc}")

    if redirect_namespace:
        with pytest.raises(InvalidStorageKey):
            storage.clear_stale()
    else:
        assert storage.clear_stale() == 0
    assert target.read_bytes() == b"keep"


def test_streaming_writes_refresh_activity_before_the_writer_closes(storage):
    class ObservingSource:
        reads = 0

        def read(self, size: int, /) -> bytes:
            self.reads += 1
            if self.reads == 1:
                path = files(storage)[0]
                os.utime(path, (1, 1))
                return b"active"
            assert files(storage)[0].stat().st_mtime > 1
            assert storage.clear_stale() == 0
            return b""

    key = storage.put_temporary(ObservingSource(), max_bytes=100)
    assert storage.exists(key)
