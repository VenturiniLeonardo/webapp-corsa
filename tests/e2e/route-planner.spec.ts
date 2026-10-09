import { expect, test } from '@playwright/test'

test('planner switches between editing and panning and selects an elevation section', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  await page.route('https://tiles.openfreemap.org/styles/dark', (r) => r.fulfill({ json: {
    version: 8, sources: {}, layers: [{ id: 'background', type: 'background', paint: { 'background-color': '#191d23' } }],
  } }))
  await page.route('**/api/**', async (r) => {
    const path = new URL(r.request().url()).pathname
    let json: unknown = {}
    if (path === '/api/settings') json = { ref_pace_s_per_km: 330 }
    if (path === '/api/routes') json = []
    if (path === '/api/activities') json = { items: [{ id: 1, name: 'Percorso test', start_time_utc: '2026-10-09', distance_m: 1000 }] }
    if (path.endsWith('/streams')) json = { channels: { lng: [9.19, 9.20], lat: [45.46, 45.47], altitude: [100, 120] } }
    if (path === '/api/routes/surface') json = { surface_m: { unknown: 1000 }, sectors: [] }
    if (path === '/api/routes/analyze') json = {
      distance_m: 1000, elev_gain_m: 20, elev_loss_m: 0, ele_min_m: 100, ele_max_m: 120,
      climb_grade_avg: 0.02, grade_max: 0.02, grade_bands_m: { flat: 1000, gentle: 0, steep: 0, down: 0 },
      turns: 0, turns_sharp: 0, hairpins: 0, sinuosity: 1, gap_distance_m: 1000,
      profile: { d: [0, 250, 500, 750, 1000], ele: [100, 105, 110, 115, 120], grade: [null, 0.02, 0.02, 0.02, 0.02],
        lng: [9.19, 9.1925, 9.195, 9.1975, 9.20], lat: [45.46, 45.4625, 45.465, 45.4675, 45.47] },
    }
    await r.fulfill({ json })
  })
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
  await expect(panel).toContainText('10 / 0 m')
  await page.getByRole('button', { name: 'Rimuovi selezione' }).click()
  await expect(page.getByRole('heading', { name: 'Tratto selezionato', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: 'Modifica', exact: true }).click()
  await page.getByRole('button', { name: 'Linea retta', exact: true }).click()
  await map.click({ position: { x: 70, y: 140 } })
  await expect(points).toHaveCount(3)
  expect(errors).toEqual([])
})
