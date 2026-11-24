import pytest

from sandbox.pricing import apply_discount


def test_apply_discount():
    assert apply_discount(100.0, 25) == 75.0


def test_apply_discount_rejects_bad_percent():
    with pytest.raises(ValueError):
        apply_discount(10.0, 150)
