import { chromium } from 'playwright-core';
import { mkdir, readFile, stat } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';

// Set MICROSCOPY_CHROME_PATH when using a locally installed Chromium executable.
// Run the local Python server first, then: npm run verify:exports
const output = path.resolve('test-artifacts', `exports-${Date.now()}`);
await mkdir(output, { recursive:true });
const browser = await chromium.launch({
  headless:true,
  ...(process.env.MICROSCOPY_CHROME_PATH ? { executablePath:process.env.MICROSCOPY_CHROME_PATH } : {}),
  args:['--enable-unsafe-swiftshader'],
});
const context = await browser.newContext({ acceptDownloads:true, viewport:{width:1440,height:1000} });
const page = await context.newPage();
const pageErrors = [];
page.on('pageerror', error => pageErrors.push(error.message));
try {
  await page.goto(process.env.MICROSCOPY_URL || 'http://127.0.0.1:8765', {waitUntil:'networkidle'});
  await page.locator('#acquisition-status').filter({hasText:'Acquisition complete'}).waitFor({timeout:30000});
  assert.equal(await page.locator('h1').innerText(), 'Virtual microscopy');
  assert.deepEqual(pageErrors, [], 'The workbench must have no uncaught browser errors.');
  const download = async (selector,filename) => {
    const event = page.waitForEvent('download', {timeout:15000});
    await page.locator(selector).click();
    const file = await event;
    const destination = path.join(output,filename);
    await file.saveAs(destination);
    assert.equal(await file.failure(),null,`Download ${filename} must complete.`);
    const bytes = (await stat(destination)).size;
    assert(bytes > 0);
    console.log(JSON.stringify({download:filename,suggested_name:file.suggestedFilename(),bytes}));
    return JSON.parse(await readFile(destination,'utf8'));
  };
  const twin = await download('#export-twin','twin.json');
  assert.equal(twin.schema_version,1);assert(twin.objects.length>0);assert.equal(twin.size_mm.length,3);
  // Inspect another point before exporting, verifying distinct acquisition/probe provenance.
  const previousProbe = await page.locator('#probe-coordinate').innerText();
  await page.locator('#sam-map').focus();await page.keyboard.press('ArrowRight');
  await page.waitForFunction(previous => document.querySelector('#probe-coordinate').textContent !== previous,previousProbe);
  const result = await download('#export-results','acquisition.json');
  assert.equal(result.twin.name,twin.name);
  assert(result.xray.image.length>0);assert(result.sam.image.length>0);
  assert(result.ascan.time_us.length>0);assert(result.bscan.image.length>0);
  assert(result.run_id);assert(result.input_sha256);assert(result.settings);
  assert(result.probe_inspection.ascan.time_us.length>0);
  assert.equal(result.probe_inspection.parent_run_id,result.run_id);
  assert.notDeepEqual(result.ascan.probe_mm,result.probe_inspection.ascan.probe_mm);
  assert.deepEqual(result.export_metadata.display_windows.sam,[0,.2]);
  assert.equal(result.settings.probe_x_mm,3.1,'An inspection must preserve the original acquisition settings.');
  assert.deepEqual(pageErrors, []);
  console.log(JSON.stringify({status:'passed',artifacts:output,checks:['page load','no uncaught browser errors','native twin download','native acquisition download','JSON data and provenance','original acquisition preserved after probe inspection']}));
} finally {
  await context.close();await browser.close();
}
