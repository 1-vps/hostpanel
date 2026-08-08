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
    const elements = page.locator(selector);
    const count = await elements.count();
    for (let index = 0; index < count; index += 1) {
      const element = elements.nth(index);
      if (!(await element.isVisible())) continue;
      const box = await element.boundingBox();
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

  const servicesCard = page.locator('.hp-services-card');
  await servicesCard.scrollIntoViewIfNeeded();
  await expect(servicesCard).toBeInViewport();
  await page.screenshot({ path: testInfo.outputPath('desktop-dashboard-bottom.png') });
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

  const lightResults = await new AxeBuilder({ page }).analyze();
  expect(lightResults.violations.filter(item => ['critical', 'serious'].includes(item.impact))).toEqual([]);
  await page.locator('#themeToggle').click();
  await expect(page.locator('body')).toHaveClass(/dark/);
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations.filter(item => ['critical', 'serious'].includes(item.impact))).toEqual([]);
});

test('dashboard actions follow asynchronous permission changes', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page);

  const navLink = page.locator('#nav a[data-page="security"]');
  const quickAction = page.locator('.hp-quick-action[data-hp-page="security"]');
  const railLinks = page.locator('.hp-security-link[data-hp-page="security"]');

  await navLink.evaluate(element => {
    element.hidden = false;
    element.removeAttribute('aria-hidden');
  });
  await expect(navLink).not.toHaveAttribute('hidden', '');
  await expect(quickAction).toBeVisible();
  await expect(railLinks).toBeVisible();

  await navLink.evaluate(element => { element.hidden = true; });
  await expect(quickAction).toBeHidden();
  await expect(railLinks).toBeHidden();
  await expect(quickAction).toHaveAttribute('aria-disabled', 'true');

  await navLink.evaluate(element => { element.hidden = false; });
  await expect(quickAction).toBeVisible();
  await expect(railLinks).toBeVisible();
  await expect(quickAction).not.toHaveAttribute('aria-disabled', 'true');

  for (const mutation of [
    element => element.setAttribute('aria-disabled', 'true'),
    element => element.classList.add('disabled'),
    element => element.style.pointerEvents = 'none',
  ]) {
    await navLink.evaluate(mutation);
    await expect(quickAction).toBeHidden();
    await navLink.evaluate(element => {
      element.removeAttribute('aria-disabled');
      element.classList.remove('disabled');
      element.style.removeProperty('pointer-events');
    });
    await expect(quickAction).toBeVisible();
  }
});

test('degraded health, service actions and dashboard freshness are truthful', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page);

  await expect(page.locator('#dashboardUpdated')).toHaveText('Dashboard data loaded');
  const inactiveRow = page.locator('#svcBody tr').filter({ has: page.locator('.tag:not(.ok)') }).first();
  await expect(inactiveRow.locator('td:last-child button')).toHaveText('Start');
  await expect(page.locator('#hpHealthRing')).not.toHaveAttribute('data-state', 'ok');
  await expect(page.locator('#hpHealthAlert')).toBeVisible();

  const button = inactiveRow.locator('td:last-child button');
  await button.click();
  await expect(page.locator('#hpServiceDialog')).toBeVisible();
  await page.locator('#hpServiceDialog button[value="cancel"]').click();
  await expect(page.locator('#hpServiceDialog')).not.toBeVisible();
  await expect(button).toBeFocused();
});

test('release-candidate locales update dynamic dashboard copy', async ({ page }) => {
  await page.route('**/api/settings/language', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ ok: true }),
  }));
  await page.route(/\/static\/i18n\.(ja|pt|zh)\.json$/, route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: '{}',
  }));

  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page);

  const cases = [
    ['ja', 'ダッシュボードのデータを更新', 'ダッシュボードの概要', '開く'],
    ['pt', 'Atualizar os dados do painel', 'Visão geral do painel', 'Abrir'],
    ['zh', '刷新仪表板数据', '仪表板概览', '打开'],
  ];
  await page.locator('#languageSelect').evaluate((select, locales) => {
    for (const locale of locales) {
      if (select.querySelector(`option[value="${locale}"]`)) continue;
      const option = document.createElement('option');
      option.value = locale;
      option.textContent = locale;
      select.append(option);
    }
  }, cases.map(([locale]) => locale));

  await page.evaluate(() => {
    window.__hostpanelOriginalNormalizeLanguage = window.hpNormalizeLanguage;
    window.hpNormalizeLanguage = value => String(value || '').toLowerCase().split('-')[0];
  });
  try {
    for (const [locale, refresh, overview, open] of cases) {
      await page.locator('#languageSelect').selectOption(locale);
      await expect(page.locator('#languageSelect')).toHaveValue(locale);
      await expect(page.locator('#dashboardRetry')).toHaveAttribute('aria-label', refresh);
      await expect(page.locator('.hp-dashboard-rail')).toHaveAttribute('aria-label', overview);
      await expect(page.locator('.hp-health-row [data-hp-page="security"]')).toHaveText(open);
    }
  } finally {
    await page.evaluate(() => {
      window.hpNormalizeLanguage = window.__hostpanelOriginalNormalizeLanguage;
      delete window.__hostpanelOriginalNormalizeLanguage;
    });
  }
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
  const reducedMotion = await page.locator('.hp-meter > span').first().evaluate(element => ({
    animation: getComputedStyle(element).animationDuration,
    transition: getComputedStyle(element).transitionDuration,
  }));
  expect(parseFloat(reducedMotion.animation) || 0).toBeLessThanOrEqual(0.01);
  expect(parseFloat(reducedMotion.transition) || 0).toBeLessThanOrEqual(0.01);

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
  await expect(page.locator('.hp-services-card tbody tr').first()).toHaveCSS('display', 'grid');
  await expectTouchTargets(page, ['.hp-services-card tbody td:last-child .btn']);
  await expectNoViewportOverflow(page);
  await page.screenshot({ path: testInfo.outputPath('phone-dashboard.png'), fullPage: true });

  const servicesCard = page.locator('.hp-services-card');
  await servicesCard.scrollIntoViewIfNeeded();
  await expect(servicesCard).toBeInViewport();
  await page.screenshot({ path: testInfo.outputPath('phone-dashboard-bottom.png') });
});

test('narrow and zoom-equivalent layout keeps primary controls usable', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 720 });
  await signIn(page);
  await expectNoViewportOverflow(page);
  await expectTouchTargets(page, [
    '#menuBtn',
    '#themeToggle',
    '#jobBell',
    '.hp-quick-action:not([hidden])',
    '.hp-services-card tbody td:last-child .btn',
  ]);
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
