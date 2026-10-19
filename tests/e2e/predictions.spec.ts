import { expect, test } from '@playwright/test'

test('record: v2 cards fit mobile and expose methodology and audit', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.setViewportSize({ width: 390, height: 844 })
  const response = page.waitForResponse(r => r.url().endsWith('/api/records/predictions'))
  await page.goto('/records')
  expect((await response).ok()).toBe(true)
  const section = page.getByRole('region', { name: 'Previsione passo gara' })
  await expect(section.locator('article')).toHaveCount(4)
  for (const name of ['5 km', '10 km', '21,1 km', '42,2 km']) {
    await expect(section.getByRole('heading', { name, exact: true })).toBeVisible()
  }
  await expect(section.getByText('Condizioni standard · piano')).toHaveCount(4)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await section.getByText('Come leggere le previsioni', { exact: true }).click()
  await expect(section.getByText(/non intervalli statistici validati/)).toBeVisible()
  const download = page.waitForEvent('download')
  await section.getByRole('button', { name: 'Esporta dati del calcolo' }).click()
  expect((await download).suggestedFilename()).toMatch(/previsioni.*\.json/)
  await section.screenshot({ path: 'test-results/race-predictions-mobile.png' })
  await page.setViewportSize({ width: 1440, height: 1000 })
  await section.screenshot({ path: 'test-results/race-predictions-desktop.png' })
  expect(errors).toEqual([])
})

test('record: API outage and retry preserve explicit missing estimates', async ({ page }) => {
  let failed = true
  await page.route('**/api/records/predictions', async route => {
    if (failed) return route.fulfill({ status: 503, json: { detail: 'Servizio temporaneamente offline' } })
    const response = await route.fetch()
    const data = await response.json()
    data.items = data.items.map((item: Record<string, unknown>) => ({ ...item,
      elapsed_s: null, pace_s_per_km: null, pace_low_s_per_km: null, pace_high_s_per_km: null,
      elapsed_low_s: null, elapsed_high_s: null, confidence: 'unavailable', sources: [], scenario: null,
    }))
    await route.fulfill({ json: data })
  })
  await page.goto('/records')
  await expect(page.getByRole('alert')).toContainText('Servizio temporaneamente offline', { timeout: 20000 })
  failed = false
  await page.getByRole('button', { name: 'Aggiorna stime' }).click()
  await expect(page.getByText('Tempo non stimabile')).toHaveCount(4)
  await expect(page.getByRole('alert')).toHaveCount(0)
})

test('record: city and dated scenario persist independently of standard estimates', async ({ page }) => {
  const baseline = await (await page.request.get('/api/records/predictions')).json()
  await page.setViewportSize({ width: 390, height: 844 })
  await page.route('**/api/records/predictions/locations?*', route => route.fulfill({ json: [
    { id: 1, label: 'Roma, Lazio, Italia', latitude: 41.9, longitude: 12.5, timezone: 'Europe/Rome' },
    { id: 2, label: 'Roma, Australia', latitude: -26.6, longitude: 148.8 },
  ] }))
  await page.route('**/api/records/predictions?*', async route => {
    const url = new URL(route.request().url())
    const data = structuredClone(baseline)
    data.weather.location_name = url.searchParams.get('location_name') ?? 'Zona ultima corsa GPS'
    if (url.searchParams.get('race_month')) {
      data.weather = { ...data.weather, status: 'climatology', message: 'Scenario climatologico del periodo, non una previsione del tempo della gara.', sample_years: 10 }
      data.items = data.items.map((item: Record<string, unknown>) => ({ ...item,
        scenario: item.elapsed_s ? { ...item, weather_status: 'climatology', temp_c: 25, dew_point_c: 18, heat_adjustment_pct: 3, extreme: false } : null,
      }))
    }
    await route.fulfill({ json: data })
  })
  await page.goto('/records')
  const section = page.getByRole('region', { name: 'Previsione passo gara' })
  await expect(section.locator('article')).toHaveCount(4)
  const standards = await section.locator('article > p.font-mono.text-sm').allTextContents()
  await page.getByLabel('Città o località per il calcolo').fill('Roma')
  await page.getByRole('button', { name: 'Cerca località' }).click()
  await page.getByRole('button', { name: 'Roma, Lazio, Italia', exact: true }).click()
  await expect(section.getByText('Roma, Lazio, Italia', { exact: true })).toBeVisible()
  await page.getByRole('combobox', { name: /^Scenario meteo/ }).selectOption('month')
  await page.getByLabel('Mese gara').selectOption('11')
  await page.getByRole('button', { name: 'Applica scenario' }).click()
  await expect(section.getByText(/Scenario climatologico del periodo/)).toBeVisible()
  expect(await section.locator('article > p.font-mono.text-sm').allTextContents()).toEqual(standards)
  await page.reload()
  await expect(section.getByText('Roma, Lazio, Italia', { exact: true })).toBeVisible()
  await expect(page.getByRole('combobox', { name: /^Scenario meteo/ })).toHaveValue('month')
  await page.getByRole('button', { name: 'Usa ultima corsa GPS' }).click()
  await expect(page.getByText('Automatica · ultima corsa GPS', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => localStorage.getItem('corsa.predictionLocation'))).toBeNull()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})

test('record: explicit profile confirmations save and survive reload', async ({ page }) => {
  await page.goto('/records')
  await page.getByText('Dati e conferme per le previsioni', { exact: true }).click()
  await page.getByLabel('Sensore FC abituale').selectOption('chest')
  await page.getByLabel('FC soglia misurata (bpm)').fill('170')
  const saved = page.waitForResponse(r => r.url().endsWith('/prediction-inputs/profile') && r.request().method() === 'PUT')
  await page.getByRole('button', { name: 'Salva dati del profilo' }).click()
  expect((await saved).ok()).toBe(true)
  await page.reload()
  await page.getByText('Dati e conferme per le previsioni', { exact: true }).click()
  await expect(page.getByLabel('Sensore FC abituale')).toHaveValue('chest')
  await expect(page.getByLabel('FC soglia misurata (bpm)')).toHaveValue('170')
})
