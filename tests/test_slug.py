import pytest

from taaad.slug import validate


def test_valid():
    for s in ["a", "abc", "acme-co-bot", "x" * 39, "a1b2c3-4"]:
        assert validate(s) == s


def test_invalid():
    for s in [
        "",
        "-leading-hyphen",
        "Capital",
        "with space",
        "with/slash",
        'foo"; rm',
        "x" * 40,
    ]:
        with pytest.raises(ValueError):
            validate(s)
