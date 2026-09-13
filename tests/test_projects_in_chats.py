"""MAD-920: chat projects are real, bound to sessions, and organized in Chats.

Registry/scaffold/migration tests plus route-level binding checks. The temp DB
pattern follows tests/test_session_list_owner_scope.py so the in-memory default
test database is never mutated across tests.
"""
import sys
import tempfile
import types
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.database import Session as DbSession
from src import project_registry

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    import core.constants as constants
    monkeypatch.setattr(constants, "DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    return tmp_path


@pytest.fixture(autouse=True)
def _isolate_session_router():
    """setup_session_routes appends to a module-level router; undo it per test.

    Without this, the routes registered here stay on the shared router and
    later test modules that pick the first matching route call our closures
    with stale monkeypatches.
    """
    from routes import session_routes as sr

    before = list(sr.router.routes)
    yield
    sr.router.routes[:] = before


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    db_file = tmp_path / "projects-in-chats.db"
    engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    cdb.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(cdb, "SessionLocal", factory)
    return factory


def _add_session(factory, **fields):
    sid = str(uuid.uuid4())
    db = factory()
    try:
        db.add(DbSession(id=sid, name="chat", endpoint_url="http://localhost",
                         model="test-model", **fields))
        db.commit()
    finally:
        db.close()
    return sid


# ── Scaffold ─────────────────────────────────────────────────────────────

def test_created_project_scaffolds_whoami_build(data_dir):
    project = project_registry.add_project(name="Scaffolded")
    root = Path(project["path"])

    for relative in (
        "AGENTS.md", "STATUS.md", "SYSTEM-MAP.md", "DECISIONS.md",
        "BACKLOG.md", "HANDOVER.md", "MEMORY.md", "BUGS.md", "IMPORT.md",
        "tickets/open/README.txt", "tickets/in-progress/README.txt",
        "tickets/completed/README.txt", "tickets/failed/README.txt",
        "tickets/blocked/README.txt", "tickets/skipped/README.txt",
        "evidence/README.txt", "artifacts/README.txt",
    ):
        assert (root / relative).is_file(), relative

    agents = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert "Scaffolded" in agents
    assert "AI-GROWTH-WORKSPACE" not in agents


def test_scaffold_never_overwrites_existing_files(data_dir):
    project = project_registry.add_project(name="Protected")
    root = Path(project["path"])
    agents = root / "AGENTS.md"
    agents.write_text("owner content", encoding="utf-8")

    written = project_registry.scaffold_project(project["path"], "Protected")

    assert "AGENTS.md" not in written
    assert agents.read_text(encoding="utf-8") == "owner content"


def test_imported_folder_is_not_scaffolded(data_dir):
    existing = data_dir / "existing-folder"
    existing.mkdir()

    project = project_registry.add_project(path=str(existing))

    assert not (existing / "AGENTS.md").exists()
    assert project["path"] == str(existing.resolve())


# ── Migration ────────────────────────────────────────────────────────────

def test_migration_converts_legacy_folders_and_skips_system_groupings(data_dir, temp_db):
    session_ids = {
        "Alpha Chat": _add_session(temp_db, folder="Alpha Chat"),
        "Beta Ops": _add_session(temp_db, folder="Beta Ops"),
        "Tasks": _add_session(temp_db, folder="Tasks"),
        "Assistant": _add_session(temp_db, folder="Assistant"),
        "No Folder": _add_session(temp_db),
    }

    summary = project_registry.migrate_session_folders()

    assert summary["already_done"] is False
    assert summary["migrated"] == 2
    assert summary["bound"] == 2
    names = {project["name"] for project in project_registry.list_projects()}
    assert names == {"Alpha Chat", "Beta Ops"}

    db = temp_db()
    try:
        rows = {row.id: row.project_id for row in db.query(DbSession).all()}
    finally:
        db.close()
    assert rows[session_ids["Alpha Chat"]]
    assert rows[session_ids["Beta Ops"]]
    assert rows[session_ids["Tasks"]] is None
    assert rows[session_ids["Assistant"]] is None
    assert rows[session_ids["No Folder"]] is None

    alpha = next(p for p in project_registry.list_projects() if p["name"] == "Alpha Chat")
    assert (Path(alpha["path"]) / "AGENTS.md").is_file()

    # Idempotent: a second pass makes no new projects and binds nothing.
    again = project_registry.migrate_session_folders()
    assert again["already_done"] is True
    assert set(p["name"] for p in project_registry.list_projects()) == names


def test_remove_project_unbinds_chats_without_deleting_them(data_dir, temp_db):
    project = project_registry.add_project(name="Unbind")
    sid = _add_session(temp_db, project_id=project["id"])

    assert project_registry.remove_project(project["id"]) is True
    assert Path(project["path"]).is_dir()

    db = temp_db()
    try:
        row = db.query(DbSession).filter(DbSession.id == sid).first()
    finally:
        db.close()
    assert row is not None
    assert row.project_id is None


# ── Route-level binding ──────────────────────────────────────────────────

def test_validate_project_id_normalizes_and_rejects_unknown(data_dir):
    from routes import session_routes as sr

    project = project_registry.add_project(name="Valid")

    assert sr._validate_project_id(project["id"]) == project["id"]
    assert sr._validate_project_id("  ") == ""
    with pytest.raises(HTTPException) as exc:
        sr._validate_project_id("does-not-exist")
    assert exc.value.status_code == 400


def test_patch_session_binds_and_clears_project(data_dir, temp_db, monkeypatch):
    from routes import session_routes as sr

    monkeypatch.setattr(sr, "SessionLocal", temp_db)
    monkeypatch.setattr(sr, "_verify_session_owner", lambda request, sid, session_manager=None: None)

    project = project_registry.add_project(name="Patched")
    sid = _add_session(temp_db)

    class _Session:
        agent_target = "jarvis"

    sm = types.SimpleNamespace(get_session=lambda session_id: _Session())
    router = sr.setup_session_routes(sm, {})
    endpoint = next(
        r.endpoint for r in router.routes
        if getattr(r, "path", "") == "/api/session/{sid}"
        and "PATCH" in getattr(r, "methods", set())
    )

    result = endpoint(
        request=types.SimpleNamespace(), sid=sid,
        name=None, folder=None, model=None, endpoint_url=None,
        endpoint_id=None, agent_target=None, project_id=project["id"],
    )
    assert result["project_id"] == project["id"]

    db = temp_db()
    try:
        assert db.query(DbSession).filter(DbSession.id == sid).first().project_id == project["id"]
    finally:
        db.close()

    cleared = endpoint(
        request=types.SimpleNamespace(), sid=sid,
        name=None, folder=None, model=None, endpoint_url=None,
        endpoint_id=None, agent_target=None, project_id="",
    )
    assert cleared["project_id"] is None

    db = temp_db()
    try:
        assert db.query(DbSession).filter(DbSession.id == sid).first().project_id is None
    finally:
        db.close()


def test_list_sessions_returns_project_binding(data_dir, temp_db, monkeypatch):
    from routes import session_routes as sr
    from unittest.mock import MagicMock

    monkeypatch.setattr(sr, "SessionLocal", temp_db)
    monkeypatch.setattr(sr, "effective_user", lambda request: "alice")

    project = project_registry.add_project(name="Listed")
    sid = _add_session(temp_db, owner="alice", project_id=project["id"])

    session = MagicMock(id=sid, name="chat", model="test-model",
                        endpoint_url="http://localhost", rag=False, archived=False)
    sm = MagicMock()
    sm.get_sessions_for_user.return_value = {sid: session}
    sr.setup_session_routes(sm, {})
    # The module-level router accumulates routes across setup calls in the test
    # process, so take the endpoint registered by this test (the newest).
    endpoint = next(
        r.endpoint for r in reversed(sr.router.routes)
        if getattr(r, "path", "") == "/api/sessions"
        and "GET" in getattr(r, "methods", set())
    )

    result = endpoint(request=MagicMock())
    row = next(item for item in result if item["id"] == sid)
    assert row["project_id"] == project["id"]


# ── Frontend wiring guards ───────────────────────────────────────────────

def test_standalone_projects_section_is_removed():
    html = (ROOT / "static/index.html").read_text(encoding="utf-8")
    assert 'id="projects-section"' not in html
    assert 'id="projects-list"' not in html
    assert 'id="projects-add-menu"' not in html


def test_chats_render_real_projects_and_bind_sessions():
    sessions = (ROOT / "static/js/sessions.js").read_text(encoding="utf-8")
    assert "_createProjectsNavLabel" in sessions
    assert "projectsModule.getProjects()" in sessions
    assert "buildProjectSubmenu" in sessions
    assert "buildFolderSubmenu" not in sessions
    assert "fd.append('project_id', _pendingProjectId)" in sessions
    assert "dataset.projectId" in sessions


def test_session_move_handles_are_hidden_at_rest():
    css = (ROOT / "static/style.css").read_text(encoding="utf-8")
    marker = "#session-list .item-drag-handle, #session-list .folder-drag-handle { display: inline-flex"
    assert marker in css
    rule = css[css.index(marker):]
    rule = rule[: rule.index("}")]
    assert "opacity: 0;" in rule
    assert "pointer-events: none;" in rule
    assert "#session-list .list-item:hover .item-drag-handle" in css
    assert "body.rearrange-mode #session-list .folder-drag-handle" in css
