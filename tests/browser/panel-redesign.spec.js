const { test, expect } = require('@playwright/test');
const AxeBuilder = require('@axe-core/playwright').default;

async function signIn(page) {
  await page.goto('/login');
  await page.locator('#login-username').fill('admin');
  await page.locator('#login-password').fill('browser-password-1234');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.locator('[data-view="dashboard"]')).toBeVisible();
  await expect(page.locator('body')).toHaveClass(/hp-redesign/);
  await expect(page.locator('#languageSelect')).toHaveValue('en');
  await expect(page.locator('#dashboardRetry')).toHaveAttribute('aria-label', 'Refresh dashboard data');
}

async function expectNoViewportOverflow(page) {
  const overflow = await page.evaluate(() => {
    const root = document.documentElement;
    const body = document.body;
    return {
      viewport: window.innerWidth,
      rootClient: root.clientWidth,
      rootScroll: root.scrollWidth,
      bodyClient: body.clientWidth,
      bodyScroll: body.scrollWidth,
    };
  });
  expect(overflow.rootScroll).toBeLessThanOrEqual(overflow.rootClient + 2);
  expect(overflow.bodyScroll).toBeLessThanOrEqual(overflow.bodyClient + 2);
  expect(overflow.rootClient).toBe(overflow.viewport);
}

async function expectTouchTargets(page, selectors) {
  for (const selector of selectors) {
    const elements = page.locator(selector).filter({ visible: true });
    const count = await elements.count();
    for (let index = 0; index < count; index += 1) {
      const box = await elements.nth(index).boundingBox();
      expect(box, `${selector}[${index}] should have a box`).not.toBeNull();
      expect(box.width, `${selector}[${index}] width`).toBeGreaterThanOrEqual(44);
      expect(box.height, `${selector}[${index}] height`).toBeGreaterThanOrEqual(44);
    }
  }
}

test('desktop dashboard has a stable information hierarchy', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page);

  await expect(page.locator('#sidebar')).toBeVisible();
  await expect(page.locator('.hp-dashboard')).toBeVisible();
  await expect(page.locator('.hp-metric-card')).toHaveCount(4);
  await expect(page.locator('.hp-dashboard-layout')).toBeVisible();
  await expect(page.locator('.hp-dashboard-rail')).toBeVisible();
  await expect(page.locator('.hp-health-ring')).toBeVisible();
  await expect(page.locator('.hp-quick-action:not([hidden])')).toHaveCount(6);
  await expect(page.locator('.hp-services-card')).toBeVisible();
  await expect(page.locator('#dashboardState')).toBeVisible();
  await expect(page.locator('#dashboardRetry')).toBeVisible();

  const layout = await page.locator('.hp-dashboard-layout').evaluate(element =>
    getComputedStyle(element).gridTemplateColumns.split(' ').length
  );
  expect(layout).toBeGreaterThanOrEqual(2);
  await expectNoViewportOverflow(page);
  await page.screenshot({ path: testInfo.outputPath('desktop-dashboard.png'), fullPage: true });
});

test('desktop keyboard, routing, dark mode and accessibility remain intact', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page);

  await page.keyboard.press(process.platform === 'darwin' ? 'Meta+K' : 'Control+K');
  await expect(page.locator('#globalSearch')).toBeFocused();
  await page.locator('#globalSearch').fill('domains');
  await page.keyboard.press('Escape');
  await expect(page.locator('#globalSearch')).not.toBeFocused();

  await page.locator('.hp-quick-action[data-hp-page="domains"]').click();
  await expect(page).toHaveURL(/#\/panel\/domains$/);
  await expect(page.locator('#crumb')).toContainText(/Domains/i);
  await page.goBack();
  await expect(page.locator('[data-view="dashboard"]')).toBeVisible();

  await page.locator('#themeToggle').click();
  await expect(page.locator('body')).toHaveClass(/dark/);
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations.filter(item => ['critical', 'serious'].includes(item.impact))).toEqual([]);
});

test('phone layout is touch-safe, drawer-safe and free of viewport overflow', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await signIn(page);

  await expect(page.locator('.hp-metric-card')).toHaveCount(4);
  await expect(page.locator('.hp-dashboard-layout')).toBeVisible();
  await expect(page.locator('.hp-dashboard-rail')).toBeVisible();
  await expect(page.locator('.hp-quick-action:not([hidden])')).toHaveCount(6);
  await expectNoViewportOverflow(page);

  const closedDrawer = await page.locator('#sidebar').boundingBox();
  expect(closedDrawer).not.toBeNull();
  expect(closedDrawer.x + closedDrawer.width).toBeLessThanOrEqual(1);

  await page.locator('#menuBtn').click();
  await expect(page.locator('#sidebar')).toHaveClass(/open/);
  await expect(page.locator('#menuBtn')).toHaveAttribute('aria-expanded', 'true');
  await expect(page.locator('#content')).toHaveAttribute('inert', '');
  const openDrawer = await page.locator('#sidebar').boundingBox();
  expect(openDrawer).not.toBeNull();
  expect(openDrawer.x).toBeGreaterThanOrEqual(-1);
  expect(openDrawer.width).toBeLessThanOrEqual(390 * 0.88 + 2);

  await expectTouchTargets(page, [
    '#menuBtn',
    '#themeToggle',
    '#jobBell',
    '.language-select',
    '#sidebar .nav-section > button',
    '#sidebar .nav a:not([hidden])',
  ]);

  await page.keyboard.press('Escape');
  await expect(page.locator('#sidebar')).not.toHaveClass(/open/);
  await expect(page.locator('#menuBtn')).toHaveAttribute('aria-expanded', 'false');
  await expect(page.locator('#content')).not.toHaveAttribute('inert', '');
  await expect(page.locator('#menuBtn')).toBeFocused();

  await expectTouchTargets(page, [
    '.hp-quick-action:not([hidden])',
    '.hp-security-link:not([hidden])',
    '.dashboard-state .btn:not([hidden])',
    '.hp-health-row .btn:not([hidden])',
  ]);

  const services = page.locator('.hp-services-card .table-wrap');
  await expect(services).toBeVisible();
  const tableOverflow = await services.evaluate(element => ({
    client: element.clientWidth,
    scroll: element.scrollWidth,
    overflowX: getComputedStyle(element).overflowX,
  }));
  expect(tableOverflow.scroll).toBeGreaterThan(tableOverflow.client);
  expect(['auto', 'scroll']).toContain(tableOverflow.overflowX);
  await expectNoViewportOverflow(page);
  await page.screenshot({ path: testInfo.outputPath('phone-dashboard.png'), fullPage: true });
});

test('tablet transition keeps cards, rail and controls usable', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await signIn(page);
  await expect(page.locator('.hp-dashboard-layout')).toBeVisible();
  await expect(page.locator('.hp-dashboard-rail')).toBeVisible();
  await expect(page.locator('.hp-metric-card')).toHaveCount(4);
  const columns = await page.locator('.hp-metric-grid').evaluate(element =>
    getComputedStyle(element).gridTemplateColumns.split(' ').length
  );
  expect(columns).toBe(2);
  await expectNoViewportOverflow(page);
});
