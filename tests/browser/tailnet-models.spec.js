// MAD-933: tailnet model discovery and registration in Settings → Add Models.
import { expect, test } from '@playwright/test';

const PEER_A = 'a'.repeat(32);
const PEER_B = 'b'.repeat(32);

test('scan tailnet, probe selected peers, and add a discovered model', async ({ page }) => {
  const addedPosts = [];
  let probedPeerIds = [];

  await page.route('**/api/**', async route => {
    const req = route.request();
    const url = new URL(req.url());
    if (url.pathname === '/api/auth/status') {
      return route.fulfill({ json: { username: 'tester', is_admin: true, privileges: {} } });
    }
    if (url.pathname === '/api/auth/settings') {
      return route.fulfill({ json: { agent_id: 'assistant', agent_constitution: 'Stay accurate.' } });
    }
    if (url.pathname === '/api/discover' && url.searchParams.get('mode') === 'tailnet_peers') {
      return route.fulfill({
        json: {
          mode: 'tailnet_peers',
          requires_selection: true,
          peers: [
            { id: PEER_A, os: 'linux', status: 'online' },
            { id: PEER_B, os: 'darwin', status: 'online' },
          ],
        },
      });
    }
    if (url.pathname === '/api/discover' && url.searchParams.get('mode') === 'tailnet_probe') {
      probedPeerIds = url.searchParams.getAll('peer_id');
      return route.fulfill({
        json: {
          mode: 'tailnet_probe',
          selected_count: probedPeerIds.length,
          candidates: [
            {
              peer_id: PEER_A,
              provider: 'openai-compatible',
              port: 8000,
              models: ['qwen3-32b', 'llama-3.3-70b'],
              capabilities: ['model-list'],
            },
          ],
        },
      });
    }
    if (url.pathname === '/api/model-endpoints' && req.method() === 'POST') {
      addedPosts.push(req.postData() || '');
      return route.fulfill({
        json: {
          id: 'tn-1',
          name: 'OpenAI-compatible (peer aaaaaaaa)',
          base_url: 'http://100.64.1.7:8000/v1',
          models: ['qwen3-32b', 'llama-3.3-70b'],
          endpoint_kind: 'tailnet',
          category: 'tailnet',
          online: true,
          is_enabled: true,
          status: 'online',
          model_type: 'llm',
        },
      });
    }
    if (
      ['/api/sessions', '/api/model-endpoints', '/api/models', '/api/plugins', '/api/tools'].includes(
        url.pathname
      )
    ) {
      return route.fulfill({ json: [] });
    }
    return route.fulfill({ json: {} });
  });

  await page.goto('/static/index.html');
  await page.evaluate(async () => (await import('/static/js/settings.js')).open());
  await expect(page.locator('#settings-modal')).toBeVisible();
  await page.locator('#settings-modal [data-settings-tab="services"]').click();

  await page.locator('#adm-epTailnetScanBtn').click();
  await expect(page.locator('#adm-epTailnetMsg')).toContainText('Found 2 online peers');
  await expect(page.locator('#adm-epTailnetResults')).toContainText('linux');

  await page.locator(`#adm-epTailnetResults input[data-tn-peer-id="${PEER_A}"]`).check();
  await page.locator(`#adm-epTailnetResults input[data-tn-peer-id="${PEER_B}"]`).check();
  await page.getByRole('button', { name: 'Probe 2 selected' }).click();

  await expect(page.locator('#adm-epTailnetResults')).toContainText('qwen3-32b');
  expect([...probedPeerIds].sort()).toEqual([PEER_A, PEER_B].sort());

  await page.locator('#adm-epTailnetResults .admin-ep-item button', { hasText: 'Add' }).first().click();
  await expect(page.locator('#adm-epTailnetMsg')).toContainText('added with 2 models');

  const body = addedPosts[addedPosts.length - 1] || '';
  expect(body).toContain(PEER_A);
  expect(body).toContain('8000');
  expect(body).toContain('tailnet');
});

test('manual tailnet URL registration uses the tailnet endpoint kind', async ({ page }) => {
  const addedPosts = [];
  await page.route('**/api/**', async route => {
    const req = route.request();
    const url = new URL(req.url());
    if (url.pathname === '/api/auth/status') {
      return route.fulfill({ json: { username: 'tester', is_admin: true, privileges: {} } });
    }
    if (url.pathname === '/api/auth/settings') {
      return route.fulfill({ json: { agent_id: 'assistant', agent_constitution: 'Stay accurate.' } });
    }
    if (url.pathname === '/api/model-endpoints' && req.method() === 'POST') {
      addedPosts.push(req.postData() || '');
      return route.fulfill({
        json: {
          id: 'tn-2',
          name: '100.64.1.9:11434',
          base_url: 'http://100.64.1.9:11434',
          models: ['llama3.3'],
          endpoint_kind: 'tailnet',
          category: 'tailnet',
          online: true,
          is_enabled: true,
          status: 'online',
          model_type: 'llm',
        },
      });
    }
    if (
      ['/api/sessions', '/api/model-endpoints', '/api/models', '/api/plugins', '/api/tools'].includes(
        url.pathname
      )
    ) {
      return route.fulfill({ json: [] });
    }
    return route.fulfill({ json: {} });
  });

  await page.goto('/static/index.html');
  await page.evaluate(async () => (await import('/static/js/settings.js')).open());
  await expect(page.locator('#settings-modal')).toBeVisible();
  await page.locator('#settings-modal [data-settings-tab="services"]').click();

  await page.locator('#adm-epTailnetUrl').fill('100.64.1.9:11434');
  await page.locator('#adm-epTailnetAddBtn').click();
  await expect(page.locator('#adm-epTailnetMsg')).toContainText('Added with 1 model');

  const body = addedPosts[addedPosts.length - 1] || '';
  expect(body).toContain('http://100.64.1.9:11434');
  expect(body).toContain('tailnet');
});
