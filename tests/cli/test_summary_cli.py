"""Reject invalid summary scope arguments before accessing configuration."""

import pytest

from voice_archive_api.cli import summaries


@pytest.mark.parametrize(
    "arguments",
    [
        ["generate", "--root-section-id", "0"],
        ["generate", "--root-section-id", "9223372036854775808"],
        ["clear", "--root-section-id", "1"],
        ["clear", "--allow-stale-downstream"],
    ],
)
def test_rejects_invalid_scope_arguments(arguments):
    with pytest.raises(SystemExit) as error:
        summaries.run([*arguments, "--book-id", "1"])

    assert error.value.code == 2
