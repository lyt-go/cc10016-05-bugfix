"""解析规则业务规则：选择器语法校验、口径/阈值/范围/判定条件约束、版本管理、事件记录回写。

核心约束：
- 选择器校验结果与规则发布状态必须同时提交：校验不通过的规则不得进入发布流程，
  拦截原因随解析规则事件记录留存；
- 字段抽取只读取解析规则事件记录，且只为本批通过校验的规则生成模板；
- 规则按口径（提取方式/选择器类型）、阈值（抽取优先级）、范围（规则版本）与判定条件
  （选择器表达式语法）执行，越界值不允许保存；
- 同一规则编号只允许一个当前发布版本，重复提交不得生成重复字段模板。
"""
from __future__ import annotations

import re
from typing import Any

from app.store import store

MODULE = "parse_rule"
LIST_FIELDS = ["规则编号", "目标字段名", "选择器类型", "选择器表达式", "提取方式", "默认值", "正则过滤", "规则版本"]
REQUIRED_FIELDS = ["规则编号", "目标字段名", "选择器类型"]
STATUS_ORDER = ["草稿", "待校验", "已生效", "已废弃"]
ACTION_RULES = {"提交校验": "待校验", "标记生效": "已生效", "标记废弃": "已废弃"}
NEGATIVE_ACTIONS = []

# 口径：允许的选择器类型与提取方式
ALLOWED_SELECTOR_TYPES = {"xpath", "css", "jsonpath", "regex"}
ALLOWED_EXTRACT_TYPES = {"text", "html", "attribute", "integer", "float", "boolean", "date"}
# 阈值：抽取优先级允许范围
PRIORITY_MIN, PRIORITY_MAX = 1, 100
# 范围：规则版本命名口径 vX.Y（必须带 v 前缀，数字段）
VERSION_PATTERN = re.compile(r"^v(\d+)\.(\d+)(?:\.(\d+))?$")
# 发布模式
PUBLISH_MODES = {"replace", "parallel"}


def _validate_selector(selector_type: str, expression: str) -> tuple[bool, str]:
    """按判定条件校验选择器表达式与类型是否匹配，返回(是否通过, 原因说明)。"""
    if not expression or not isinstance(expression, str):
        return False, "选择器表达式为空"
    t = (selector_type or "").lower()
    if t not in ALLOWED_SELECTOR_TYPES:
        return False, f"选择器类型「{selector_type}」不在支持范围：{sorted(ALLOWED_SELECTOR_TYPES)}"
    if t == "xpath":
        if not expression.startswith(("/", "//")):
            return False, "XPath必须以/或//开头"
        if expression.count("(") != expression.count(")") or expression.count("[") != expression.count("]"):
            return False, "XPath括号或方括号不匹配"
        if expression.count("'") % 2 != 0 or expression.count('"') % 2 != 0:
            return False, "XPath引号未闭合"
        if "::" in expression and not re.search(r"axis::|\b(?:child|parent|ancestor|descendant|following|preceding|self)::", expression):
            return False, "XPath轴名非法"
        return True, "XPath合法"
    if t == "css":
        # CSS 选择器必须包含类/ID/属性/伪类/组合等结构，或为合法标签名
        # 注意 > 是合法的子代组合符，不能当作非法字符
        if re.search(r"[<>\"']", expression.replace(">", "")):
            return False, "CSS选择器包含非法字符"
        if not re.search(r"[.#\[\]:>+~* ]", expression) and not expression.replace("-", "").isalnum():
            return False, "CSS选择器格式异常"
        if expression.count("[") != expression.count("]") or expression.count("(") != expression.count(")"):
            return False, "CSS选择器括号不匹配"
        return True, "CSS选择器合法"
    if t == "jsonpath":
        if not expression.startswith("$"):
            return False, "JSONPath必须以$开头"
        if expression.count("[") != expression.count("]") or expression.count("(") != expression.count(")"):
            return False, "JSONPath括号不匹配"
        return True, "JSONPath合法"
    if t == "regex":
        try:
            re.compile(expression)
            return True, "正则合法"
        except re.error as exc:
            return False, f"正则错误: {exc}"
    return False, f"选择器类型「{selector_type}」不在支持范围"


