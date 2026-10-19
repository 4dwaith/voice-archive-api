from typing import BinaryIO, Protocol


class FileTooLarge(Exception):
    """The incoming stream exceeded the supplied byte limit."""


class InvalidStorageKey(ValueError):
    """A key is malformed or points outside the storage namespace."""


class BinarySource(Protocol):
    """A synchronous byte stream; seeking, filenames, and closing are not required."""

    def read(self, size: int, /) -> bytes:
        """Return at most size bytes, or b'' at EOF; may block awaiting input."""
        ...


class Storage(Protocol):
    """Opaque keys identify bytes; filenames and media types belong to the service.

    Callers own the input stream and must close streams returned by open().
    Media validation happens between put_temporary() and promote(). The service
    deletes temporary files on validation failure and new assets on DB failure.
    """

    def put_temporary(self, source: BinarySource, *, max_bytes: int) -> str:
        """Copy a possibly non-seekable stream; remove partial output on failure.

        Input uses synchronous read(size), not an async read or a chunk iterator.
        """
        ...

    def promote(self, temporary_key: str) -> str:
        """Promote validated bytes under a fresh immutable key and return it."""
        ...

    def open(self, key: str) -> BinaryIO:
        """Open for binary reading, including seek() for byte-range delivery."""
        ...

    def delete(self, key: str) -> None:
        """Remove a file; already absent files are a successful no-op."""
        ...

    def exists(self, key: str) -> bool:
        ...

    def clear_stale(self) -> int:
        """Delete temporary files last updated more than 24 hours ago.

        Promoted assets are never touched. Return the number of files removed.
        Calling/scheduling this maintenance operation is the application's job.
        """
        ...
