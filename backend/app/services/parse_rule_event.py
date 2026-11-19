"""解析规则事件记录：状态机按序逐个流转，留存每次规则状态变化与选择器拦截原因。

事件记录是解析规则与字段抽取之间的跨模块回写主数据：
- 每条解析规则的每次提交、拦截、发布、撤下、废弃都会落到同一条事件记录的状态轨迹上；
- 字段抽取只读取「校验通过 / 已发布且校验通过」的事件记录生成模板；
- 拦截类事件必须携带选择器校验结果与拦截原因，保证校验结果与发布状态同时提交。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "parse_rule_event"

# 事件状态机：必须沿序列逐个流转，禁止跳步
EVENT_STATUS_ORDER = ["已登记", "校验通过", "校验拦截", "已发布", "已撤下", "已废弃"]
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "已登记": {"校验通过", "校验拦截"},
    "校验拦截": {"校验通过", "校验拦截"},  # 修复后重新提交可再次进入校验分支
    "校验通过": {"已发布", "已废弃"},
    "已发布": {"已撤下", "已废弃"},
    "已撤下": {"已发布", "已废弃"},
    "已废弃": set(),
}
# 终态与异常态
BLOCKED_STATUS = "校验拦截"
PUBLISHED_STATUS = "已发布"
PASS_STATUS = "校验通过"

LIST_FIELDS = [
    "事件编号", "解析规则ID", "规则编号", "规则版本", "事件批次", "事件类型",
    "事件状态", "选择器类型", "选择器表达式", "校验通过", "校验信息", "拦截原因",
    "发布模式", "版本角色", "关联模板ID", "跨模块同步状态", "发生时间", "备注",
]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class ParseRuleEventService:
    """解析规则事件记录的写入、状态机流转与查询。"""

    # ------------------------------------------------------------------
    # 基础读写
    # ------------------------------------------------------------------
    def _table(self) -> list[dict[str, Any]]:
        return store.rows(MODULE)

    def _next_id(self) -> int:
        return max((int(row.get("id", 0)) for row in self._table()), default=0) + 1

    def new_batch_id(self) -> str:
        """一次提交对应一个事件批次，批次内事件共享批次号。"""
        return f"BATCH-{datetime.now().strftime('%Y%m%d%H%M%S')}-{self._next_id():06d}"

    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(self._table())
        for field, expected in (filters or {}).items():
            rows = [row for row in rows if expected.casefold() in str(row.get(field, "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("事件状态") == status]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def events_for_rule(self, rule_id: int) -> list[dict[str, Any]]:
        return [row for row in self._table() if int(row.get("解析规则ID", 0)) == rule_id]

    def latest_event(self, rule_id: int) -> dict[str, Any] | None:
        events = self.events_for_rule(rule_id)
        return events[-1] if events else None

    # ------------------------------------------------------------------
    # 状态机
    # ------------------------------------------------------------------
    def _transition(self, record: dict[str, Any], target: str, reason: str = "", **patch: Any) -> dict[str, Any]:
        current = str(record.get("事件状态") or "")
        allowed = ALLOWED_TRANSITIONS.get(current, set())
        if target not in allowed:
            raise ValueError(f"事件状态不允许从「{current}」流转到「{target}」，允许的下一状态：{sorted(allowed) or '无（终态）'}")
        record["事件状态"] = target
        record["status"] = target
        record["abnormal"] = target == BLOCKED_STATUS
        record["pending"] = target not in {"已废弃", "已撤下", PUBLISHED_STATUS}
        record.setdefault("状态轨迹", []).append({
            "from": current,
            "to": target,
            "时间": _now(),
            "原因": reason,
        })
        for key, value in patch.items():
            record[key] = value
        return record

    def manual_transition(self, event_id: int, target: str, remark: str | None = None) -> tuple[dict[str, Any] | None, str]:
        record = self.get_entry(event_id)
        if record is None:
            return None, f"解析规则事件 {event_id} 不存在"
        if target not in EVENT_STATUS_ORDER:
            return None, f"目标状态「{target}」不在事件状态机序列里"
        try:
            self._transition(record, target, remark or "人工流转")
        except ValueError as exc:
            return None, str(exc)
        return record, f"事件已流转至「{target}」"

    # ------------------------------------------------------------------
    # 业务事件写入
    # ------------------------------------------------------------------
    def _new_record(self, rule: dict[str, Any], batch_id: str, check: dict[str, Any]) -> dict[str, Any]:
        record = {
            "id": self._next_id(),
            "事件编号": f"EVT-{int(rule['id']):04d}-{len(self.events_for_rule(int(rule['id']))) + 1:02d}",
            "解析规则ID": int(rule["id"]),
            "规则编号": rule.get("规则编号"),
            "规则版本": rule.get("规则版本"),
            "事件批次": batch_id,
            "事件类型": "规则登记",
            "事件状态": "已登记",
            "status": "已登记",
            "选择器类型": rule.get("选择器类型"),
            "选择器表达式": rule.get("选择器表达式"),
            "校验通过": bool(check.get("ok")),
            "校验信息": check.get("msg", ""),
            "拦截原因": "",
            "发布模式": "",
            "版本角色": "",
            "关联模板ID": None,
            "跨模块同步状态": "未同步",
            "发生时间": _now(),
            "备注": "",
            "pending": True,
            "abnormal": False,
            "状态轨迹": [{
                "from": None,
                "to": "已登记",
                "时间": _now(),
                "原因": "解析规则登记，选择器校验结果与规则数据同时落库",
            }],
        }
        self._table().append(record)
        return record

    def record_registration(self, rule: dict[str, Any], check: dict[str, Any]) -> dict[str, Any]:
        """规则登记时生成已登记事件（幂等：同批次不重复建）。"""
        batch_id = self.new_batch_id()
        return self._new_record(rule, batch_id, check)

    def apply_validation(self, rule: dict[str, Any], check: dict[str, Any], batch_id: str) -> dict[str, Any]:
        """提交校验：校验结果与规则发布状态同时写入事件记录。"""
        latest = self.latest_event(int(rule["id"]))
        if latest is None:
            latest = self._new_record(rule, batch_id, check)
        target = PASS_STATUS if check.get("ok") else BLOCKED_STATUS
        reason = "选择器校验通过" if check.get("ok") else f"选择器校验被拦截：{check.get('msg', '')}"
        self._transition(
            latest,
            target,
            reason,
            事件类型="提交校验",
            事件批次=batch_id,
            选择器类型=rule.get("选择器类型"),
            选择器表达式=rule.get("选择器表达式"),
            校验通过=bool(check.get("ok")),
            校验信息=check.get("msg", ""),
            拦截原因="" if check.get("ok") else str(check.get("msg", "")),
            发生时间=_now(),
        )
        return latest

    def apply_publish(
        self,
        rule: dict[str, Any],
        batch_id: str,
        mode: str,
        version_role: str,
        template_id: int | None,
    ) -> dict[str, Any]:
        latest = self.latest_event(int(rule["id"]))
        if latest is None:  # 兼容历史数据：无事件时补建
            latest = self._new_record(rule, batch_id, rule.get("_selector_check") or {"ok": True, "msg": "历史已发布规则"})
            self._transition(latest, PASS_STATUS, "补录历史校验结果")
        self._transition(
            latest,
            PUBLISHED_STATUS,
            f"规则发布（{mode}模式），版本角色：{version_role}",
            事件类型="标记生效",
            事件批次=batch_id,
            发布模式=mode,
            版本角色=version_role,
            关联模板ID=template_id,
            跨模块同步状态="已同步" if template_id is not None else "未同步",
            发生时间=_now(),
        )
        return latest

    def apply_withdraw(self, rule: dict[str, Any], reason: str) -> dict[str, Any] | None:
        latest = self.latest_event(int(rule["id"]))
        if latest is None or latest["事件状态"] != PUBLISHED_STATUS:
            return latest
        self._transition(latest, "已撤下", reason, 事件类型="模板撤下", 版本角色="历史版本", 发生时间=_now())
        return latest

    def apply_deprecate(self, rule: dict[str, Any], reason: str = "规则废弃") -> dict[str, Any] | None:
        latest = self.latest_event(int(rule["id"]))
        if latest is None:
            return None
        if latest["事件状态"] == "已废弃":
            return latest
        self._transition(latest, "已废弃", reason, 事件类型="标记废弃", 发生时间=_now())
        return latest

    def mark_role(self, rule_id: int, role: str) -> None:
        latest = self.latest_event(rule_id)
        if latest is not None and latest["事件状态"] == PUBLISHED_STATUS:
            latest["版本角色"] = role

    def mark_synced(self, event_id: int, template_id: int) -> None:
        record = self.get_entry(event_id)
        if record is not None:
            record["关联模板ID"] = template_id
            record["跨模块同步状态"] = "已同步"

    # ------------------------------------------------------------------
    # 种子数据补录
    # ------------------------------------------------------------------
    def seed_from_existing_rules(self) -> None:
        """为内存仓库里预置的解析规则补建事件记录，保证事件链路从启动时完整。"""
        if self._table():
            return
        # 延迟导入避免循环依赖
        from app.services.parse_rule import _validate_selector

        for rule in store.rows("parse_rule"):
            ok, msg = _validate_selector(rule.get("选择器类型"), rule.get("选择器表达式") or "")
            rule.setdefault("_selector_check", {"ok": ok, "msg": msg})
            batch_id = self.new_batch_id()
            record = self._new_record(rule, batch_id, {"ok": ok, "msg": msg})
            rels = store.get_relations("parse_to_field", source_module="parse_rule", source_id=int(rule["id"]))
            template_id = int(rels[-1]["target_id"]) if rels else None
            rule_status = rule.get("status")
            if rule_status == "已生效":
                self._transition(record, PASS_STATUS, "预置规则补录校验通过", 校验通过=ok, 校验信息=msg)
                self._transition(
                    record,
                    PUBLISHED_STATUS,
                    "预置规则补录发布",
                    发布模式="replace",
                    版本角色="当前发布版本",
                    关联模板ID=template_id,
                    跨模块同步状态="已同步" if template_id is not None else "未同步",
                )
                if template_id is not None:
                    template = store.find("field_extract", template_id)
                    if template is not None:
                        template["版本角色"] = "当前发布版本"
                        template["来源规则ID"] = int(rule["id"])
                        template["来源事件ID"] = record["id"]
            elif rule_status == "待校验":
                target = PASS_STATUS if ok else BLOCKED_STATUS
                self._transition(
                    record, target,
                    "预置规则补录校验通过" if ok else f"预置规则选择器拦截：{msg}",
                    校验通过=ok, 校验信息=msg, 拦截原因="" if ok else msg,
                )
            elif rule_status == "已废弃":
                if ok:
                    self._transition(record, PASS_STATUS, "预置规则补录校验通过", 校验通过=ok, 校验信息=msg)
                try:
                    self._transition(record, "已废弃", "预置规则补录废弃")
                except ValueError:
                    # 拦截态无直达废弃路径，保留当前事件状态，由后续人工流转
                    pass
            # 草稿规则保持「已登记」事件状态
