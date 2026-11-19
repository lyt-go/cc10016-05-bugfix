"""接口出入参模型：列表分页、动作结果与各模块的明细结构。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20


class ActionResult(BaseModel):
    ok: bool
    message: str
    entry: dict[str, Any] | None = None


class EntryPayload(BaseModel):
    """登记或修改一条业务记录时提交的字段集合。"""

    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None


class ActionPayload(BaseModel):
    """状态动作使用独立契约，避免与登记的 values 载荷混用。"""

    action: str
    remark: str | None = None
    # 解析规则发布模式：replace 撤下旧模板，parallel 新旧模板并存
    mode: str | None = None



class CrawlSourceEntry(BaseModel):
    """采集源明细结构。"""

    field_0: str | None = None  # 源标识
    field_1: str | None = None  # 源名称
    field_2: str | None = None  # 协议类型
    field_3: str | None = None  # 基础URL
    field_4: str | None = None  # 认证方式
    field_5: str | None = None  # 地区归属
    field_6: str | None = None  # 标签分类
    field_7: str | None = None  # 最近采集时间

class SeedConfigEntry(BaseModel):
    """采集种子明细结构。"""

    field_0: str | None = None  # 种子编号
    field_1: str | None = None  # 目标URL模式
    field_2: str | None = None  # 列表页XPath
    field_3: str | None = None  # 详情页XPath
    field_4: str | None = None  # 分页参数
    field_5: str | None = None  # 排序字段
    field_6: str | None = None  # 去重键
    field_7: str | None = None  # 种子优先级

class ParseRuleEntry(BaseModel):
    """解析规则明细结构。"""

    field_0: str | None = None  # 规则编号
    field_1: str | None = None  # 目标字段名
    field_2: str | None = None  # 选择器类型
    field_3: str | None = None  # 选择器表达式
    field_4: str | None = None  # 提取方式
    field_5: str | None = None  # 默认值
    field_6: str | None = None  # 正则过滤
    field_7: str | None = None  # 规则版本

class RateLimitEntry(BaseModel):
    """限流策略明细结构。"""

    field_0: str | None = None  # 策略编号
    field_1: str | None = None  # 目标源
    field_2: str | None = None  # 请求间隔
    field_3: str | None = None  # 并发上限
    field_4: str | None = None  # 日请求总量
    field_5: str | None = None  # 冷却时长
    field_6: str | None = None  # 队列上限
    field_7: str | None = None  # 优先级

class ProxyPoolEntry(BaseModel):
    """代理节点明细结构。"""

    field_0: str | None = None  # 节点编号
    field_1: str | None = None  # 代理地址
    field_2: str | None = None  # 端口号
    field_3: str | None = None  # 协议类型
    field_4: str | None = None  # 地域标识
    field_5: str | None = None  # 响应延迟
    field_6: str | None = None  # 成功率
    field_7: str | None = None  # 失效次数

class SessionMgrEntry(BaseModel):
    """采集会话明细结构。"""

    field_0: str | None = None  # 会话编号
    field_1: str | None = None  # 关联源
    field_2: str | None = None  # 登录状态
    field_3: str | None = None  # 最后活动
    field_4: str | None = None  # 请求计数
    field_5: str | None = None  # Cookie摘要
    field_6: str | None = None  # UserAgent
    field_7: str | None = None  # 会话时长

class RetryPolicyEntry(BaseModel):
    """重试策略明细结构。"""

    field_0: str | None = None  # 策略编号
    field_1: str | None = None  # 目标源
    field_2: str | None = None  # 最大重试次数
    field_3: str | None = None  # 退避算法
    field_4: str | None = None  # 初始延迟
    field_5: str | None = None  # 可重试异常
    field_6: str | None = None  # 不可重试异常
    field_7: str | None = None  # 策略版本

class FieldExtractEntry(BaseModel):
    """抽取模板明细结构。"""

    field_0: str | None = None  # 模板编号
    field_1: str | None = None  # 关联种子
    field_2: str | None = None  # 字段名
    field_3: str | None = None  # 选择器
    field_4: str | None = None  # 精炼规则
    field_5: str | None = None  # 输出类型
    field_6: str | None = None  # 可为空
    field_7: str | None = None  # 抽取优先级

class AntiSpiderEntry(BaseModel):
    """对抗策略明细结构。"""

    field_0: str | None = None  # 策略编号
    field_1: str | None = None  # 目标源
    field_2: str | None = None  # 反爬类型
    field_3: str | None = None  # 触发条件
    field_4: str | None = None  # 应对方式
    field_5: str | None = None  # 冷静时长
    field_6: str | None = None  # 切换代理
    field_7: str | None = None  # 标识伪造

class PageRenderEntry(BaseModel):
    """渲染配置明细结构。"""

    field_0: str | None = None  # 配置编号
    field_1: str | None = None  # 目标源
    field_2: str | None = None  # 渲染引擎
    field_3: str | None = None  # 超时时间
    field_4: str | None = None  # 拦截规则
    field_5: str | None = None  # 屏幕尺寸
    field_6: str | None = None  # 滚动策略
    field_7: str | None = None  # 资源白名单

class DataValidateEntry(BaseModel):
    """校验规则明细结构。"""

    field_0: str | None = None  # 规则编号
    field_1: str | None = None  # 目标字段
    field_2: str | None = None  # 校验函数
    field_3: str | None = None  # 合法范围
    field_4: str | None = None  # 异常动作
    field_5: str | None = None  # 采样比例
    field_6: str | None = None  # 校验阶段
    field_7: str | None = None  # 阻断策略

class IncreCursorEntry(BaseModel):
    """增量标记明细结构。"""

    field_0: str | None = None  # 标记编号
    field_1: str | None = None  # 关联种子
    field_2: str | None = None  # 增量字段
    field_3: str | None = None  # 上次位置
    field_4: str | None = None  # 当前位置
    field_5: str | None = None  # 增量类型
    field_6: str | None = None  # 覆写策略
    field_7: str | None = None  # 更新间隔

class DedupFilterEntry(BaseModel):
    """去重规则明细结构。"""

    field_0: str | None = None  # 规则编号
    field_1: str | None = None  # 目标源
    field_2: str | None = None  # 去重键列
    field_3: str | None = None  # 比较方式
    field_4: str | None = None  # 相似度阈值
    field_5: str | None = None  # 时间窗口
    field_6: str | None = None  # 冲突策略
    field_7: str | None = None  # 规则版本

class FormatConvertEntry(BaseModel):
    """转换配置明细结构。"""

    field_0: str | None = None  # 配置编号
    field_1: str | None = None  # 源格式
    field_2: str | None = None  # 目标格式
    field_3: str | None = None  # 映射规则
    field_4: str | None = None  # 字符编码
    field_5: str | None = None  # 嵌套展开
    field_6: str | None = None  # 数组拍平
    field_7: str | None = None  # 缺失处理

class CrawlScheduleEntry(BaseModel):
    """采集计划明细结构。"""

    field_0: str | None = None  # 计划编号
    field_1: str | None = None  # 关联种子
    field_2: str | None = None  # Cron表达式
    field_3: str | None = None  # 并行批次
    field_4: str | None = None  # 优先级
    field_5: str | None = None  # 依赖条件
    field_6: str | None = None  # 失败动作
    field_7: str | None = None  # 计划版本

class ResultStoreEntry(BaseModel):
    """落地配置明细结构。"""

    field_0: str | None = None  # 配置编号
    field_1: str | None = None  # 输出格式
    field_2: str | None = None  # 存储路径
    field_3: str | None = None  # 分区规则
    field_4: str | None = None  # 压缩算法
    field_5: str | None = None  # 保留天数
    field_6: str | None = None  # 写入批次
    field_7: str | None = None  # 回滚水位

class CrawlMonitorEntry(BaseModel):
    """监控指标明细结构。"""

    field_0: str | None = None  # 指标编号
    field_1: str | None = None  # 关联源
    field_2: str | None = None  # 成功率
    field_3: str | None = None  # 平均延迟
    field_4: str | None = None  # 失败分类
    field_5: str | None = None  # 采集量
    field_6: str | None = None  # 数据量
    field_7: str | None = None  # 最近告警

class ContentExtractEntry(BaseModel):
    """提取任务明细结构。"""

    field_0: str | None = None  # 任务编号
    field_1: str | None = None  # 来源页面
    field_2: str | None = None  # 内容类型
    field_3: str | None = None  # 抽取算法
    field_4: str | None = None  # 关键词过滤
    field_5: str | None = None  # 摘要长度
    field_6: str | None = None  # 质量阈值
    field_7: str | None = None  # 任务批次
