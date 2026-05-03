"""LangGraph 工作流组装 — 将节点串联为带条件循环的图。"""

from __future__ import annotations

import logging

from langgraph.graph import END, StateGraph

from workflows.nodes import (
    analyze_node,
    collect_node,
    organize_node,
    review_node,
    save_node,
)
from workflows.state import KBState

logger = logging.getLogger(__name__)


def _review_router(state: KBState) -> str:
    """review 之后的条件路由：通过则保存，否则回到整理节点修正。"""
    if state.get("review_passed"):
        return "save"
    return "organize"


def build_graph() -> any:
    """构建并编译 LangGraph 工作流，返回可执行的 app。"""
    graph = StateGraph(KBState)

    graph.add_node("collect", collect_node)
    graph.add_node("analyze", analyze_node)
    graph.add_node("organize", organize_node)
    graph.add_node("review", review_node)
    graph.add_node("save", save_node)

    graph.set_entry_point("collect")
    graph.add_edge("collect", "analyze")
    graph.add_edge("analyze", "organize")
    graph.add_edge("organize", "review")
    graph.add_conditional_edges("review", _review_router, {
        "save": "save",
        "organize": "organize",
    })
    graph.add_edge("save", END)

    return graph.compile()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    app = build_graph()
    initial_state: KBState = {
        "sources": [],
        "analyses": [],
        "articles": [],
        "review_feedback": "",
        "review_passed": False,
        "iteration": 0,
        "cost_tracker": {},
    }

    final_state = initial_state
    for event in app.stream(initial_state):
        for node_name, output in event.items():
            final_state = {**final_state, **output}
            if node_name == "collect":
                logger.info("✓ collect: %d 条原始数据", len(output.get("sources", [])))
            elif node_name == "analyze":
                logger.info("✓ analyze: %d 条分析结果", len(output.get("analyses", [])))
            elif node_name == "organize":
                logger.info("✓ organize: %d 条知识条目", len(output.get("articles", [])))
            elif node_name == "review":
                logger.info(
                    "✓ review: passed=%s, iteration=%d",
                    output.get("review_passed"), output.get("iteration"),
                )
            elif node_name == "save":
                logger.info("✓ save: %d 条已保存", len(output.get("articles", [])))

    cost = final_state.get("cost_tracker", {})
    logger.info(
        "工作流执行完毕 — prompt_tokens=%d, completion_tokens=%d, 总费用=¥%.6f",
        cost.get("prompt_tokens", 0),
        cost.get("completion_tokens", 0),
        cost.get("total_cost", 0),
    )