def _validate_bounds(values: dict[str, Any]) -> list[str]:
    """校验口径、阈值、范围等越界条件，返回越界原因列表（空列表表示全部通过）。"""
    errors: list[str] = []
    selector_type = str(values.get("选择器类型") or "").strip().lower()
    if selector_type and selector_type not in ALLOWED_SELECTOR_TYPES:
        errors.append(f"选择器类型口径越界：{selector_type}，允许 {sorted(ALLOWED_SELECTOR_TYPES)}")
    extract_type = str(values.get("提取方式") or "").strip().lower()
    if extract_type and extract_type not in ALLOWED_EXTRACT_TYPES:
        errors.append(f"提取方式口径越界：{extract_type}，允许 {sorted(ALLOWED_EXTRACT_TYPES)}")
    priority_raw = values.get("抽取优先级")
    if priority_raw not in (None, ""):
        try:
            priority = int(str(priority_raw))
            if not (PRIORITY_MIN <= priority <= PRIORITY_MAX):
                errors.append(f"抽取优先级阈值越界：{priority}，允许 {PRIORITY_MIN}-{PRIORITY_MAX}")
        except (ValueError, TypeError):
            errors.append(f"抽取优先级阈值越界：{priority_raw} 不是整数")
    version = str(values.get("规则版本") or "").strip()
    if version and not VERSION_PATTERN.match(version):
        errors.append(f"规则版本范围越界：{version}，需符合 vX.Y 或 vX.Y.Z 格式")
    return errors


def _version_key(version: str) -> tuple[int, int, int]:
    match = VERSION_PATTERN.match(str(version or "").strip())
    if not match:
        return (0, 0, 0)
    parts = match.groups(default="0")
    return tuple(int(p) for p in parts)  # type: ignore[return-value]


