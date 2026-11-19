"""解析规则事件记录、选择器拦截与字段模板回写链路测试。

覆盖：
- 无效 XPath 提交被拦截：发布状态不变、事件记录留存拦截原因、不产生模板；
- 有效 CSS 规则发布：状态机按序流转、事件进入已发布、模板只生成一次；
- 选择器校验结果与规则发布状态原子提交；
- 口径/阈值/范围越界值不允许保存；
- 版本冲突 replace / parallel 两种处理与历史口径优先级；
- 跨模块回写链路：字段抽取消费事件记录，只为本批通过校验的规则生成模板；
- 事件状态机只能按序逐个流转。
"""
from __future__ import annotations

import pytest

from app.services.field_extract import FieldExtractService
from app.services.parse_rule import (
    ALLOWED_EXTRACT_TYPES,
    ALLOWED_SELECTOR_TYPES,
    ParseRuleService,
    _validate_selector,
)
from app.services.parse_rule_event import (
    ALLOWED_TRANSITIONS,
    BLOCKED_STATUS,
    EVENT_STATUS_ORDER,
    PASS_STATUS,
    PUBLISHED_STATUS,
    ParseRuleEventService,
)
from app.store import store


@pytest.fixture(autouse=True)
def _reset_store() -> None:
    """每个用例使用干净仓库：重建种子数据、关系索引并补录预置事件。"""
    from app.seed import SEED_ROWS
    store._tables.clear()
    store._pipeline_states.clear()
    store._relations.clear()
    for name, rows in SEED_ROWS.items():
        store._tables[name] = [dict(row) for row in rows]
    store._tables["parse_rule_event"] = []
    store._pipeline_states["parse_rule_event_sync"] = {}
    store._build_relations()
    store.seed_parse_rule_events()


def _create(svc: ParseRuleService, code: str, *, selector_type: str = "xpath",
            expression: str = "//h1", version: str = "v1.0", extract: str = "text"):
    return svc.create_entry({
        "规则编号": code,
        "目标字段名": "测试字段",
        "选择器类型": selector_type,
        "选择器表达式": expression,
        "提取方式": extract,
        "规则版本": version,
    })


# ------------------------------------------------------------------
# 选择器判定条件
# ------------------------------------------------------------------

def test_selector_validation_cases() -> None:
    ok, _ = _validate_selector("xpath", "//div[@class='item']/text()")
    assert ok is True
    ok, msg = _validate_selector("xpath", "div[@class='item']")
    assert ok is False and "XPath" in msg
    ok, msg = _validate_selector("xpath", "//div[(]")
    assert ok is False and "括号" in msg
    ok, _ = _validate_selector("css", ".price > span")
    assert ok is True
    ok, msg = _validate_selector("css", "div<>")
    assert ok is False
    ok, _ = _validate_selector("jsonpath", "$.items[0].id")
    assert ok is True
    ok, msg = _validate_selector("jsonpath", "items[0]")
    assert ok is False
    ok, msg = _validate_selector("regex", "[a-z(")
    assert ok is False and "正则" in msg
    ok, msg = _validate_selector("unknown", "x")
    assert ok is False


# ------------------------------------------------------------------
# 核心验收：无效 XPath 与有效 CSS 各提交一次
# ------------------------------------------------------------------

def test_invalid_xpath_is_blocked_and_publish_state_unchanged() -> None:
    svc = ParseRuleService()
    event_svc = ParseRuleEventService()
    rule, errors = _create(svc, "PARS-ACC-01-A", expression="not-a-xpath")
    assert errors == []
    rule_id = int(rule["id"])
    assert rule["status"] == "草稿"
    assert rule["_selector_check"]["ok"] is False

    # 提交校验：报错且规则仍保持草稿，不允许进入待校验/已生效
    blocked, message = svc.run_action(rule_id, "提交校验")
    assert blocked is None
    assert "拦截" in message and "XPath" in message
    assert store.find("parse_rule", rule_id)["status"] == "草稿"

    # 事件记录留存状态变化与拦截原因
    events = event_svc.events_for_rule(rule_id)
    statuses = [e["事件状态"] for e in events]
    assert statuses[-1] == BLOCKED_STATUS
    blocked_event = events[-1]
    assert blocked_event["校验通过"] is False
    assert blocked_event["拦截原因"]
    assert blocked_event["选择器表达式"] == "not-a-xpath"
    # 状态轨迹记录每一步流转
    assert any(step["to"] == BLOCKED_STATUS for step in blocked_event["状态轨迹"])

    # 被拦截规则不产生字段模板
    assert svc.generate_field_extracts(rule_id) == []
    assert store.get_relations("parse_to_field", source_module="parse_rule", source_id=rule_id) == []

    # 拦截状态直接发布也被拒绝
    published, pub_msg = svc.run_action(rule_id, "标记生效")
    assert published is None
    assert "状态机" in pub_msg or "校验" in pub_msg


