import { chromium } from 'playwright-core';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';

const baseURL = process.env.MICROSCOPY_URL || 'http://127.0.0.1:8765';
const output = path.resolve('test-artifacts', `reconstruction-jobs-${Date.now()}`);
await mkdir(output, { recursive: true });
const browser = await chromium.launch({
  headless: true,
  ...(process.env.MICROSCOPY_CHROME_PATH ? { executablePath: process.env.MICROSCOPY_CHROME_PATH } : {}),
  args: ['--enable-unsafe-swiftshader'],
});
const page = await browser.newPage({ viewport: { width: 1600, height: 1350 } });
const errors = [];
page.on('pageerror', error => errors.push(error.message));
let sourceId = process.env.MICROSCOPY_CT_SOURCE_ID, datasetId;
const get = async route => {
  const response = await page.request.get(`${baseURL}${route}`);
  assert(response.ok(), await response.text());
  return response.json();
};
const responseFor = (suffix, method = 'POST') => page.waitForResponse(
  response => response.url().includes(suffix) && response.request().method() === method,
  { timeout: 120000 },
);
async function pollJob(id, predicate) {
  const deadline = Date.now() + 180000;
  do {
    const job = await get(`/api/v2/jobs/${id}`);
    assert.notEqual(job.status, 'failed', job.error);
    if (predicate(job)) return job;
    await new Promise(resolve => setTimeout(resolve, 250));
  } while (Date.now() < deadline);
  throw new Error(`Job ${id} did not reach the requested checkpoint.`);
}
try {
  if (!sourceId) {
    const twin = { name: 'Synthetic CT resume fixture', size_mm: [4, 3, 2], objects: [
      { id: 'silicon', name: 'Silicon', shape: 'box', material: 'silicon', center_mm: [2, 1.5, 1], size_mm: [3, 2, 1] },
    ] };
    const sourceResponse = await page.request.post(`${baseURL}/api/v2/jobs`, { data: {
      kind: 'xray_projection_volume', twin, acquisition: {
        geometry_nx: 48, geometry_ny: 32, geometry_nz: 64,
        detector_cols: 96, detector_rows: 64, views: 60, noise: false, detector_fwhm_mm: 0,
      },
    } });
    assert.equal(sourceResponse.status(), 202, await sourceResponse.text());
    sourceId = (await sourceResponse.json()).dataset_id;
    await pollJob(sourceId, job => job.status === 'completed');
  }
  const sourceBefore = await get(`/api/v2/datasets/${sourceId}`);
  await page.goto(`${baseURL}/?specimen=nvidia-h100-sxm`, { waitUntil: 'networkidle' });
  await page.waitForFunction(() => !document.querySelector('#example-picker')?.disabled, {}, { timeout: 60000 });
  await page.locator('#reconstruction-btn').click();
  await page.locator(`#ct-source option[value="${sourceId}"]`).waitFor({ state: 'attached' });
  await page.locator('#ct-source').selectOption(sourceId);
  await page.waitForFunction(() => !document.querySelector('#ct-start').disabled);
  for (const [axis, count] of [['nx', 128], ['ny', 64], ['nz', 96]]) {
    await page.locator(`#ct-${axis}`).fill(String(count));
  }
  await page.locator('#ct-estimate-btn').click();
  await page.waitForFunction(() => !document.querySelector('#ct-start').disabled, {}, { timeout: 60000 });
  const submitted = responseFor('/api/v2/jobs');
  await page.locator('#ct-start').click();
  const submission = await submitted;
  assert.equal(submission.status(), 202, await submission.text());
  datasetId = (await submission.json()).dataset_id;
  const row = page.locator(`.ct-job[data-job-id="${datasetId}"]`);
  await pollJob(datasetId, job => job.completed_units > 0);
  const cancelledResponse = responseFor(`/api/v2/jobs/${datasetId}/cancel`);
  await row.locator('[data-action="cancel"]').click();
  assert.equal((await cancelledResponse).status(), 200);
  const cancelled = await pollJob(datasetId, job => job.status === 'cancelled');
  assert(cancelled.completed_units > 0 && cancelled.completed_units < cancelled.total_units);
  const before = await get(`/api/v2/datasets/${datasetId}`);
  assert.equal((await page.request.get(`${baseURL}/api/v2/datasets/${datasetId}/reconstruction-view`)).status(), 409);
  assert(await page.locator(`.ct-dataset[data-dataset-id="${datasetId}"]`).isDisabled());
  await row.locator('[data-action="resume"]').waitFor({ timeout: 30000 });
  const resumedResponse = responseFor(`/api/v2/jobs/${datasetId}/resume`);
  await row.locator('[data-action="resume"]').click();
  assert.equal((await (await resumedResponse).json()).dataset_id, datasetId);
  await pollJob(datasetId, job => job.status === 'completed');
  const after = await get(`/api/v2/datasets/${datasetId}`);
  assert.deepEqual(after.request, before.request);
  assert.equal(after.input_sha256, before.input_sha256);
  for (const [key, value] of Object.entries(before.completed_chunks)) {
    assert.deepEqual(after.completed_chunks[key], value, `Committed slice ${key} changed after resume.`);
  }
  assert.deepEqual(await get(`/api/v2/datasets/${sourceId}`), sourceBefore);
  await page.waitForFunction(id => document.querySelector(`.ct-job[data-job-id="${id}"] strong`)?.textContent.startsWith('completed'), datasetId, { timeout: 30000 });
  const screenshotId = process.env.MICROSCOPY_CT_DEMO_ID || datasetId;
  await page.locator(`.ct-dataset[data-dataset-id="${screenshotId}"]:not(:disabled)`).click({ timeout: 30000 });
  await page.waitForFunction(id => document.querySelector('#ct-export')?.getAttribute('href') === `/api/v2/datasets/${id}/export`
    && document.querySelector('#ct-view-note')?.textContent.startsWith('Reading saved data only'), screenshotId, { timeout: 60000 });
  // Native controls keep screenshots tied to an actual saved reconstruction.
  await page.locator('#ct-z').focus();
  await page.keyboard.press('Home');
  for (let index = 0; index < 20; index++) await page.keyboard.press('ArrowRight');
  await page.locator('#ct-window-min').fill('-.1');
  await page.locator('#ct-window-max').fill('.6');
  await page.waitForFunction(() => document.querySelector('#ct-z').value === '20'
    && document.querySelector('#ct-view-note')?.textContent.startsWith('Reading saved data only'));
  await page.locator('.ct-inspector').evaluate(element => element.scrollTop = 0);
  await page.locator('.ct-acquisition').evaluate(element => element.scrollTop = 0);
  await page.screenshot({ path: path.join(output, 'ct-reconstruction-workspace.png') });
  assert.deepEqual(errors, []);
  await writeFile(path.join(output, 'verification.json'), JSON.stringify({
    passed: true, sourceId, datasetId, cancelled_slices: cancelled.completed_units,
    total_slices: cancelled.total_units, committed_checksums_preserved: true,
    source_manifest_unchanged: true, screenshotDatasetId: screenshotId, errors,
  }, null, 2));
  console.log(`CT cancellation/resume verification passed: ${output}`);
} catch (error) {
  await page.screenshot({ path: path.join(output, 'failure.png') }).catch(() => {});
  await writeFile(path.join(output, 'failure.json'), JSON.stringify({ error: error.stack, sourceId, datasetId, errors }, null, 2));
  throw error;
} finally {
  await browser.close();
}
