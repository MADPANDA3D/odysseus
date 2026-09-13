// MAD-934: pair a node agent from Add Models and list it honestly.
import { expect, test } from '@playwright/test';

const CONNECTED_AGENT = {
  id: 'ag1',
  name: 'Studio Node',
  base_url: 'http://192.168.1.20:8040',
  has_key: true,
  api_key_fingerprint: 'abc12345',
  is_enabled: true,
  models: [],
  online: true,
  status: 'connected',
  protocol: 'codex-bridge',
  workspaces: ['home-lab'],
  endpoint_kind: 'agent',
  model_type: 'agent',
  category: 'agent',
};

function stubApi(page, { pairResult, registerError, onRegister }) {
  return page.route('**/api/**', async route => {
    const req = route.request();
    const url = new URL(req.url());
    if (url.pathname === '/api/auth/status') {
      return route.fulfill({ json: { username: 'tester', is_admin: true, privileges: {} } });
    }
    if (url.pathname === '/api/auth/settings') {
      return route.fulfill({ json: { agent_id: 'assistant', agent_constitution: 'Stay accurate.' } });
    }
    if (url.pathname === '/api/model-endpoints/pair-agent' && req.method() === 'POST') {
      return route.fulfill({ json: pairResult });
    }
    if (url.pathname === '/api/model-endpoints' && req.method() === 'POST') {
      if (onRegister) onRegister(req.postData() || '');
      if (registerError) {
        return route.fulfill({ status: 400, json: { detail: registerError } });
      }
      return route.fulfill({ json: CONNECTED_AGENT });
    }
    if (url.pathname === '/api/model-endpoints') {
      return route.fulfill({ json: [CONNECTED_AGENT] });
    }
    if (
      ['/api/sessions', '/api/models', '/api/plugins', '/api/tools', '/api/selector-catalog'].includes(
        url.pathname
      )
    ) {
      return route.fulfill({ json: [] });
    }
    return route.fulfill({ json: {} });
  });
}

test('pair a node agent from Add Models and show it in the Agents group', async ({ page }) => {
  let registeredBody = '';
  await stubApi(page, {
    pairResult: {
      ok: true,
      state: 'connected',
      protocol: 'codex-bridge',
      protocol_version: 'pandamonium.codex-bridge.v2',
      node_label: 'Studio Bridge',
      message: 'Connected. The node bridge accepted the pairing code.',
    },
    onRegister: body => { registeredBody = body; },
  });

  await page.goto('/static/index.html');
  await page.evaluate(async () => (await import('/static/js/settings.js')).open());
  await page.locator('#settings-modal [data-settings-tab="services"]').click();

  await page.locator('#adm-agentUrl').fill('http://192.168.1.20:8040');
  await page.locator('#adm-agentToken').fill('one-time-code');
  await page.locator('#adm-agentWorkspaces').fill('home-lab');

  await page.locator('#adm-agentTestBtn').click();
  await expect(page.locator('#adm-agentMsg')).toContainText('accepted the pairing code');

  await page.locator('#adm-agentAddBtn').click();
  await expect(page.locator('#adm-agentMsg')).toContainText('model picker');

  expect(registeredBody).toContain('agent');
  expect(registeredBody).toContain('codex-bridge');
  expect(registeredBody).toContain('home-lab');

  await page.locator('#settings-modal [data-settings-tab="added-models"]').click();
  const agentSection = page.locator('.adm-ep-section', { has: page.locator('#adm-epList-agent') });
  await expect(agentSection).toContainText('Agents');
  await expect(page.locator('#adm-epList-agent')).toContainText('Studio Node');
  await expect(page.locator('#adm-epList-agent')).toContainText('connected');
  await expect(page.locator('#adm-epList-agent')).toContainText('workspaces: home-lab');
  await expect(page.locator('#adm-epList-agent')).not.toContainText('one-time-code');
});

test('pairing failure fails closed with human copy and registers nothing', async ({ page }) => {
  let registerCalls = 0;
  await stubApi(page, {
    pairResult: {
      ok: false,
      state: 'auth_required',
      reason: 'authentication_failed',
      message: 'The node rejected the pairing code. Generate a new one-time code on the node and pair again.',
    },
    registerError: 'The node rejected the pairing code. Generate a new one-time code on the node and pair again.',
    onRegister: () => { registerCalls += 1; },
  });

  await page.goto('/static/index.html');
  await page.evaluate(async () => (await import('/static/js/settings.js')).open());
  await page.locator('#settings-modal [data-settings-tab="services"]').click();

  await page.locator('#adm-agentUrl').fill('http://192.168.1.20:8040');
  await page.locator('#adm-agentToken').fill('stale-code');
  await page.locator('#adm-agentAddBtn').click();

  await expect(page.locator('#adm-agentMsg')).toContainText('rejected the pairing code');
  expect(registerCalls).toBe(1);
});
