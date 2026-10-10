import { expect, test } from '@playwright/test'

test('HR chart shows pace-adjusted drift on distance and time axes', async ({ page }, testInfo) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  const time = Array.from({ length: 1501 }, (_, i) => i)
  const speed = time.map((t) => 1000 / (t < 900 ? 420 : 300))
  let distance = 0
  await page.route('**/api/activities/*/streams', (route) => route.fulfill({
    json: { channels: {
      time, speed,
      hr: time.map((t) => t < 900 ? 140 : 160),
      distance: time.map((_, i) => { if (i) distance += speed[i - 1]; return distance }),
    } },
  }))
  await page.goto('/activities')
  await page.locator('tbody tr').first().click()
  const explanation = page.getByText('Linea verde: deriva', { exact: false })
  await expect(explanation).toBeVisible()
  const hr = explanation.locator('..').locator('[_echarts_instance_]')
  await expect(hr).toBeVisible()
  const hover = async () => {
    await hr.scrollIntoViewIfNeeded()
    const box = (await hr.boundingBox())!
    await page.mouse.move(box.x + box.width * 0.8, box.y + box.height / 2)
    await page.mouse.move(box.x + box.width * 0.85, box.y + box.height / 2, { steps: 5 })
  }
  await hover()
  await expect(hr).toContainText('Deriva (passo corretto) -22.5%')
  await expect(hr).toContainText('FC 160')
  await expect(hr).toContainText('km')
  await page.getByRole('button', { name: /^(Tempo|Time)$/ }).click()
  await hover()
  await expect(hr).toContainText('Deriva (passo corretto) -22.5%')
  await page.setViewportSize({ width: 375, height: 812 })
  await expect(hr).toBeVisible()
  await expect.poll(async () => {
    const box = (await hr.boundingBox())!
    return box.x + box.width
  }).toBeLessThanOrEqual(375)
  await page.mouse.move(0, 0)
  await explanation.locator('..').screenshot({ path: testInfo.outputPath('hr-drift-mobile.png') })
  expect(errors).toEqual([])
})
