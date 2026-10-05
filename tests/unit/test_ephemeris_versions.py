"""Проверки выбора группы и отказа при противоречивых первичных таблицах."""
from pathlib import Path

import pytest

from submoon_research.catalog.ephemeris_versions import body_summary, compare_himalia_versions

ROOT = Path(__file__).resolve().parents[2]
OLD = ROOT / "data/raw/W0-himalia-sources-20261004/jup344.cmt"
NEW = ROOT / "data/raw/W0-l1-sources-20261004/jup347.cmt"


def sources():
    return OLD.read_text(encoding="utf-8"), NEW.read_text(encoding="utf-8")


def test_himalia_scope_excludes_other_group_and_merged_jupiter():
    old, new = sources()
    result = compare_himalia_versions(old, new)
    assert result["gm_exact_equal"]
    assert len(result["old"]["blocks"]) == 1
    assert len(result["new"]["blocks"]) == 2
    assert float(result["new"]["constants"]["500GM"]) == 126686531.7301536
    assert float(result["new"]["constants"]["500GM"]) != 126686531.8817060
    assert result["comparisons"]["J502"]["delta"] != "0"
    assert result["state_delta"] is None
    assert result["dynamic_compatibility"] == "not_demonstrated"


def test_reject_inconsistent_backward_forward_gm():
    _, new = sources()
    new = new.replace("1.515524299611265E-01", "1.615524299611265E-01", 1)
    with pytest.raises(ValueError, match="Конфликт"):
        body_summary(new, "JUP347", "Himalia", 506)


def test_reject_inconsistent_backward_forward_constants():
    _, new = sources()
    new = new.replace("1.266865317301536E+08", "1.266865317301537E+08", 1)
    with pytest.raises(ValueError, match="Конфликт"):
        body_summary(new, "JUP347", "Himalia", 506)


def test_wrong_body_and_nonfinite_gm_are_rejected():
    old, _ = sources()
    with pytest.raises(ValueError, match="не найден"):
        body_summary(old, "JUP344", "Himalia", 599)
    with pytest.raises(ValueError, match="Некорректный GM"):
        body_summary(old.replace("1.515524299611265E-01", "NaN"), "JUP344", "Himalia", 506)
