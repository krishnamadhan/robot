"""Shared test config — isolate tests from on-disk robot state."""
import pytest


@pytest.fixture(autouse=True)
def _no_token_budget_persistence(monkeypatch):
    """TokenBudget persists to the real episodic.db; tests must stay in-RAM."""
    from cognition.llm import TokenBudget
    monkeypatch.setattr(TokenBudget, "_db", lambda self: None)