def test_valid_css_rule_publishes_and_generates_single_template() -> None:
    svc = ParseRuleService()
    event_svc = ParseRuleEventService()
    fe_svc = FieldExtractService()
    rule, errors = _create(
        svc, "PARS-ACC-02-A", selector_type="css", expression=".card .title", version="v1.2")
    assert errors == []
    rule_id = int(rule["id"])

    checked, msg = svc.run_action(rule_id, "提交校验")
    assert checked is not None and checked["status"] == "待校验"
    assert event_svc.latest_event(rule_id)["事件状态"] == PASS_STATUS

    published, msg = svc.run_action(rule_id, "标记生效")
    assert published is not None and published["status"] == "已生效"
    latest = event_svc.latest_event(rule_id)
    assert latest["事件状态"] == PUBLISHED_STATUS
    assert latest["版本角色"] == "当前发布版本"
    assert latest["跨模块同步状态"] == "已同步"

    # 只生成一个模板，重复提交/重复生成不产生重复模板
    rels = store.get_relations("parse_to_field", source_module="parse_rule", source_id=rule_id)
    assert len(rels) == 1
    again = svc.generate_field_extracts(rule_id)
    assert len(again) == 1
    rels2 = store.get_relations("parse_to_field", source_module="parse_rule", source_id=rule_id)
    assert len(rels2) == 1

    # 回写链路游标已记录该事件
    cursor = store.pipeline_state("parse_rule_event_sync")
    assert str(latest["id"]) in cursor["synced_events"]
    assert fe_svc.list_synced_templates()


def test_selector_check_and_publish_state_are_atomic() -> None:
    """校验结果与发布状态同时提交：失败时状态绝不前进。"""
    svc = ParseRuleService()
    event_svc = ParseRuleEventService()
    rule, _ = _create(svc, "PARS-ATOM-01-A", expression="//span[")
    before = store.find("parse_rule", int(rule["id"]))["status"]
    result, _ = svc.run_action(int(rule["id"]), "提交校验")
    after = store.find("parse_rule", int(rule["id"]))["status"]
    assert result is None and before == after == "草稿"
    event = event_svc.latest_event(int(rule["id"]))
    assert event["事件状态"] == BLOCKED_STATUS and event["校验通过"] is False


# ------------------------------------------------------------------
# 口径、阈值、范围与判定条件
# ------------------------------------------------------------------

def test_bounds_violations_are_rejected_on_save() -> None:
    svc = ParseRuleService()
    base = {
        "规则编号": "PARS-BND-1", "目标字段名": "f", "选择器类型": "css",
        "选择器表达式": ".a", "提取方式": "text", "规则版本": "v1.0",
    }
    bad_type = dict(base, 选择器类型="magic-query")
    entry, errors = svc.create_entry(bad_type)
    assert entry is None and any("口径" in e for e in errors)

    bad_extract = dict(base, 提取方式="telepathy")
    entry, errors = svc.create_entry(bad_extract)
    assert entry is None and any("提取方式" in e for e in errors)

    bad_version = dict(base, 规则版本="1.0")  # 缺少 v 前缀
    entry, errors = svc.create_entry(bad_version)
    assert entry is None and any("版本" in e for e in errors)

    good = dict(base, 选择器类型=sorted(ALLOWED_SELECTOR_TYPES)[0],
                提取方式=sorted(ALLOWED_EXTRACT_TYPES)[0])
    entry, errors = svc.create_entry(good)
    assert entry is not None and errors == []


def test_publish_mode_must_be_known() -> None:
    svc = ParseRuleService()
    rule, _ = _create(svc, "PARS-MODE-1-A", selector_type="css", expression=".a")
    svc.run_action(int(rule["id"]), "提交校验")
    entry, msg = svc.run_action(int(rule["id"]), "标记生效", mode="mystery")
    assert entry is None and "发布模式" in msg


