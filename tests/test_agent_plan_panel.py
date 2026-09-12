"""Agent working-plan / todos panel guards (MAD-917).

The backend update_plan tool and its plan_update SSE event already existed; this
pins the frontend contract and the agent-facing wording that makes the tool a
general multi-step progress list rather than an approved-plan-only marker.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "static"


def test_composer_panel_markup_precedes_input_bar():
    index = (STATIC / "index.html").read_text()
    panel = index.index('id="agent-plan-panel"')
    input_bar = index.index('class="chat-input-bar"')
    assert panel < input_bar
    assert 'id="agent-plan-count"' in index
    assert 'id="agent-plan-toggle"' in index
    assert 'id="agent-plan-list"' in index


def test_plan_module_contract():
    source = (STATIC / "js" / "agentPlan.js").read_text()
    for token in ("export function parsePlan", "export function update", "export function restore", "initAgentPlan", "todos completed", "is-active", "is-done"):
        assert token in source


def test_storage_keys_registered():
    source = (STATIC / "js" / "storage.js").read_text()
    assert "AGENT_PLAN: 'odysseus-agent-plan'" in source
    assert "AGENT_PLAN_COLLAPSED: 'odysseus-agent-plan-collapsed'" in source


def test_chat_stream_uses_plan_module_not_removed_helper():
    source = (STATIC / "js" / "chat.js").read_text()
    assert "window.agentPlanModule?.update?.(_pu, streamSessionId)" in source
    assert "_setStoredPlan" not in source


def test_app_initializes_plan_module():
    source = (STATIC / "app.js").read_text()
    assert "agentPlanModule" in source
    assert "initAgentPlan()" in source


def test_agent_told_to_publish_multi_step_plans():
    schema = (REPO / "src" / "tool_schemas.py").read_text()
    assert "live todo panel above the composer" in schema
    assert "No effect if there is no active plan" not in schema
    loop = (REPO / "src" / "agent_loop.py").read_text()
    assert "live todo panel above the composer" in loop
