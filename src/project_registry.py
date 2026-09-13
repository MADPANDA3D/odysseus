"""Pandamonium agent workstation projects (MAD-902, MAD-920).

Projects are real directories on the machine Pandamonium runs on. The registry
lives in ``DATA_DIR/projects.json``; new projects are created under
``DATA_DIR/projects`` by default and stamped with the WhoAmI project-local
starter build (``src/whoami_project_template``). Imported folders are vetted
with the same rules as the chat workspace picker, so filesystem roots and
sensitive paths can never be registered as projects.

MAD-920 makes projects the organizing surface for chats: sessions carry a
``project_id`` binding, and the legacy free-form chat folder names are converted
into real projects exactly once by :func:`migrate_session_folders`.
"""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from core.atomic_io import atomic_write_json

# A project name is a single path segment: no separators, no traversal, no
# NUL, and bounded so it cannot become an unreasonable directory name.
_NAME_RE = re.compile(r"^[^/\\\x00]{1,64}$")

# System groupings are not projects and must never be converted or shown as
# one. ``Tasks`` is set by the scheduler; ``Assistant`` is the legacy
# assistant log folder.
_EXCLUDED_FOLDER_NAMES = {"tasks", "assistant"}

_MIGRATION_KEY = "session_folder_migration"

_lock = threading.Lock()
_migration_lock = threading.Lock()


def _store_path() -> Path:
    from core.constants import DATA_DIR

    return Path(DATA_DIR) / "projects.json"


def projects_root() -> Path:
    """Default parent for created projects (installation data directory)."""
    from core.constants import DATA_DIR

    return Path(DATA_DIR) / "projects"


def _read_document() -> dict:
    """Read the whole store document, preserving non-project metadata."""
    try:
        raw = json.loads(_store_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"projects": []}
    if isinstance(raw, list):
        return {"projects": raw}
    if not isinstance(raw, dict):
        return {"projects": []}
    if not isinstance(raw.get("projects"), list):
        raw["projects"] = []
    return raw


