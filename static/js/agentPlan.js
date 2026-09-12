// static/js/agentPlan.js
//
// Agent working plan / todos (MAD-917). The agent's `update_plan` tool streams
// `plan_update` events carrying a GitHub-style markdown checklist; this module
// renders the latest plan for the active session in a collapsible panel docked
// directly above the composer. This is the AGENT's progress list while it works
// through a task — not the user-facing Tasks feature.

import Storage, { KEYS } from './storage.js';

const byId = id => document.getElementById(id);
const _STEP_RE = /^(\s*)[-*]\s+\[([ xX])\]\s+(.*\S)\s*$/;
const _PENDING_SESSION = '__pending__';

function _sessionId() {
  return window.sessionModule?.getCurrentSessionId?.() || _PENDING_SESSION;
}

/** Parse a markdown checklist into ordered steps with done + nesting depth. */
export function parsePlan(markdown) {
  const items = [];
  for (const line of String(markdown || '').split(/\r?\n/)) {
    const match = _STEP_RE.exec(line);
    if (!match) continue;
    const indent = match[1].replace(/\t/g, '  ').length;
    items.push({
      text: match[3].trim(),
      done: match[2].toLowerCase() === 'x',
      depth: Math.min(3, Math.floor(indent / 2)),
    });
  }
  return items;
}

export function getStoredPlan(sessionId = _sessionId()) {
  const record = Storage.getJSON(KEYS.AGENT_PLAN, null);
  return record && record.sessionId === sessionId && typeof record.text === 'string'
    ? record.text
    : '';
}

function _storePlan(sessionId, text) {
  try {
    Storage.setJSON(KEYS.AGENT_PLAN, { sessionId, text });
  } catch (_) { /* quota / private mode — the live panel still renders */ }
}

function _isCollapsed() {
  try { return localStorage.getItem(KEYS.AGENT_PLAN_COLLAPSED) === '1'; } catch (_) { return false; }
}

function _applyCollapsed(collapsed) {
  const panel = byId('agent-plan-panel');
  if (panel) panel.classList.toggle('collapsed', collapsed);
  const toggle = byId('agent-plan-toggle');
  if (toggle) toggle.setAttribute('aria-expanded', String(!collapsed));
  try { localStorage.setItem(KEYS.AGENT_PLAN_COLLAPSED, collapsed ? '1' : '0'); } catch (_) {}
}

function _renderItems(items) {
  const doneCount = items.filter(item => item.done).length;
  const count = byId('agent-plan-count');
  if (count) count.textContent = `${doneCount} of ${items.length} todos completed`;
  const list = byId('agent-plan-list');
  if (!list) return;
  const firstPending = items.findIndex(item => !item.done);
  list.replaceChildren(...items.map((item, index) => {
    const row = document.createElement('li');
    row.className = 'agent-plan-item';
    if (item.done) row.classList.add('is-done');
    const active = !item.done && index === firstPending;
    if (active) row.classList.add('is-active');
    row.style.setProperty('--plan-depth', String(item.depth));
    const check = document.createElement('span');
    check.className = 'agent-plan-check';
    check.setAttribute('aria-hidden', 'true');
    check.textContent = item.done ? '✓' : active ? '▸' : '○';
    const text = document.createElement('span');
    text.className = 'agent-plan-text';
    text.textContent = item.text;
    row.append(check, text);
    return row;
  }));
}

/** Render a plan markdown string (empty string hides the panel). */
export function renderPlan(markdown) {
  const panel = byId('agent-plan-panel');
  if (!panel) return;
  const items = parsePlan(markdown);
  if (!items.length) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  _renderItems(items);
}

/** Store and (when it belongs to the active session) render a live plan update. */
export function update(planText, sessionId = _sessionId()) {
  const text = String(planText || '').trim();
  if (!text) return;
  _storePlan(sessionId, text);
  if (sessionId === _sessionId()) renderPlan(text);
}

/** Show the stored plan for a session (or hide the panel when there is none). */
export function restore(sessionId = _sessionId()) {
  renderPlan(getStoredPlan(sessionId));
}

export function initAgentPlan() {
  if (!byId('agent-plan-panel')) return;
  const toggle = byId('agent-plan-toggle');
  if (toggle) {
    toggle.addEventListener('click', () => _applyCollapsed(!_isCollapsed()));
  }
  _applyCollapsed(_isCollapsed());
  window.addEventListener('odysseus:session-rendered', () => restore());
  restore();
}

export default { initAgentPlan, update, restore, renderPlan, parsePlan, getStoredPlan };
