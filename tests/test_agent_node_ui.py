"""MAD-934: Add Models UI wiring for node-agent pairing and listing."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_index_exposes_agent_node_pairing_form_and_list():
    html = _source("static/index.html")

    for element_id in (
        "adm-agentUrl",
        "adm-agentToken",
        "adm-agentName",
        "adm-agentWorkspaces",
        "adm-agentMsg",
        "adm-agentAddBtn",
        "adm-agentTestBtn",
        "adm-epList-agent",
    ):
        assert f'id="{element_id}"' in html


def test_admin_pairs_node_with_the_redacted_pairing_routes_and_lists_agents():
    js = _source("static/js/admin.js")

    assert "/api/model-endpoints/pair-agent" in js
    assert "fd.append('endpoint_kind', 'agent')" in js
    assert "fd.append('bridge_protocol', 'codex-bridge')" in js
    assert "initAgentNodeForm" in js
    # Agent endpoints are partitioned out of the model rows and rendered in
    # their own node group with honest status copy.
    assert "agentEndpoints" in js
    assert "_agentRowHtml" in js
    assert "pairing rejected" in js
    assert "bridge update needed" in js
    # Only the fingerprint is ever rendered; the pairing token never comes back.
    assert "api_key_fingerprint" in js
    assert "d.api_key" not in js
    assert "data.api_key" not in js
