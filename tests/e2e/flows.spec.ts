import { expect, test } from '@playwright/test'

test('1. dashboard loads and period change updates the volume chart', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  await page.goto('/')
  const chart = page.locator('section', { has: page.getByRole('heading', { name: 'Volume' }) }).locator('canvas')
  await expect(chart).toBeVisible()

  const reload = page.waitForResponse((r) => r.url().includes('/api/stats/volume') && r.url().includes('from_date'))
  await page.getByRole('button', { name: '4W', exact: true }).click()
  expect((await reload).ok()).toBe(true)
  await expect(page.getByRole('button', { name: '4W', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(chart).toBeVisible()
  await expect(page.getByText('Failed to load stats.')).toHaveCount(0)
  expect(errors).toEqual([])
})

test('2. activities: filter by distance, open run, map and synced charts render', async ({ page }) => {
  await page.goto('/activities')
  const rows = page.locator('tbody tr')
  await expect(rows.first()).toBeVisible()
  const before = await rows.count()

  const filtered = page.waitForResponse((r) => r.url().includes('dist_min=15000'))
  await page.getByLabel('dmin').fill('15')
  expect((await filtered).ok()).toBe(true)
  await expect(rows).not.toHaveCount(before) // long runs only: 15+ km is a subset of the 50 shown
  await rows.first().click()

  await expect(page).toHaveURL(/\/activities\/\d+$/)
  await expect(page.locator('.maplibregl-canvas')).toBeVisible()
  const charts = page.locator('[_echarts_instance_]')
  await expect(charts.nth(2)).toBeVisible() // pace, HR, elevation at least
  await expect(charts.locator('canvas').first()).toBeVisible()

  // hovering the first chart shows the tooltip (x in km) on the last one too: connected-chart sync
  await charts.first().scrollIntoViewIfNeeded()
  const box = (await charts.first().boundingBox())!
  await page.mouse.move(box.x + box.width * 0.3, box.y + box.height / 2)
  await page.mouse.move(box.x + box.width * 0.5, box.y + box.height / 2, { steps: 5 })
  await expect(charts.last()).toContainText('km')
})

test('3. edit notes and workout type, persisted after reload', async ({ page }) => {
  await page.goto('/activities')
  await page.locator('tbody tr').first().click()
  await expect(page).toHaveURL(/\/activities\/\d+$/)

  const type = page.getByLabel('Workout type')
  const notes = page.getByLabel('Notes')
  await expect(type).toBeVisible()
  const newType = (await type.inputValue()) === 'race' ? 'easy' : 'race'
  const text = `e2e note ${Date.now()}`

  const saved = () => page.waitForResponse((r) => r.request().method() === 'PATCH' && r.ok())
  let done = saved()
  await type.selectOption(newType)
  await done
  done = saved()
  await notes.fill(text)
  await notes.blur()
  await done

  await page.reload()
  await expect(page.getByLabel('Workout type')).toHaveValue(newType)
  await expect(page.getByLabel('Notes')).toHaveValue(text)
})
