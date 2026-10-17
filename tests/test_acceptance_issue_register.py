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


def test_remediation_plan_covers_all_issues_lifecycle_audit_and_delivery_gates():
    root = Path(__file__).resolve().parents[1]
    text = (root / "specs/acceptance-remediation-plan-20261017.md").read_text(encoding="utf-8")
    assert re.findall(r"^\| (A\d{2}) \|", text, re.M) == [f"A{i:02d}" for i in range(1, 19)]
    for identity in ["S01", "S02"] + [f"U{i:02d}" for i in range(1, 9)]:
        assert identity in text
    assert re.findall(r"^### (P\d) ", text, re.M) == [f"P{i}" for i in range(1, 7)]
    assert text.count("出门条件：") == 6
    for constraint in ("每批代码修改", "Git commit", "旧历史可读", "累计补丁", "回滚", "真实未改样本", "不使用显卡"):
        assert constraint in text
    assert "不是修复完成报告" in text
    assert "不能保证穷尽" in text
    assert "用户已明确选择：每次翻译完成都暂停，手动继续" in text
    assert "不重复卡在等待点" in text
