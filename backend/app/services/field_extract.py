"""字段抽取业务规则：模板匹配、字段精炼、输出类型转换。"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.store import store

MODULE = "field_extract"
LIST_FIELDS = ["模板编号", "关联种子", "字段名", "选择器", "精炼规则", "输出类型", "可为空", "抽取优先级"]
REQUIRED_FIELDS = ["模板编号", "关联种子", "字段名"]
STATUS_ORDER = ["草稿", "待校验", "已生效", "已废弃"]
ACTION_RULES = {"提交校验": "待校验", "标记生效": "已生效", "标记废弃": "已废弃"}
NEGATIVE_ACTIONS = []


def _refine_value(value: str, rule: str) -> tuple[str, bool]:
    """使用精炼规则处理原始值，返回(结果,是否成功)。"""
    if not rule or not value:
        return value, True
    try:
        m = re.search(rule, str(value))
        if m:
            return m.group(1) if m.lastindex else m.group(0), True
        return value, False
    except re.error:
        return value, False


def _cast_output(value: str, output_type: str) -> tuple[Any, bool]:
    """将字符串值转换为目标输出类型。"""
    t = (output_type or "").lower()
    if t == "integer":
        try:
            return int(value), True
        except (ValueError, TypeError):
            return None, False
    if t == "float":
        try:
            return float(value), True
        except (ValueError, TypeError):
            return None, False
    if t == "boolean":
        return value.lower() in ("true", "1", "yes", "是"), True
    if t == "date":
        if re.match(r"\d{4}-\d{2}-\d{2}", str(value)):
            return str(value), True
        return None, False
    return str(value), True


class FieldExtractService:
    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        for field, expected in (filters or {}).items():
            rows = [row for row in rows if expected.casefold() in str(row.get(field, "")).casefold()]
        if keyword:
            rows = [row for row in rows if keyword.casefold() in str(row.get("模板编号", "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in LIST_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        # 预计算输出示例
        sample_value = "示例文本 123.45"
        refined, refine_ok = _refine_value(sample_value, entry.get("精炼规则") or "")
        casted, cast_ok = _cast_output(refined, entry.get("输出类型"))
        entry["_preview"] = {"sample": sample_value, "refined": refined, "casted": casted, "ok": refine_ok and cast_ok}
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"抽取模板 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于字段抽取可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        if action == "提交校验":
            sample_value = "示例文本 123.45"
            refined, refine_ok = _refine_value(sample_value, entry.get("精炼规则") or "")
            casted, cast_ok = _cast_output(refined, entry.get("输出类型"))
            entry["_preview"] = {"sample": sample_value, "refined": refined, "casted": casted, "ok": refine_ok and cast_ok}
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"抽取模板已{action}"

    def extract_fields(self, entry_id: int, raw_html: str) -> dict[str, Any]:
        """对给定原始HTML执行字段抽取，返回抽取结果与元数据。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return {"ok": False, "error": "模板不存在"}
        selector = str(entry.get("选择器") or "")
        refine_rule = str(entry.get("精炼规则") or "")
        output_type = str(entry.get("输出类型") or "text")
        nullable = str(entry.get("可为空") or "").lower() in ("是", "true", "yes")
        # 模拟选择器匹配：在HTML中查找包含选择器关键文本的片段
        matched = ""
        if selector in raw_html:
            start = raw_html.find(selector)
            end = raw_html.find("</", start)
            matched = raw_html[start:end] if end > start else raw_html[start:start + 100]
        if not matched:
            matched = raw_html[:200]
        refined, refine_ok = _refine_value(matched, refine_rule)
        casted, cast_ok = _cast_output(refined, output_type)
        result = {
            "ok": cast_ok or nullable,
            "field": entry.get("字段名"),
            "matched_length": len(matched),
            "refined": refined,
            "casted": casted,
            "type": output_type,
            "nullable": nullable,
        }
        # 保存抽取结果到store供下游消费
        results = store.pipeline_state("content_extract_results") or {}
        results[f"field_{entry_id}"] = result
        store.set_pipeline_state("content_extract_results", results)
        return result

    # ------------------------------------------------------------------
    # 跨模块回写链路：消费解析规则事件记录
    # ------------------------------------------------------------------
    def consume_parse_rule_events(self) -> dict[str, Any]:
        """读取解析规则事件记录，只为本批通过校验的规则同步/确认字段模板。

        - 校验拦截事件：不生成任何模板，回写状态记为「已拦截」；
        - 校验通过/已发布事件：确保存在对应模板（由解析规则侧幂等创建），
          并把同步游标推进到该事件；
        - 已撤下/已废弃事件：停用对应模板。
        重复消费同一事件不会生成重复模板。
        """
        from app.services.parse_rule_event import (
            BLOCKED_STATUS,
            MODULE as EVENT_MODULE,
            PASS_STATUS,
            PUBLISHED_STATUS,
        )

        sync_cursor: dict[str, Any] = store.pipeline_state("parse_rule_event_sync") or {}
        # 游标按事件ID记录最近一次消费到的状态，事件状态再次变化时允许重新消费
        synced_map: dict[str, str] = sync_cursor.setdefault("synced_events", {})
        blocked_events: list[int] = sync_cursor.setdefault("blocked_event_ids", [])
        events = store.rows(EVENT_MODULE)
        created_or_confirmed = 0
        blocked = 0
        withdrawn = 0

        for event in events:
            event_id = int(event["id"])
            rule_id = int(event.get("解析规则ID", 0))
            event_status = str(event.get("事件状态") or "")
            if event_status == BLOCKED_STATUS:
                if event_id not in blocked_events:
                    blocked_events.append(event_id)
                # 拦截规则不得保留由其产生的模板
                for rel in store.get_relations("parse_to_field", source_module="parse_rule", source_id=rule_id):
                    candidate = store.find(MODULE, int(rel["target_id"]))
                    if candidate is not None and candidate.get("选择器") == event.get("选择器表达式"):
                        candidate["status"] = "已废弃"
                        candidate["pending"] = False
                blocked += 1
                synced_map.pop(str(event_id), None)
                continue
            if event_status in {"已撤下", "已废弃"}:
                template_id = event.get("关联模板ID")
                if template_id is not None:
                    template = store.find(MODULE, int(template_id))
                    if template is not None and template.get("status") != "已废弃":
                        template["status"] = "已废弃"
                        template["pending"] = False
                        withdrawn += 1
                synced_map[str(event_id)] = event_status
                continue
            if event_status not in {PASS_STATUS, PUBLISHED_STATUS} or not event.get("校验通过"):
                continue
            if synced_map.get(str(event_id)) == event_status:
                # 幂等：同一事件同一状态重复消费不重复生成模板
                continue
            rule = store.find("parse_rule", rule_id)
            if rule is None or not str(rule.get("选择器表达式") or "").strip():
                continue
            template_code = f"GEN-{rule.get('规则编号')}-{rule.get('规则版本')}-FE"
            template = store.find_by_field(MODULE, "模板编号", template_code)
            if template is None:
                payload = {
                    "模板编号": template_code,
                    "关联种子": rule.get("规则编号"),
                    "字段名": rule.get("目标字段名"),
                    "选择器": rule.get("选择器表达式"),
                    "精炼规则": rule.get("正则过滤") or "",
                    "输出类型": rule.get("提取方式") or "text",
                    "可为空": "否",
                    "抽取优先级": "10",
                }
                template, missing = self.create_entry(payload)
                if missing:
                    continue
                store.add_relation("parse_to_field", "parse_rule", rule_id, MODULE, int(template["id"]))
            template["来源规则ID"] = rule_id
            template["来源事件ID"] = event_id
            template["版本角色"] = "当前发布版本" if event_status == PUBLISHED_STATUS else template.get("版本角色", "校验通过待发布")
            synced_map[str(event_id)] = event_status
            created_or_confirmed += 1

        sync_cursor["last_consumed_at"] = datetime.now().isoformat(timespec="seconds")
        store.set_pipeline_state("parse_rule_event_sync", sync_cursor)
        return {
            "ok": True,
            "synced": created_or_confirmed,
            "blocked": blocked,
            "withdrawn": withdrawn,
            "total_events": len(events),
            "cursor": sync_cursor,
        }

    def list_synced_templates(self) -> list[dict[str, Any]]:
        """列出由解析规则事件记录同步而来的字段模板。"""
        return [row for row in store.rows(MODULE) if row.get("来源事件ID") is not None]
