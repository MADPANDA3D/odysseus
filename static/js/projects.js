// static/js/projects.js
//
// Pandamonium agent workstation projects (MAD-902, MAD-920). Each project is a
// real directory on the machine Pandamonium runs on. Creating a project makes
// the directory and stamps the WhoAmI project-local build into it; importing
// registers an existing folder. The project list is server-backed
// (DATA_DIR/projects.json), so it survives reloads and is shared across
// browsers for the installation.
//
// This module owns project data and actions. The Chats sidebar (sessions.js)
// renders the project folders and their bound chats.

import uiModule from './ui.js';
import { pickFolder } from './workspace.js';

const API_BASE = window.location.origin;
let _projects = [];
let _loadPromise = null;
let _menu = null;

function _projectPath(project) {
  return project.resolved_path || project.path || '';
}

export function getProjects() {
  return _projects;
}

export function getProjectById(projectId) {
  const wanted = projectId ? String(projectId) : '';
  if (!wanted) return null;
  return _projects.find(project => String(project.id) === wanted) || null;
}

function _notifyChanged() {
  try {
    window.dispatchEvent(new CustomEvent('odysseus:projects-changed'));
  } catch (_) {}
}

export async function refreshProjects() {
  try {
    const response = await fetch(`${API_BASE}/api/projects`, { credentials: 'same-origin' });
    if (!response.ok) throw new Error(`projects_${response.status}`);
    const data = await response.json();
    _projects = Array.isArray(data.projects) ? data.projects : [];
  } catch (_) {
    _projects = [];
  }
  _notifyChanged();
  return _projects;
}

/** Load projects once (idempotent) — used before the first sidebar render. */
export function ensureProjectsLoaded() {
  if (!_loadPromise) _loadPromise = refreshProjects();
  return _loadPromise;
}

async function _addProject(payload) {
  const response = await fetch(`${API_BASE}/api/projects`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify(payload),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || 'Could not add the project');
  _projects = Array.isArray(data.projects) ? data.projects : _projects;
  _notifyChanged();
  return data.project || null;
}

export async function createProjectInteractive() {
  let name = '';
  try {
    name = await uiModule.styledPrompt('Create a new project folder. Pandamonium stamps the WhoAmI project build into it.', {
      title: 'New project',
      placeholder: 'Project name',
      confirmText: 'Create',
    });
  } catch (_) { name = ''; }
  name = (name || '').trim();
  if (!name) return null;
  try {
    const project = await _addProject({ name });
    if (project) uiModule.showToast(`Project created: ${project.name}`);
    return project;
  } catch (error) {
    uiModule.showError(error.message || 'Could not create the project');
    return null;
  }
}

export async function importProjectInteractive() {
  const path = await pickFolder();
  if (!path) return null;
  try {
    const project = await _addProject({ path });
    if (project) uiModule.showToast(`Project added: ${project.name}`);
    return project;
  } catch (error) {
    uiModule.showError(error.message || 'Could not add the project');
    return null;
  }
}

export async function removeProject(projectId) {
  const response = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(projectId)}`, {
    method: 'DELETE',
    credentials: 'same-origin',
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || 'Could not remove the project');
  _projects = Array.isArray(data.projects) ? data.projects : _projects;
  _notifyChanged();
}

function _closeAddMenu() {
  if (!_menu) return;
  _menu.classList.remove('show');
  const button = document.getElementById('projects-add-btn');
  if (button) button.setAttribute('aria-expanded', 'false');
}

function _ensureAddMenu() {
  if (_menu) return _menu;
  _menu = document.createElement('div');
  _menu.id = 'projects-add-menu';
  _menu.className = 'dropdown';
  _menu.setAttribute('role', 'menu');
  _menu.innerHTML = `
    <div class="dropdown-item" id="project-create-option" role="menuitem" tabindex="0">
      <h4>New project</h4>
      <p>Create a working folder with the WhoAmI build</p>
    </div>
    <div class="dropdown-item" id="project-import-option" role="menuitem" tabindex="0">
      <h4>Add existing folder</h4>
      <p>Use a folder that already exists</p>
    </div>`;
  _menu.querySelector('#project-create-option').addEventListener('click', () => {
    _closeAddMenu();
    createProjectInteractive();
  });
  _menu.querySelector('#project-import-option').addEventListener('click', () => {
    _closeAddMenu();
    importProjectInteractive();
  });
  document.addEventListener('click', event => {
    if (_menu.classList.contains('show') && !_menu.contains(event.target)
      && !(event.target.closest && event.target.closest('#projects-add-btn'))) {
      _closeAddMenu();
    }
  });
  document.body.appendChild(_menu);
  return _menu;
}

/** Open the New/Import menu anchored to the Projects + button. */
export function openAddMenu(anchor) {
  const menu = _ensureAddMenu();
  const button = anchor || document.getElementById('projects-add-btn');
  if (!button) return;
  if (menu.classList.contains('show') && button.getAttribute('aria-expanded') === 'true') {
    _closeAddMenu();
    return;
  }
  menu.classList.add('show');
  menu.style.position = 'fixed';
  menu.style.right = 'auto';
  menu.style.visibility = 'hidden';
  const rect = button.getBoundingClientRect();
  const menuWidth = menu.offsetWidth || 240;
  const menuHeight = menu.offsetHeight || 120;
  let left = rect.right - menuWidth;
  if (left < 8) left = 8;
  if (left + menuWidth > window.innerWidth - 8) left = Math.max(8, window.innerWidth - menuWidth - 8);
  let top = rect.bottom + 4;
  if (top + menuHeight > window.innerHeight - 8) top = Math.max(8, rect.top - menuHeight - 4);
  menu.style.left = left + 'px';
  menu.style.top = top + 'px';
  menu.style.visibility = '';
  button.setAttribute('aria-expanded', 'true');
}

export function initProjects() {
  ensureProjectsLoaded();
}

export default {
  initProjects,
  refreshProjects,
  ensureProjectsLoaded,
  getProjects,
  getProjectById,
  createProjectInteractive,
  importProjectInteractive,
  removeProject,
  openAddMenu,
};
