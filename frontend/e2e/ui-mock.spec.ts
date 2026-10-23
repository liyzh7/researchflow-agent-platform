// New P2 fixtures. This suite mocks HTTP responses as well as AI; it does not verify PostgreSQL.
import { expect, test } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/v1/auth/login') {
      await route.fulfill({
        json: {
          access_token: 'p2-browser-fictional-session',
          expires_at: new Date(Date.now() + 300000).toISOString(),
        },
      });
    } else if (path === '/api/v1/auth/me') {
      await route.fulfill({
        json: { id: 'p2-fictional-user', username: 'p2-ui-fixture', is_admin: false },
      });
    } else if (path === '/api/v1/info') {
      await route.fulfill({
        json: {
          version: '0.8.0rc1',
          environment: 'test',
          provider_mode: 'mock',
          rag_enabled: false,
          agent_enabled: false,
        },
      });
    } else if (path === '/api/v1/auth/logout') {
      await route.fulfill({ status: 204 });
    } else {
      await route.fulfill({ json: { items: [], total: 0, limit: 100, offset: 0 } });
    }
  });
  await page.route('**/ready', (route) =>
    route.fulfill({ json: { status: 'ready', pgvector_version: 'mock-only' } }),
  );
  await page.goto('/');
});

async function login(page: import('@playwright/test').Page) {
  await page.getByLabel('用户名', { exact: true }).fill('p2-ui-fixture');
  await page.getByLabel('密码', { exact: true }).fill('p2-fictional-browser-password');
  await page.getByRole('button', { name: '进入科研工作台' }).click();
  await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible();
}

test('login boundary and memory-only credentials', async ({ page }) => {
  await expect(page.getByRole('heading', { name: '欢迎回到工作台' })).toBeVisible();
  await expect(page.getByRole('navigation')).toHaveCount(0);
  await login(page);
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
  await page.reload();
  await expect(page.getByRole('heading', { name: '欢迎回到工作台' })).toBeVisible();
});

test('HTTP-mocked workspace shows empty state and disabled AI', async ({ page }) => {
  await login(page);
  await expect(page.getByText('研究空间准备就绪')).toBeVisible();
  await page.getByRole('link', { name: '智能问答', exact: true }).click();
  await expect(page.getByRole('button', { name: '提交问题' })).toBeDisabled();
  await page.getByRole('link', { name: '系统状态', exact: true }).click();
  await expect(page.locator('.status-grid')).toContainText('Mock 测试模式');
});

test('HTTP-mocked logout restores protected boundary', async ({ page }) => {
  await login(page);
  await page.getByRole('button', { name: '注销' }).filter({ visible: true }).click();
  await expect(page.getByRole('heading', { name: '欢迎回到工作台' })).toBeVisible();
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
});
