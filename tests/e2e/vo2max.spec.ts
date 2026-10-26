import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

async function openFitness(page: Page) {
  await expect(page.getByRole('heading', { name: 'La tua stagione' })).toBeVisible()
  const details = page.getByRole('button', { name: /Analisi approfondite/ })
  if (await details.count() && await details.getAttribute('aria-expanded') === 'false') await details.click()
}

test('VO2max shows recent HR estimate before VDOT and refreshes on return', async ({ page }) => {
  let value = 42.3
  await page.route('**/api/stats/fitness', async (route) => {
    const response = await route.fetch()
    const data = await response.json()
    data.vo2max = { vdot: 49.8, vdot_source: '5K', vdot_date: '2026-09-01', hr_based: value, hr_based_n: 5, hr_based_date: '2026-10-25', hr_based_activity_id: 164, hr_configured: true }
    await route.fulfill({ json: data })
  })
  await page.goto('/')
  await openFitness(page)
  const card = page.getByText('VO₂max stimata · da frequenza cardiaca', { exact: true }).locator('..')
  await expect(card).toContainText('42,3')
  await expect(card).toContainText('VDOT Daniels: 49,8')
  await expect(card.getByRole('link', { name: '2026-10-25' })).toHaveAttribute('href', '/activities/164')
  await expect(card).toContainText('5 corse con tratti regolari')
  await page.locator('a[href="/activities"]').first().click()
  await expect(page).toHaveURL(/\/activities$/)
  await expect(card).toHaveCount(0)
  value = 43.1
  await page.goBack()
  await openFitness(page)
  await expect(card).toContainText('43,1')
})

test('VDOT fallback explains whether HR settings or usable runs are missing', async ({ page }) => {
  let configured = false
  await page.route('**/api/stats/fitness', async (route) => {
    const response = await route.fetch()
    const data = await response.json()
    data.vo2max = { vdot: 49.8, vdot_source: '5K', vdot_date: '2026-09-01', hr_based: null, hr_based_n: 0, hr_based_date: null, hr_based_activity_id: null, hr_configured: configured }
    await route.fulfill({ json: data })
  })
  await page.goto('/')
  await openFitness(page)
  await expect(page.getByText('VDOT da prestazione', { exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Per la stima da FC, imposta FC massima e a riposo' })).toBeVisible()
  configured = true
  await page.reload()
  await openFitness(page)
  await expect(page.getByText('Da FC: nessuna corsa con tratti regolari e dati validi negli ultimi 28 gg')).toBeVisible()
})
