"""解析规则接口：维护解析规则，覆盖提交校验、标记生效、标记废弃等动作。

选择器校验结果与规则发布状态在同一动作内原子提交；校验不通过时规则保持草稿，
拦截原因写入解析规则事件记录，且不产生字段抽取模板。
"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import ActionPayload, ActionResult, PageResult
from app.services.parse_rule import ParseRuleService
from app.services.parse_rule_event import EVENT_STATUS_ORDER, ParseRuleEventService

router = APIRouter(prefix="/api/parse_rule", tags=["解析规则"])

service = ParseRuleService()
event_service = ParseRuleEventService()

LIST_FIELDS = ["规则编号", "目标字段名", "选择器类型", "选择器表达式", "提取方式", "默认值", "正则过滤", "规则版本"]
STATUSES = ["草稿", "待校验", "已生效", "已废弃"]


def query_filters(request: Request) -> dict[str, str]:
    """只接受当前模块声明过的字段，忽略分页等保留参数。"""
    return {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in LIST_FIELDS and value.strip()
    }


@router.get("", response_model=PageResult[dict])
def list_entries(
    request: Request,
    keyword: str | None = Query(default=None, description="按规则编号检索"),
    status: str | None = Query(default=None, description="草稿、待校验、已生效、已废弃"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    """按页面字段、规则编号关键字与状态过滤解析规则列表。"""
    items, total = service.list_entries(
        filters=query_filters(request), keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/event_statuses")
def event_statuses() -> dict[str, Any]:
    """返回解析规则事件记录状态机序列，供前端按序展示。"""
    return {"items": EVENT_STATUS_ORDER}


@router.get("/events")
def list_events(
    rule_id: int | None = Query(default=None, description="按解析规则ID过滤事件记录"),
    status: str | None = Query(default=None, description="按事件状态过滤"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    """查询解析规则事件记录：留存每次规则状态变化与被拦截的选择器原因。"""
    if rule_id is not None:
        items = event_service.events_for_rule(rule_id)
        if status:
            items = [row for row in items if row.get("事件状态") == status]
        total = len(items)
        start = (page - 1) * size
        return PageResult(items=items[start:start + size], total=total, page=page, size=size)
    items, total = event_service.list_entries(status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("/events/{event_id}/transition", response_model=ActionResult)
def transition_event(event_id: int, payload: ActionPayload) -> ActionResult:
    """按状态机顺序逐个流转解析规则事件记录。"""
    record, message = event_service.manual_transition(event_id, payload.action.strip(), payload.remark)
    if record is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=record)


@router.get("/export")
def export_entries(
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> StreamingResponse:
    """下载当前筛选条件下的 UTF-8 CSV；必须声明在 /{entry_id} 之前。"""
    items, _ = service.list_entries(
        filters=query_filters(request), keyword=keyword, status=status, page=1, size=1000000)
    output = io.StringIO()
    output.write("﻿")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *LIST_FIELDS, "status"])
    for item in items:
        row = []
        for value in [item.get("id"), *(item.get(field, "") for field in LIST_FIELDS), item.get("status", "")]:
            text = "" if value is None else str(value)
            row.append("'" + text if text.startswith(("=", "+", "-", "@")) else text)
        writer.writerow(row)
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="parse_rule.csv"'})


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条解析规则明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"解析规则 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: dict[str, object]) -> ActionResult:
    """登记一条解析规则，缺字段或口径/阈值/范围越界时说明原因而不是静默丢弃。"""
    entry, errors = service.create_entry(payload)
    if errors:
        return ActionResult(ok=False, message="；".join(errors))
    return ActionResult(ok=True, message="解析规则已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    """对单条解析规则执行提交校验、标记生效、标记废弃。

    选择器校验结果与发布状态同时提交：校验被拦截时返回 ok=False 且规则状态不变，
    拦截原因可在 /api/parse_rule/events 事件记录中追溯。
    """
    entry, message = service.run_action(
        entry_id, payload.action.strip(), mode=(payload.mode or "replace"), remark=payload.remark)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/generate_fields")
def generate_field_extracts(entry_id: int) -> dict:
    """基于解析规则事件记录生成字段抽取模板，仅通过选择器校验的规则会产出模板。"""
    return {"items": service.generate_field_extracts(entry_id)}


@router.post("/generate_fields_batch")
def generate_fields_batch(payload: dict[str, object]) -> dict:
    """按批为通过校验的解析规则生成字段模板，未通过校验的规则自动跳过。"""
    raw_ids = payload.get("rule_ids") or []
    try:
        rule_ids = [int(value) for value in raw_ids]
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="rule_ids 必须是整数数组")
    items = service.generate_batch_for_rules(rule_ids)
    return {"items": items, "total": len(items)}
