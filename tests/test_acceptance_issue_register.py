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


def test_followup_separates_real_failures_from_final_success_and_missing_overlap_evidence():
    path = Path(__file__).resolve().parents[1] / "specs/acceptance-issues-20261017.md"
    text = path.read_text(encoding="utf-8")
    assert "180 秒 TTS 合成超时而真实失败" in text
    assert "最终 history 证实整条流水线已成功" in text
    assert "日志没有记录每条 SSE 消息内容" in text
    assert "不能用这个旧文件的检测结果反驳用户" in text
    assert "不要求用户逐条确认安全项" in text
