import pytest

from voice_archive_api.logic.books.validation import resolve_primary_language


@pytest.mark.parametrize(
    "declared,primary",
    [("en", "en"), ("en-US", "en"), ("en_US", "en"), ("EN_us", "en"), ("eng", "en"), ("kn-IN", "kn"), ("fra", "fr")],
)
def test_resolves_recognized_primary_language_aliases(declared, primary):
    assert resolve_primary_language(declared) == primary


@pytest.mark.parametrize("declared", [None, "und", "und-IN", "mul", "mis", "zxx"])
def test_requires_a_single_known_primary_language(declared):
    with pytest.raises(ValueError, match="single primary book language"):
        resolve_primary_language(declared)


def test_rejects_invalid_declared_language():
    with pytest.raises(ValueError, match="recognized language code"):
        resolve_primary_language("english")
