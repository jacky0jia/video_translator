import re
from pathlib import Path


def test_all_ten_user_reported_issues_have_status_and_acceptance_criteria():
    path = Path(__file__).resolve().parents[1] / "specs/acceptance-issues-20261017.md"
    text = path.read_text(encoding="utf-8")
    issues = re.findall(r"^## ([SU]\d{2}) (.*?)(?=^## |\Z)", text, re.M | re.S)
    assert [identity for identity, _ in issues] == ["S01", "S02"] + [f"U{i:02d}" for i in range(1, 9)]
    for identity, section in issues:
        assert "状态：" in section, identity
        assert "验收：" in section, identity
    assert "未修改应用运行逻辑" in text
    assert "均未标记为修复完成" in text
