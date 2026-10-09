import { expect, test } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.route('https://tiles.openfreemap.org/styles/dark', (r) => r.fulfill({ json: {
    version: 8, sources: {}, layers: [{ id: 'background', type: 'background', paint: { 'background-color': '#191d23' } }],
  } }))
  await page.route('**/api/**', async (r) => {
    const path = new URL(r.request().url()).pathname
    let json: unknown = {}
    if (path === '/api/settings') json = { ref_pace_s_per_km: 330 }
    const saved = { id: 1, name: 'Percorso salvato test', notes: null, distance_m: 1000, elev_gain_m: 20, elev_loss_m: 20,
      target_speed_ms: 3, surface: { surface_m: { unknown: 1000 }, sectors: [] }, waypoints: null, updated_at: '2026-10-09' }
    if (path === '/api/routes') json = [saved]
    if (path === '/api/routes/1') json = { ...saved, coords: [[9.19, 45.46, 100], [9.20, 45.47, 120]] }
    if (path === '/api/activities') json = { items: [{ id: 1, name: 'Percorso test', start_time_utc: '2026-10-09', distance_m: 1000 }] }
    if (path.endsWith('/streams')) json = { channels: { lng: [9.19, 9.20], lat: [45.46, 45.47], altitude: [100, 120] } }
    if (path === '/api/routes/surface') json = { surface_m: { unknown: 1000 }, sectors: [] }
    if (path === '/api/routes/analyze') json = {
      distance_m: 1000, elev_gain_m: 20, elev_loss_m: 20, ele_min_m: 100, ele_max_m: 120,
      climb_grade_avg: 0.02, grade_max: 0.02, grade_bands_m: { flat: 1000, gentle: 0, steep: 0, down: 0 },
      turns: 0, turns_sharp: 0, hairpins: 0, sinuosity: 1, gap_distance_m: 1000,
      profile: { d: [0, 250, 500, 750, 1000], ele: [100, 110, 120, 110, 100], grade: [null, 0.04, 0.04, -0.04, -0.04],
        lng: [9.19, 9.1925, 9.195, 9.1975, 9.20], lat: [45.46, 45.4625, 45.465, 45.4675, 45.47] },
    }
    await r.fulfill({ json })
  })
})

test('planner switches between editing and panning and selects an elevation section', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  await page.goto('/routes')
  await page.getByLabel('Copia da corsa').selectOption('1')
  const points = page.locator('ol li')
  await expect(points).toHaveCount(2)
  const map = page.locator('.maplibregl-canvas')
  await expect(map).toBeVisible()
  await page.getByRole('button', { name: 'Sposta', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Sposta', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await map.click({ position: { x: 70, y: 140 } })
  const m = (await map.boundingBox())!
  await page.mouse.move(m.x + m.width / 2, m.y + m.height / 2)
  await page.mouse.down()
  await page.mouse.move(m.x + m.width / 2 + 50, m.y + m.height / 2 + 30, { steps: 5 })
  await page.mouse.up()
  await expect(points).toHaveCount(2)

  const chart = page.getByLabel('Profilo altimetrico: trascina per selezionare un tratto')
  await chart.scrollIntoViewIfNeeded()
  const box = (await chart.boundingBox())!
  await page.mouse.move(box.x + 44 + (box.width - 56) * 0.25, box.y + 60)
  await page.mouse.down()
  await page.mouse.move(box.x + 44 + (box.width - 56) * 0.75, box.y + 90, { steps: 10 })
  await page.mouse.up()
  const panel = page.locator('section, article').filter({ has: page.getByRole('heading', { name: 'Tratto selezionato', exact: true }) }).last()
  await expect(page.getByRole('heading', { name: 'Tratto selezionato', exact: true })).toBeVisible()
  const distance = Number.parseFloat(await panel.locator('dd').first().innerText())
  expect(distance).toBeGreaterThan(0.48)
  expect(distance).toBeLessThan(0.52)
  await expect(panel).toContainText('10 / 10 m')
  await page.getByRole('button', { name: 'Rimuovi selezione' }).click()
  await expect(page.getByRole('heading', { name: 'Tratto selezionato', exact: true })).toHaveCount(0)
  await page.getByRole('group', { name: 'Modalità mappa' }).getByRole('button', { name: 'Modifica', exact: true }).click()
  await page.getByRole('button', { name: 'Linea retta', exact: true }).click()
  await map.click({ position: { x: 70, y: 140 } })
  await expect(points).toHaveCount(3)
  expect(errors).toEqual([])
})

test('saved routes can be viewed without editing and then switched to edit mode', async ({ page }) => {
  const writes: string[] = []
  page.on('request', (r) => {
    if (r.url().endsWith('/api/routes/1') && r.method() !== 'GET') writes.push(r.method())
  })
  await page.goto('/routes')
  await page.getByRole('button', { name: 'Visualizza', exact: true }).click()
  await expect(page.getByText('Visualizzazione:', { exact: false })).toBeVisible()
  const modes = page.getByRole('group', { name: 'Modalità mappa' })
  await expect(modes.getByRole('button', { name: 'Modifica', exact: true })).toBeDisabled()
  await expect(modes.getByRole('button', { name: 'Sposta', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByRole('button', { name: 'Aggiorna', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Rimuovi il punto 1', exact: true })).toHaveCount(0)
  const map = page.locator('.maplibregl-canvas')
  await map.click({ position: { x: 70, y: 140 } })
  await expect(page.locator('ol li')).toHaveCount(2)
  const chart = page.getByLabel('Profilo altimetrico: trascina per selezionare un tratto')
  await chart.scrollIntoViewIfNeeded()
  const box = (await chart.boundingBox())!
  await page.mouse.move(box.x + 44 + (box.width - 56) * 0.25, box.y + 60)
  await page.mouse.down()
  await page.mouse.move(box.x + 44 + (box.width - 56) * 0.75, box.y + 90, { steps: 10 })
  await page.mouse.up()
  await expect(page.getByRole('heading', { name: 'Tratto selezionato', exact: true })).toBeVisible()
  expect(writes).toEqual([])
  await page.getByRole('button', { name: 'Evidenzia salita continua più lunga', exact: true }).click()
  const selection = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Tratto selezionato', exact: true }) })
  await expect(selection).toContainText('Da 0.00 km a 0.50 km')
  await page.getByRole('button', { name: 'Evidenzia discesa continua più lunga', exact: true }).click()
  await expect(selection).toContainText('Da 0.50 km a 1.00 km')
  await page.getByRole('button', { name: 'Modifica percorso', exact: true }).click()
  await expect(modes.getByRole('button', { name: 'Modifica', exact: true })).toBeEnabled()
  await expect(page.getByRole('button', { name: 'Aggiorna', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Rimuovi il punto 1', exact: true })).toBeVisible()
  const savedPanel = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Percorsi salvati', exact: true }) })
  await savedPanel.getByRole('button', { name: 'Visualizza', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Modifica percorso', exact: true })).toBeVisible()
  await savedPanel.getByRole('button', { name: 'Modifica', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Aggiorna', exact: true })).toBeVisible()
})