class ParseRuleService:
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
            rows = [row for row in rows if keyword.casefold() in str(row.get("规则编号", "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        enriched = []
        for row in rows[start:start + size]:
            d = dict(row)
            d["_field_count"] = len(store.get_relations("parse_to_field", source_module=MODULE, source_id=d["id"]))
            from app.services.parse_rule_event import ParseRuleEventService
            latest = ParseRuleEventService().latest_event(int(d["id"]))
            d["_event_status"] = latest.get("事件状态") if latest else None
            d["_version_role"] = latest.get("版本角色") if latest else ""
            enriched.append(d)
        return enriched, total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        d = dict(entry)
        d["_field_count"] = len(store.get_relations("parse_to_field", source_module=MODULE, source_id=entry_id))
        from app.services.parse_rule_event import ParseRuleEventService
        event_svc = ParseRuleEventService()
        latest = event_svc.latest_event(entry_id)
        d["_event_status"] = latest.get("事件状态") if latest else None
        d["_events"] = event_svc.events_for_rule(entry_id)
        return d

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, [f"缺少必填字段：{field}" for field in missing]
        # 口径/阈值/范围越界值不允许保存
        bound_errors = _validate_bounds(values)
        if bound_errors:
            return None, bound_errors
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in LIST_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        # 登记时即做选择器判定，校验结果随规则数据同时落库
        ok, msg = _validate_selector(entry.get("选择器类型"), entry.get("选择器表达式") or "")
        entry["_selector_check"] = {"ok": ok, "msg": msg}
        entry["abnormal"] = not ok
        rows.append(entry)
        # 生成已登记事件记录
        from app.services.parse_rule_event import ParseRuleEventService
        ParseRuleEventService().record_registration(entry, entry["_selector_check"])
        # 绑定种子
        seed_id = values.get("关联种子ID")
        if seed_id:
            try:
                sid = int(seed_id)
                store.add_relation("seed_to_parse_rule", "seed_config", sid, MODULE, entry["id"])
            except (ValueError, TypeError):
                pass
        return entry, []

    def run_action(
        self, entry_id: int, action: str, *, mode: str = "replace", remark: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        from app.services.parse_rule_event import ParseRuleEventService
        event_svc = ParseRuleEventService()
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"解析规则 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于解析规则可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        if mode not in PUBLISH_MODES:
            return None, f"发布模式「{mode}」非法，允许 {sorted(PUBLISH_MODES)}"
        # 状态机按顺序逐个流转，禁止跳步/重复流转
        current_idx = STATUS_ORDER.index(entry["status"])
        target_idx = STATUS_ORDER.index(target)
        if target_idx != current_idx + 1:
            return None, f"状态机要求按序流转：当前「{entry['status']}」不能直接变更为「{target}」"

        if action == "标记废弃":
            event_svc.apply_deprecate(entry, remark or "人工标记废弃")
            entry["status"] = target
            entry["pending"] = target != STATUS_ORDER[-1]
            entry["abnormal"] = False
            return entry, "解析规则已标记废弃"

        batch_id = event_svc.new_batch_id()
        if action == "提交校验":
            # 选择器校验结果与发布状态同时提交；选择器不合法或口径/阈值/范围越界都拦截
            bound_errors = _validate_bounds(entry)
            selector_ok, selector_msg = _validate_selector(
                entry.get("选择器类型"), entry.get("选择器表达式") or "")
            if not selector_ok:
                check = {"ok": False, "msg": selector_msg}
            elif bound_errors:
                check = {"ok": False, "msg": "；".join(bound_errors)}
            else:
                check = {"ok": True, "msg": selector_msg}
            entry["_selector_check"] = check
            event_svc.apply_validation(entry, check, batch_id)
            if not check["ok"]:
                # 拦截：规则保持草稿，不得发布；拦截原因已写入事件记录
                entry["abnormal"] = True
                entry["pending"] = True
                return None, f"选择器校验未通过，发布被拦截：{check['msg']}"
            entry["status"] = target
            entry["pending"] = True
            entry["abnormal"] = False
            return entry, "解析规则已提交校验，选择器校验通过"

        # 标记生效（发布）
        if entry["status"] != "待校验":
            return None, f"规则未通过校验（当前状态「{entry['status']}」），不允许发布"
        check = entry.get("_selector_check") or {"ok": False, "msg": "缺少选择器校验结果"}
        if not check.get("ok"):
            event_svc.apply_validation(entry, check, batch_id)
            return None, f"选择器校验未通过，发布被拦截：{check.get('msg', '')}"
        conflict = self._version_conflict(entry)
        if conflict:
            return None, conflict
        # 先生成模板并经跨模块回写链路同步（不触发版本冲突处理，状态机尚未发布）
        templates = self.generate_field_extracts(entry_id, mode=mode)
        template_id = int(templates[0]["id"]) if templates else None
        # 事件状态机按序进入已发布：校验通过 -> 已发布
        event_svc.apply_publish(entry, batch_id, mode, "当前发布版本", template_id)
        if template_id is not None:
            event_svc.mark_synced(int(event_svc.latest_event(entry_id)["id"]), template_id)
            # 发布落地后再处理 replace/parallel 版本冲突与模板版本角色标注
            self._apply_publish_mode(entry, mode, template_id)
        entry["status"] = target
        entry["pending"] = False
        entry["abnormal"] = False
        return entry, f"解析规则已发布（{mode}模式），生成字段模板 {len(templates)} 个"

    # ------------------------------------------------------------------
    # 版本冲突与发布模式
    # ------------------------------------------------------------------
    def _rule_group_key(self, code: str) -> str:
        """同一规则主数据的并行版本按「字母前缀」归组，末尾数字序号是版本号。

        PARS-ACC-C-01 / PARS-ACC-C-02 同组（前缀 PARS-ACC-C），
        PARS-E001-A 与 PARS-N002-A 前缀不同，不属于同组。
        """
        return re.sub(r"-?\d+$", "", str(code or ""))

    def _sibling_rules(self, entry: dict[str, Any]) -> list[dict[str, Any]]:
        group = self._rule_group_key(str(entry.get("规则编号") or ""))
        return [
            row for row in store.rows(MODULE)
            if int(row.get("id", 0)) != int(entry["id"])
            and self._rule_group_key(str(row.get("规则编号") or "")) == group
        ]

    def _version_conflict(self, entry: dict[str, Any]) -> str:
        """历史口径优先级判定：发布版本不得低于现存当前发布版本。"""
        incoming = _version_key(str(entry.get("规则版本") or ""))
        for sibling in self._sibling_rules(entry):
            if sibling.get("status") == "已生效":
                existing = _version_key(str(sibling.get("规则版本") or ""))
                if incoming < existing:
                    return (
                        f"版本冲突：规则版本 {entry.get('规则版本')} 低于已发布版本 "
                        f"{sibling.get('规则版本')}，历史口径优先级不允许覆盖当前发布版本"
                    )
        return ""

    def _apply_publish_mode(self, entry: dict[str, Any], mode: str, new_template_id: int) -> None:
        """处理同一规则编号下的版本冲突：replace 撤下旧模板，parallel 双模板并存。"""
        from app.services.parse_rule_event import ParseRuleEventService
        event_svc = ParseRuleEventService()
        siblings = self._sibling_rules(entry)
        if mode == "replace":
            for sibling in siblings:
                if sibling.get("status") == "已生效":
                    sibling["status"] = "已废弃"
                    sibling["pending"] = False
                    event_svc.apply_withdraw(sibling, f"新版本 {entry.get('规则版本')} 发布，旧模板撤下")
                    event_svc.mark_role(int(sibling["id"]), "历史版本")
                    for rel in store.get_relations("parse_to_field", source_module=MODULE, source_id=int(sibling["id"])):
                        old_template = store.find("field_extract", int(rel["target_id"]))
                        if old_template is not None:
                            old_template["status"] = "已废弃"
                            old_template["pending"] = False
                            old_template["版本角色"] = "历史版本（已停用）"
            event_svc.mark_role(int(entry["id"]), "当前发布版本")
        else:  # parallel：新旧选择器各自产出字段，明确当前发布版本与历史版本
            for sibling in siblings:
                if sibling.get("status") == "已生效":
                    event_svc.mark_role(int(sibling["id"]), "历史版本")
                    for rel in store.get_relations("parse_to_field", source_module=MODULE, source_id=int(sibling["id"])):
                        old_template = store.find("field_extract", int(rel["target_id"]))
                        if old_template is not None:
                            old_template["版本角色"] = "历史版本"
            event_svc.mark_role(int(entry["id"]), "当前发布版本")
        new_template = store.find("field_extract", new_template_id)
        if new_template is not None:
            new_template["版本角色"] = "当前发布版本"
            new_template["来源规则ID"] = int(entry["id"])

    # ------------------------------------------------------------------
    # 字段模板生成（跨模块回写链路入口）
    # ------------------------------------------------------------------
    def generate_field_extracts(self, entry_id: int, *, batch_id: str | None = None,
                                mode: str = "replace") -> list[dict[str, Any]]:
        """基于解析规则事件记录生成字段抽取模板，只为通过校验的规则产出。"""
        from app.services.parse_rule_event import ParseRuleEventService
        event_svc = ParseRuleEventService()
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return []
        latest = event_svc.latest_event(entry_id)
        check = entry.get("_selector_check") or {"ok": False, "msg": "缺少选择器校验结果"}
        # 闸门：事件记录必须处于校验通过/已发布（或已登记即带通过结果），空选择器绝不产出模板
        eligible_event_statuses = {"已登记", "校验通过", "已发布", "已撤下"}
        if not check.get("ok") or not str(entry.get("选择器表达式") or "").strip():
            return []
        if latest is not None and latest.get("事件状态") not in eligible_event_statuses:
            return []
        if latest is not None and not latest.get("校验通过", False):
            return []

        from app.services.field_extract import FieldExtractService
        svc = FieldExtractService()
        field_name = str(entry.get("目标字段名") or "")
        selector = str(entry.get("选择器表达式") or "")
        extract_type = str(entry.get("提取方式") or "text")
        # 模板编号带版本，重复提交命中同一编号，绝不生成重复模板
        payload = {
            "模板编号": f"GEN-{entry.get('规则编号')}-{entry.get('规则版本')}-FE",
            "关联种子": entry.get("规则编号"),
            "字段名": field_name,
            "选择器": selector,
            "精炼规则": entry.get("正则过滤") or "",
            "输出类型": extract_type,
            "可为空": "否",
            "抽取优先级": "10",
        }
        existing = store.find_by_field("field_extract", "模板编号", payload["模板编号"])
        if existing is not None:
            templates = [existing]
            new_id = int(existing["id"])
        else:
            new_entry, _ = svc.create_entry(payload)
            if not new_entry:
                return []
            new_id = int(new_entry["id"])
            store.add_relation("parse_to_field", MODULE, entry_id, "field_extract", new_id)
            templates = [new_entry]
        # 事件记录回写：关联模板与同步状态，并经跨模块链路同步给字段抽取
        if latest is not None:
            event_svc.mark_synced(int(latest["id"]), new_id)
            synced_template = store.find("field_extract", new_id)
            if synced_template is not None:
                synced_template["来源规则ID"] = entry_id
                synced_template["来源事件ID"] = int(latest["id"])
        # 仅显式发布（调用方传入批次）才处理并行/替换版本冲突
        if batch_id is not None:
            self._apply_publish_mode(entry, mode, new_id)
        svc.consume_parse_rule_events()
        return templates

    def generate_batch_for_rules(self, rule_ids: list[int], *, batch_id: str | None = None) -> list[dict[str, Any]]:
        """按批为通过校验的规则生成模板，未通过校验的规则跳过。"""
        generated: list[dict[str, Any]] = []
        for rule_id in rule_ids:
            generated.extend(self.generate_field_extracts(rule_id, batch_id=batch_id))
        return generated
