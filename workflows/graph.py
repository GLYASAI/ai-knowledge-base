"""
LangGraph 工作流组装 — 将节点串联为带条件循环的图。
工作流程结构：
plan → sources → analyses → review ─[pass]→ organize → END
                                ↓
                            revise → review（循环）
                                ↓[>max]
                            human_flag → END
"""

from __future__ import annotations

import logging

from langgraph.graph import END, StateGraph

from workflows.analyzer import analyze_node
from workflows.collector import collect_node
from workflows.human_flag import human_flag_node
from workflows.organizer import organize_node, save_node
from workflows.planner import planner_node
from workflows.reviewer import review_node
from workflows.reviser import revise_node
from workflows.state import KBState

logger = logging.getLogger(__name__)


def _review_router(state: KBState) -> str:
    """review 之后的 3 路条件路由。"""
    plan = state.get("plan", {}) or {}
    max_iter = int(plan.get("max_iterations", 3))

    if state.get("review_passed"):
        return "organize"
    if state.get("iteration", 0) >= max_iter:
        return "human_flag"
    return "revise"


def build_graph() -> any:
    """构建并编译 LangGraph 工作流，返回可执行的 app。"""
    graph = StateGraph(KBState)

    graph.add_node("plan", planner_node)
    graph.add_node("collect", collect_node)
    graph.add_node("analyze", analyze_node)
    graph.add_node("organize", organize_node)
    graph.add_node("review", review_node)
    graph.add_node("revise", revise_node)
    graph.add_node("human_flag", human_flag_node)
    graph.add_node("save", save_node)

    graph.set_entry_point("plan")
    graph.add_edge("plan", "collect")
    graph.add_edge("collect", "analyze")
    graph.add_edge("analyze", "review")
    graph.add_conditional_edges("review", _review_router, {
        "organize": "organize",
        "revise": "revise",
        "human_flag": "human_flag",
    })
    graph.add_edge("revise", "review")
    graph.add_edge("organize", "save")
    graph.add_edge("human_flag", END)
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
            elif node_name == "review":
                logger.info(
                    "✓ review: passed=%s, iteration=%d",
                    output.get("review_passed"), output.get("iteration"),
                )
            elif node_name == "revise":
                logger.info("✓ revise: %d 条已修正", len(output.get("analyses", [])))
            elif node_name == "organize":
                logger.info("✓ organize: %d 条知识条目", len(output.get("articles", [])))
            elif node_name == "human_flag":
                logger.warning("✓ human_flag: 需人工审核")
            elif node_name == "save":
                logger.info("✓ save: %d 条已保存", len(output.get("articles", [])))

    cost = final_state.get("cost_tracker", {})
    logger.info(
        "工作流执行完毕 — prompt=%.1fk tokens, completion=%.1fk tokens, 总费用=¥%.4f",
        cost.get("prompt_tokens", 0) / 1000,
        cost.get("completion_tokens", 0) / 1000,
        cost.get("total_cost", 0),
    )
