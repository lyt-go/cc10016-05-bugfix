#!/usr/bin/env python3
"""HTTP 验收脚本：提交无效 XPath 与有效 CSS 各一次，核对发布状态、拒绝原因和模板数量。

用法：先启动 uvicorn（端口 8011），再运行本脚本。
"""
from __future__ import annotations

import json
import os
import urllib.request

BASE = os.environ.get("ACCEPT_BASE", "http://127.0.0.1:8011/api")


def call(method: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def main() -> None:
    # 1. 无效 XPath
    inv = call("POST", "/parse_rule", {
        "规则编号": "PARS-ACC-X-01", "目标字段名": "标题", "选择器类型": "xpath",
        "选择器表达式": "div[class=oops", "提取方式": "text", "规则版本": "v1.0"})
    inv_id = inv["entry"]["id"]
    assert inv["entry"]["_selector_check"]["ok"] is False
    blocked = call("POST", f"/parse_rule/{inv_id}/actions", {"action": "提交校验"})
    assert blocked["ok"] is False and "拦截" in blocked["message"]
    detail = call("GET", f"/parse_rule/{inv_id}")
    assert detail["status"] == "草稿", "无效规则不得被标成已发布"
    events = call("GET", f"/parse_rule/events?rule_id={inv_id}")["items"]
    assert events[-1]["事件状态"] == "校验拦截" and events[-1]["拦截原因"]
    assert call("POST", f"/parse_rule/{inv_id}/generate_fields")["items"] == []
    print(f"[OK] 无效 XPath：发布被拦截，规则保持草稿，事件原因={events[-1]['拦截原因']}，模板数=0")

    # 2. 有效 CSS
    val = call("POST", "/parse_rule", {
        "规则编号": "PARS-ACC-C-01", "目标字段名": "价格", "选择器类型": "css",
        "选择器表达式": ".price > span", "提取方式": "text", "规则版本": "v1.0"})
    val_id = val["entry"]["id"]
    assert val["entry"]["_selector_check"]["ok"] is True
    submitted = call("POST", f"/parse_rule/{val_id}/actions", {"action": "提交校验"})
    assert submitted["entry"]["status"] == "待校验"
    published = call("POST", f"/parse_rule/{val_id}/actions",
                     {"action": "标记生效", "mode": "replace"})
    assert published["ok"] and published["entry"]["status"] == "已生效"
    pub_events = call("GET", f"/parse_rule/events?rule_id={val_id}")["items"]
    assert pub_events[-1]["事件状态"] == "已发布"
    assert pub_events[-1]["版本角色"] == "当前发布版本"
    # 重复生成保持幂等
    call("POST", f"/parse_rule/{val_id}/generate_fields")
    call("POST", f"/parse_rule/{val_id}/generate_fields")
    synced = call("GET", "/field_extract/synced_from_events")["items"]
    mine = [t for t in synced if t["模板编号"] == "GEN-PARS-ACC-C-01-v1.0-FE"]
    assert len(mine) == 1, f"重复提交生成了 {len(mine)} 个模板"
    print(f"[OK] 有效 CSS：发布成功，事件=已发布/当前发布版本，模板数=1（重复提交不新增）")

    # 3. 并行版本与历史口径
    v2 = call("POST", "/parse_rule", {
        "规则编号": "PARS-ACC-C-02", "目标字段名": "价格", "选择器类型": "css",
        "选择器表达式": ".price b", "提取方式": "text", "规则版本": "v2.0"})
    v2_id = v2["entry"]["id"]
    call("POST", f"/parse_rule/{v2_id}/actions", {"action": "提交校验"})
    par = call("POST", f"/parse_rule/{v2_id}/actions", {"action": "标记生效", "mode": "parallel"})
    assert par["ok"]
    old_role = call("GET", f"/parse_rule/events?rule_id={val_id}")["items"][-1]["版本角色"]
    new_role = call("GET", f"/parse_rule/events?rule_id={v2_id}")["items"][-1]["版本角色"]
    assert old_role == "历史版本" and new_role == "当前发布版本"
    print("[OK] parallel 模式：新旧模板并存，事件分别标注历史版本/当前发布版本")

    v0 = call("POST", "/parse_rule", {
        "规则编号": "PARS-ACC-C-03", "目标字段名": "价格", "选择器类型": "css",
        "选择器表达式": ".old", "提取方式": "text", "规则版本": "v0.5"})
    v0_id = v0["entry"]["id"]
    call("POST", f"/parse_rule/{v0_id}/actions", {"action": "提交校验"})
    low = call("POST", f"/parse_rule/{v0_id}/actions", {"action": "标记生效"})
    assert low["ok"] is False and "版本冲突" in low["message"]
    print(f"[OK] 历史口径优先级：低版本发布被拦截，原因={low['message']}")

    # 4. 回写链路
    consumed = call("POST", "/field_extract/consume_parse_rule_events")
    assert consumed["ok"] and consumed["blocked"] >= 1
    print(f"[OK] 跨模块回写：blocked={consumed['blocked']} total_events={consumed['total_events']}")
    print("\n全部验收通过")


if __name__ == "__main__":
    main()
