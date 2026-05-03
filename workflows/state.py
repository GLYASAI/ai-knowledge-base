"""LangGraph 工作流共享状态定义"""

from typing import TypedDict


class KBState(TypedDict):
    """知识库工作流的共享状态

    遵循"报告式通信"原则：每个字段存放结构化摘要，
    而非原始 HTML/API 响应等大体积数据。
    """

    # 采集到的原始数据摘要列表，每项包含 source_url / title / raw_text 等关键字段
    sources: list[dict]

    # LLM 分析后的结构化结果，每项包含 summary / tech_highlights / relevance_score
    analyses: list[dict]

    # 格式化、去重后的知识条目，符合 knowledge/articles/ 的 JSON schema
    articles: list[dict]

    # 审核反馈意见，Supervisor 节点给出的修改建议（空字符串表示无反馈）
    review_feedback: str

    # 审核是否通过，True 表示可以进入发布流程
    review_passed: bool

    # 当前审核循环次数，从 0 开始，最多 3 次（超过则强制通过）
    iteration: int

    # Token 用量追踪，包含 prompt_tokens / completion_tokens / total_cost_yuan
    cost_tracker: dict
