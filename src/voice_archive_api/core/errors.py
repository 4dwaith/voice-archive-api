"""Expected application failures, independent of their transport representation."""

from typing import ClassVar


class ApplicationError(Exception):
    code: ClassVar[str]
    message: ClassVar[str]
