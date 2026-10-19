import { expect, test } from '@playwright/test'

test('record: predictions render below records and fit a mobile screen', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.setViewportSize({ width: 390, height: 844 })
  const response = page.waitForResponse(r => r.url().endsWith('/api/records/predictions'))
  await page.goto('/records')
  expect((await response).ok()).toBe(true)
  const section = page.getByRole('region', { name: 'Previsione passo gara' })
  await expect(section).toBeVisible()
  await expect(section.locator('article')).toHaveCount(4)
  for (const name of ['5 km', '10 km', '21,1 km', '42,2 km']) {
    await expect(section.getByRole('heading', { name, exact: true })).toBeVisible()
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await section.getByText('Come leggere le previsioni').click()
  await expect(section.getByText(/non intervalli statistici validati/)).toBeVisible()
  await section.screenshot({ path: 'test-results/race-predictions-mobile.png' })
  await page.setViewportSize({ width: 1440, height: 1000 })
  await section.screenshot({ path: 'test-results/race-predictions-desktop.png' })
  expect(errors).toEqual([])
})

test('record: current weather, missing data and retry are explicit', async ({ page }) => {
  let failed = true
  await page.route('**/api/records/predictions', async route => {
    if (failed) return route.fulfill({ status: 503, json: { detail: 'offline' } })
    return route.fulfill({ json: {
      from_date: '2026-08-25', to_date: '2026-10-19', runs_8w: 0, hr_runs_8w: 0,
      steady_hr_runs_8w: 0, hr_trend_applied: false, hr_efficiency_change_pct: null,
      avg_weekly_distance_m: 0, longest_run_m: 0, heat_adjustment_pct: 2,
      notices: ['Servono almeno 3 corse nelle ultime 8 settimane.'],
      weather: { status: 'current', temp_c: 24, dew_point_c: 18, observed_at: '2026-10-19T12:00:00Z', location_activity_id: 1 },
      items: ['5 km', '10 km', '21,1 km', '42,2 km'].map((label, i) => ({
        distance_m: [5000, 10000, 21097.5, 42195][i], label, elapsed_s: null,
        pace_s_per_km: null, pace_low_s_per_km: null, pace_high_s_per_km: null,
        confidence: 'unavailable', reasons: ['Dati insufficienti.'], anchor_activity_id: null,
      })),
    } })
  })
  await page.goto('/records')
  await expect(page.getByRole('alert')).toContainText('Impossibile aggiornare', { timeout: 20000 })
  failed = false
  await page.getByRole('button', { name: 'Aggiorna stime' }).click()
  await expect(page.getByText(/24.0 °C/)).toBeVisible()
  await expect(page.getByText('Tempo non stimabile')).toHaveCount(4)
  await expect(page.getByRole('alert')).toHaveCount(0)
})

test('record: select a city, remember it on reload and restore automatic location', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.route('**/api/records/predictions/locations?*', route => route.fulfill({ json: [
    { id: 1, label: 'Roma, Lazio, Italia', latitude: 41.9, longitude: 12.5 },
    { id: 2, label: 'Roma, Australia', latitude: -26.6, longitude: 148.8 },
  ] }))
  await page.route('**/api/records/predictions?*', async route => {
    const url = new URL(route.request().url())
    expect(url.searchParams.get('latitude')).toBe('41.9')
    expect(url.searchParams.get('longitude')).toBe('12.5')
    const response = await route.fetch()
    const data = await response.json()
    data.weather = { status: 'current', location_mode: 'custom', location_name: 'Roma, Lazio, Italia', temp_c: 30, dew_point_c: 22, observed_at: null }
    await route.fulfill({ json: data })
  })
  await page.goto('/records')
  await page.getByLabel('Città o località per il calcolo').fill('Roma')
  await page.getByRole('button', { name: 'Cerca località' }).click()
  await expect(page.getByRole('button', { name: 'Roma, Australia', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Roma, Lazio, Italia', exact: true }).click()
  await expect(page.getByText(/30.0 °C/)).toBeVisible()
  await expect(page.getByText('Meteo attuale · Roma, Lazio, Italia')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.reload()
  await expect(page.getByText(/30.0 °C/)).toBeVisible()
  await page.getByRole('button', { name: 'Usa ultima corsa GPS' }).click()
  await expect(page.getByText('Automatica · ultima corsa GPS')).toBeVisible()
  await expect(page.getByText('Meteo attuale · zona dell’ultima corsa GPS')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Usa ultima corsa GPS' })).toHaveCount(0)
  expect(await page.evaluate(() => localStorage.getItem('corsa.predictionLocation'))).toBeNull()
})
