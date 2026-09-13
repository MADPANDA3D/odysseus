"""WhoAmI project-local starter template (MAD-920).

These files are the ``project-local-ai-growth`` layer of the shipped
MADPANDA3D Codex WhoAmI starter bundle (v1.0.0). Pandamonium stamps them into
a new project directory so every project starts with the correct WhoAmI build:
AGENTS contract, status, handover, memory, bugs, decisions, system map,
backlog, import record, tickets, evidence, and artifacts.

The literal ``AI-GROWTH-WORKSPACE`` placeholder is replaced with the project
name at stamp time. Existing files are never overwritten by the scaffolder.
"""
from __future__ import annotations

from importlib import resources
from typing import List

TEMPLATE_VERSION = "1.0.0"
WORKSPACE_TOKEN = "AI-GROWTH-WORKSPACE"


def template_files() -> List[str]:
    """Return every template file as a POSIX-style relative path."""
    found: List[str] = []

    def _walk(node, prefix: str = "") -> None:
        for child in node.iterdir():
            name = f"{prefix}{child.name}"
            if child.is_dir():
                _walk(child, f"{name}/")
            elif child.is_file() and not name.startswith("_") and name != "__init__.py":
                found.append(name)

    _walk(resources.files(__package__))
    return sorted(found)


def template_dirs() -> List[str]:
    """Return every directory needed by the template, parents first."""
    dirs = set()
    for relative in template_files():
        parent = relative.rsplit("/", 1)[0] if "/" in relative else ""
        while parent:
            dirs.add(parent)
            parent = parent.rsplit("/", 1)[0] if "/" in parent else ""
    return sorted(dirs)


def read_template(relative_path: str, project_name: str = "") -> str:
    """Read one template file and substitute the workspace token."""
    root = resources.files(__package__)
    node = root
    for part in relative_path.split("/"):
        node = node.joinpath(part)
    text = node.read_text(encoding="utf-8")
    if project_name:
        text = text.replace(WORKSPACE_TOKEN, project_name)
    return text


__all__ = [
    "TEMPLATE_VERSION",
    "WORKSPACE_TOKEN",
    "read_template",
    "template_dirs",
    "template_files",
]
