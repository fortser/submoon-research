"""Отказ при неоднозначном или повреждённом первичном PCK."""
import pytest

from submoon_research.catalog.passports import pck_values


def test_pck_ignores_prose_and_accepts_fortran_exponents():
    text = r"""KPL/PCK
\begintext
BODY608_GM = (999)
\begindata
BODY608_GM = (1.205D+2)
\begintext
BODY608_GM = (0)
"""
    assert pck_values(text, "BODY608_GM") == [120.5]


@pytest.mark.parametrize("data", ["NaN", "1e999", "1.2 invalid", ""])
def test_pck_rejects_invalid_numbers(data):
    with pytest.raises(ValueError):
        pck_values("\\begindata\nBODY608_GM = (" + data + ")", "BODY608_GM")


def test_pck_rejects_duplicate_assignments():
    with pytest.raises(ValueError):
        pck_values("\\begindata\nBODY608_GM=(1)\nBODY608_GM=(2)", "BODY608_GM")
