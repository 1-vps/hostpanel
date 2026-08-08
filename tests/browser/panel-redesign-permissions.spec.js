const { test, expect } = require('@playwright/test');

async function signIn(page) {
  await page.goto('/login');
  await page.locator('#login-username').fill('admin');
  await page.locator('#login-password').fill('browser-password-1234');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.locator('[data-view="dashboard"]')).toBeVisible();
  await expect(page.locator('body')).toHaveClass(/hp-redesign/);
}

async function expectControlsHidden(controls, hidden) {
  const count = await controls.count();
  expect(count).toBeGreaterThanOrEqual(2);
  for (let index = 0; index < count; index += 1) {
    const control = controls.nth(index);
    if (hidden) {
      await expect(control).toBeHidden();
      await expect(control).toHaveAttribute('aria-disabled', 'true');
    } else {
      await expect(control).toBeVisible();
      await expect(control).not.toHaveAttribute('aria-disabled', 'true');
    }
  }
}

test('quick navigation follows CSS-hidden permission links', async ({ page }) => {
  await signIn(page);

  const navLink = page.locator('#nav a[data-page="security"]');
  const controls = page.locator('[data-hp-page="security"]');
  await expect(navLink).toBeVisible();
  await expectControlsHidden(controls, false);

  await navLink.evaluate((link) => {
    link.style.display = 'none';
  });
  await expectControlsHidden(controls, true);

  await navLink.evaluate((link) => {
    link.style.removeProperty('display');
  });
  await expectControlsHidden(controls, false);

  await page.addStyleTag({
    content: '.hp-permission-hidden { display: none !important; }',
  });
  await navLink.evaluate((link) => {
    link.classList.add('hp-permission-hidden');
  });
  await expectControlsHidden(controls, true);

  await navLink.evaluate((link) => {
    link.classList.remove('hp-permission-hidden');
  });
  await expectControlsHidden(controls, false);
});