def test_submit_blocks_when_selector_becomes_invalid() -> None:
    """提交时重新执行判定：选择器被改成非法值后，校验拦截且状态不前进。"""
    svc = ParseRuleService()
    event_svc = ParseRuleEventService()
    rule, _ = _create(svc, "PARS-TAMPER-1-A", selector_type="css", expression=".a")
    rid = int(rule["id"])
    store.find("parse_rule", rid)["选择器表达式"] = "not a <<selector"
    entry, msg = svc.run_action(rid, "提交校验")
    assert entry is None and "拦截" in msg
    assert store.find("parse_rule", rid)["status"] == "草稿"
    assert event_svc.latest_event(rid)["事件状态"] == BLOCKED_STATUS
    assert event_svc.latest_event(rid)["校验通过"] is False


# ------------------------------------------------------------------
# 事件状态机：按顺序逐个流转
# ------------------------------------------------------------------

def test_event_state_machine_order() -> None:
    assert EVENT_STATUS_ORDER == ["已登记", "校验通过", "校验拦截", "已发布", "已撤下", "已废弃"]
    # 合法路径
    assert "已发布" in ALLOWED_TRANSITIONS["校验通过"]
    assert "已撤下" in ALLOWED_TRANSITIONS["已发布"]
    assert ALLOWED_TRANSITIONS["已废弃"] == set()

    svc = ParseRuleService()
    event_svc = ParseRuleEventService()
    rule, _ = _create(svc, "PARS-SM-01-A", selector_type="css", expression=".a")
    event = event_svc.latest_event(int(rule["id"]))
    # 已登记不能直接发布
    record, msg = event_svc.manual_transition(int(event["id"]), "已发布")
    assert record is None and "不允许" in msg
    # 合法流转
    record, _ = event_svc.manual_transition(int(event["id"]), "校验通过")
    assert record["事件状态"] == "校验通过"
    record, _ = event_svc.manual_transition(int(event["id"]), "已发布")
    assert record["事件状态"] == "已发布"
    record, _ = event_svc.manual_transition(int(event["id"]), "已撤下")
    assert record["事件状态"] == "已撤下"
    record, _ = event_svc.manual_transition(int(event["id"]), "已废弃")
    assert record["事件状态"] == "已废弃"
    # 终态不能再流转
    record, msg = event_svc.manual_transition(int(event["id"]), "已发布")
    assert record is None


def test_rule_state_machine_rejects_skip_and_repeat() -> None:
    svc = ParseRuleService()
    rule, _ = _create(svc, "PARS-SM-02-A", selector_type="css", expression=".a")
    rid = int(rule["id"])
    entry, _ = svc.run_action(rid, "标记生效")  # 草稿直接发布
    assert entry is None
    svc.run_action(rid, "提交校验")
    svc.run_action(rid, "标记生效")
    entry, msg = svc.run_action(rid, "标记生效")  # 重复发布
    assert entry is None and "按序流转" in msg
    entry, msg = svc.run_action(rid, "提交校验")  # 回退也禁止
    assert entry is None


# ------------------------------------------------------------------
# 版本冲突：replace 与 parallel
# ------------------------------------------------------------------

def _publish_two_versions(svc: ParseRuleService, mode: str):
    r1, _ = _create(svc, "PARS-VER-01-01", selector_type="css", expression=".v1", version="v1.0")
    svc.run_action(int(r1["id"]), "提交校验")
    svc.run_action(int(r1["id"]), "标记生效")
    r2, _ = _create(svc, "PARS-VER-01-02", selector_type="css", expression=".v2", version="v2.0")
    svc.run_action(int(r2["id"]), "提交校验")
    svc.run_action(int(r2["id"]), "标记生效", mode=mode)
    return r1, r2


def test_replace_mode_withdraws_old_template_and_event() -> None:
    svc = ParseRuleService()
    event_svc = ParseRuleEventService()
    r1, r2 = _publish_two_versions(svc, "replace")
    assert store.find("parse_rule", int(r1["id"]))["status"] == "已废弃"
    assert store.find("parse_rule", int(r2["id"]))["status"] == "已生效"
    assert event_svc.latest_event(int(r1["id"]))["事件状态"] == "已撤下"
    assert event_svc.latest_event(int(r2["id"]))["版本角色"] == "当前发布版本"

    old_tpl = store.find_by_field("field_extract", "模板编号", "GEN-PARS-VER-01-01-v1.0-FE")
    new_tpl = store.find_by_field("field_extract", "模板编号", "GEN-PARS-VER-01-02-v2.0-FE")
    assert old_tpl["status"] == "已废弃" and "停用" in old_tpl["版本角色"]
    assert new_tpl["版本角色"] == "当前发布版本"


