import pytest

from voice_archive_api.logic.users import normalize_email_address


@pytest.mark.parametrize(
    "email,expected",
    [
        (" Reader.Name+Tag@EXAMPLE.COM ", "reader.name+tag@example.com"),
        ("आद्वैत@example.com", "आद्वैत@example.com"),
        ("reader@bücher.de", "reader@bücher.de"),
        ("e\u0301@example.com", "é@example.com"),
        ("reader@does-not-exist.example.com", "reader@does-not-exist.example.com"),
    ],
)
def test_normalize_email_preserves_address_identity_without_dns(email, expected):
    assert normalize_email_address(email) == expected


@pytest.mark.parametrize("email", ["Unassigned", "reader@localhost", "Name <reader@example.com>"])
def test_invalid_email_does_not_expose_input(email):
    with pytest.raises(ValueError) as failure:
        normalize_email_address(email)

    assert email not in str(failure.value)
