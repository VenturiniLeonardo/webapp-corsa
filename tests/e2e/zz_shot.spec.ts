import { test } from '@playwright/test'
test('shot', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 900 })
  await page.goto('/')
  await page.getByText('AI analysis').waitFor()
  await page.screenshot({ path: 'C:/Users/LEONAR~1/AppData/Local/Temp/claude/C--Users-Leonardo-Venturini-Desktop-webapp-corsa/bbd1d7b9-c096-45d5-a1b2-3e27bcc72445/scratchpad/dash.png' })
  await page.goto('/activities')
  await page.locator('tbody tr a').first().click()
  await page.getByText('AI analysis').scrollIntoViewIfNeeded()
  await page.screenshot({ path: 'C:/Users/LEONAR~1/AppData/Local/Temp/claude/C--Users-Leonardo-Venturini-Desktop-webapp-corsa/bbd1d7b9-c096-45d5-a1b2-3e27bcc72445/scratchpad/detail.png' })
})