def test_parallel_mode_keeps_both_templates_with_roles() -> None:
    svc = ParseRuleService()
    event_svc = ParseRuleEventService()
    r1, r2 = _publish_two_versions(svc, "parallel")
    assert store.find("parse_rule", int(r1["id"]))["status"] == "已生效"
    assert store.find("parse_rule", int(r2["id"]))["status"] == "已生效"
    assert event_svc.latest_event(int(r1["id"]))["版本角色"] == "历史版本"
    assert event_svc.latest_event(int(r2["id"]))["版本角色"] == "当前发布版本"
    old_tpl = store.find_by_field("field_extract", "模板编号", "GEN-PARS-VER-01-01-v1.0-FE")
    new_tpl = store.find_by_field("field_extract", "模板编号", "GEN-PARS-VER-01-02-v2.0-FE")
    # 两个模板并存，新旧选择器各自产出字段
    assert old_tpl["status"] != "已废弃" and old_tpl["版本角色"] == "历史版本"
    assert new_tpl["版本角色"] == "当前发布版本"


def test_historical_version_priority_blocks_lower_publish() -> None:
    svc = ParseRuleService()
    r1, _ = _create(svc, "PARS-HIST-1-01", selector_type="css", expression=".v2", version="v2.0")
    svc.run_action(int(r1["id"]), "提交校验")
    svc.run_action(int(r1["id"]), "标记生效")
    r2, _ = _create(svc, "PARS-HIST-1-02", selector_type="css", expression=".v1", version="v1.0")
    svc.run_action(int(r2["id"]), "提交校验")
    entry, msg = svc.run_action(int(r2["id"]), "标记生效")
    assert entry is None and "版本冲突" in msg
    # 被拦截的新版本仍是待校验，未产生模板
    assert store.find("parse_rule", int(r2["id"]))["status"] == "待校验"


def test_only_one_current_published_version() -> None:
    svc = ParseRuleService()
    event_svc = ParseRuleEventService()
    r1, r2 = _publish_two_versions(svc, "parallel")
    current = [
        rid for rid in (int(r1["id"]), int(r2["id"]))
        if event_svc.latest_event(rid).get("版本角色") == "当前发布版本"
    ]
    assert current == [int(r2["id"])]


# ------------------------------------------------------------------
# 跨模块回写链路
# ------------------------------------------------------------------

def test_field_extract_consumes_event_records_only_for_passed_batch() -> None:
    svc = ParseRuleService()
    fe_svc = FieldExtractService()
    # 本批一条无效、一条有效
    bad, _ = _create(svc, "PARS-BAT-01-A", expression="bad::xpath")
    good, _ = _create(svc, "PARS-BAT-02-A", selector_type="css", expression=".ok")
    svc.run_action(int(bad["id"]), "提交校验")
    svc.run_action(int(good["id"]), "提交校验")

    result = fe_svc.consume_parse_rule_events()
    assert result["ok"] is True
    # 被拦截事件进入拦截清单，且没有对应模板
    cursor = store.pipeline_state("parse_rule_event_sync")
    assert int(ParseRuleEventService().latest_event(int(bad["id"]))["id"]) in cursor["blocked_event_ids"]
    good_code = "GEN-PARS-BAT-02-A-v1.0-FE"
    assert store.find_by_field("field_extract", "模板编号", good_code) is not None
    assert store.find_by_field("field_extract", "模板编号", "GEN-PARS-BAT-01-A-v1.0-FE") is None

    # 再消费一次保持幂等，模板数量不增长
    before = len(store.rows("field_extract"))
    fe_svc.consume_parse_rule_events()
    assert len(store.rows("field_extract")) == before


def test_publish_does_not_duplicate_templates_on_repeated_submission() -> None:
    svc = ParseRuleService()
    rule, _ = _create(svc, "PARS-IDEM-1-A", selector_type="css", expression=".once")
    svc.run_action(int(rule["id"]), "提交校验")
    svc.run_action(int(rule["id"]), "标记生效")
    svc.generate_field_extracts(int(rule["id"]))
    svc.generate_field_extracts(int(rule["id"]))
    templates = [
        row for row in store.rows("field_extract")
        if row.get("模板编号") == "GEN-PARS-IDEM-1-A-v1.0-FE"
    ]
    assert len(templates) == 1


def test_seed_rules_have_event_records_and_synced_templates() -> None:
    event_svc = ParseRuleEventService()
    published_seed_rules = [r for r in store.rows("parse_rule") if r.get("status") == "已生效"]
    assert published_seed_rules
    for rule in published_seed_rules:
        latest = event_svc.latest_event(int(rule["id"]))
        assert latest is not None and latest["事件状态"] == PUBLISHED_STATUS
        assert latest["校验通过"] is True
