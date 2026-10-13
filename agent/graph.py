"""Compatibility imports for the research graph.

New code should import from agent.langgraph.lg_builder or lg_nodes.
"""

from .langgraph.lg_builder import ResearchGraph, create_graph
from .langgraph.lg_nodes import (
    _default_research,
    _default_validate,
    _default_write,
    _deterministic_critic,
    _llm_critic,
    _normalize_critic_result,
    _parse_plan,
    _state_value,
)

__all__ = ["ResearchGraph", "create_graph"]