def _read_store() -> List[dict]:
    entries = _read_document().get("projects")
    if not isinstance(entries, list):
        return []
    projects: List[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        project_id = str(entry.get("id") or "").strip()
        path = str(entry.get("path") or "").strip()
        if not project_id or not path:
            continue
        projects.append({
            "id": project_id,
            "name": str(entry.get("name") or Path(path).name or "Project")[:80],
            "path": path,
            "created_at": str(entry.get("created_at") or ""),
        })
    return projects


def _write_store(projects: List[dict]) -> None:
    document = _read_document()
    document["projects"] = projects
    atomic_write_json(str(_store_path()), document, indent=2)


def _decorate(project: dict) -> dict:
    """Add live availability state without storing it."""
    resolved = os.path.realpath(os.path.expanduser(project["path"]))
    exists = os.path.isdir(resolved)
    readable = exists and os.access(resolved, os.R_OK | os.X_OK)
    reason = ""
    if not exists:
        reason = "folder no longer exists"
    elif not readable:
        reason = "folder is not readable"
    return {**project, "resolved_path": resolved, "available": readable, "reason": reason}


def list_projects() -> List[dict]:
    with _lock:
        return [_decorate(project) for project in _read_store()]


def get_project(project_id: str) -> Optional[dict]:
    """Return one decorated project by id, or None."""
    wanted = str(project_id or "").strip()
    if not wanted:
        return None
    with _lock:
        for project in _read_store():
            if project["id"] == wanted:
                return _decorate(project)
    return None


def project_id_for_name(name: str) -> Optional[str]:
    """Return the id of the first project with this display name, or None."""
    wanted = str(name or "").strip().casefold()
    if not wanted:
        return None
    with _lock:
        for project in _read_store():
            if project["name"].strip().casefold() == wanted:
                return project["id"]
    return None


def scaffold_project(resolved_path: str, name: str) -> List[str]:
    """Stamp the WhoAmI project-local starter build into a project folder.

    Creates the template's directories and writes every template file whose
    destination does not already exist — an existing project is never
    overwritten. Returns the list of relative paths written.
    """
    from src import whoami_project_template

    root = Path(resolved_path)
    written: List[str] = []
    for relative_dir in whoami_project_template.template_dirs():
        (root / relative_dir).mkdir(parents=True, exist_ok=True)
    for relative in whoami_project_template.template_files():
        target = root / relative
        if target.exists():
            continue
        content = whoami_project_template.read_template(relative, project_name=name)
        target.write_text(content, encoding="utf-8")
        written.append(relative)
    return written


def add_project(name: str = "", path: str = "") -> dict:
    """Import an existing folder or create a new scaffolded project directory.

    Raises ValueError with a user-facing message when the input is unusable.
    """
    from src.tool_execution import vet_workspace

    with _lock:
        projects = _read_store()
        created = False
        if path:
            resolved = vet_workspace(path)
            if not resolved:
                raise ValueError("That folder cannot be used as a project")
            display = (name or "").strip() or Path(resolved).name
            if any(existing["path"] == resolved for existing in projects):
                raise ValueError("That folder is already a project")
        else:
            candidate = (name or "").strip()
            if not candidate or candidate in {".", ".."} or not _NAME_RE.match(candidate):
                raise ValueError("Enter a project name without slashes")
            root = os.path.realpath(projects_root())
            resolved = os.path.realpath(os.path.join(root, candidate))
            try:
                inside_root = os.path.commonpath((root, resolved)) == root
            except ValueError:
                inside_root = False
            if not inside_root:
                raise ValueError("Project name must stay inside the projects folder")
            try:
                os.makedirs(resolved, exist_ok=True)
            except OSError as exc:
                raise ValueError(f"Could not create the project folder: {exc.strerror or exc}")
            display = candidate
            created = True
            if any(existing["path"] == resolved for existing in projects):
                raise ValueError("That project already exists")
        project = {
            "id": uuid.uuid4().hex[:8],
            "name": display[:80],
            "path": resolved,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        projects.append(project)
        _write_store(projects)
    if created:
        try:
            scaffold_project(resolved, display)
        except OSError:
            # The project is registered; a failed stamp must not lose it.
            pass
    return _decorate(project)


def remove_project(project_id: str) -> bool:
    """Forget a project and unbind its chats (never deletes the directory)."""
    with _lock:
        projects = _read_store()
        remaining = [project for project in projects if project["id"] != project_id]
        if len(remaining) == len(projects):
            return False
        _write_store(remaining)
    try:
        from core.database import Session as DbSession, SessionLocal

        db = SessionLocal()
        try:
            db.query(DbSession).filter(DbSession.project_id == project_id).update(
                {DbSession.project_id: None}, synchronize_session=False
            )
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()
    except Exception:
        pass
    return True


def migrate_session_folders() -> dict:
    """Convert legacy chat folder names into real projects, exactly once.

    Every distinct non-system ``sessions.folder`` value becomes a project
    (scaffolded when newly created) and its sessions are bound to it. Existing
    projects with a matching name are reused. Idempotent: a flag is stored only
    after a successful pass, and re-runs skip unset work safely.
    """
    with _migration_lock:
        if _read_document().get(_MIGRATION_KEY) is True:
            return {"migrated": 0, "bound": 0, "already_done": True}

        from core.database import Session as DbSession, SessionLocal

        db = SessionLocal()
        try:
            rows = db.query(DbSession.folder).filter(DbSession.folder.isnot(None)).distinct().all()
            folders = [str(row[0]).strip() for row in rows if str(row[0] or "").strip()]
        finally:
            db.close()

        migrated = 0
        bound = 0
        for folder in folders:
            if folder.casefold() in _EXCLUDED_FOLDER_NAMES:
                continue
            project_id = project_id_for_name(folder)
            if not project_id:
                try:
                    project = add_project(name=folder)
                except (ValueError, OSError):
                    continue
                project_id = project["id"]
                migrated += 1
            db = SessionLocal()
            try:
                bound += (
                    db.query(DbSession)
                    .filter(
                        DbSession.folder == folder,
                        (DbSession.project_id.is_(None)) | (DbSession.project_id == ""),
                    )
                    .update({DbSession.project_id: project_id}, synchronize_session=False)
                )
                db.commit()
            except Exception:
                db.rollback()
            finally:
                db.close()

        document = _read_document()
        document[_MIGRATION_KEY] = True
        atomic_write_json(str(_store_path()), document, indent=2)
        return {"migrated": migrated, "bound": bound, "already_done": False}
