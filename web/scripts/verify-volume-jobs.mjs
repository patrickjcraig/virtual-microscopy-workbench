import { chromium } from 'playwright-core';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';

const baseURL=process.env.MICROSCOPY_URL || 'http://127.0.0.1:8765',output=path.resolve('test-artifacts',`volume-jobs-${Date.now()}`);await mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true,...(process.env.MICROSCOPY_CHROME_PATH?{executablePath:process.env.MICROSCOPY_CHROME_PATH}:{}),args:['--enable-unsafe-swiftshader']}),page=await browser.newPage({viewport:{width:1600,height:1500}}),errors=[];
page.on('pageerror',error=>errors.push(error.message));let id;
const responseFor=(path,method)=>page.waitForResponse(response=>response.url().endsWith(path)&&response.request().method()===method,{timeout:90000});
try{
  await page.goto(`${baseURL}/?specimen=nvidia-h100-sxm`,{waitUntil:'networkidle'});await page.waitForFunction(()=>document.querySelector('#example-picker')?.value==='nvidia-h100-sxm'&&!document.querySelector('#example-picker').disabled,{},{timeout:60000});
  await page.locator('#roi-site').selectOption('hbm-6');await page.locator('#roi-stack').click();await page.locator('#volumes-btn').click();
  await page.locator('#vol-scan_nx').fill('64');await page.locator('#vol-scan_ny').fill('64');await page.locator('#vol-depth_samples').selectOption('512');await page.locator('#vol-record_duration_us').fill('2');await page.locator('#vol-estimate-btn').click();await page.waitForFunction(()=>!document.querySelector('#vol-start').disabled);
  const submit=responseFor('/api/v2/jobs','POST');await page.locator('#vol-start').click();const created=await (await submit).json();id=created.id;
  const row=page.locator(`.vol-job[data-job-id="${id}"]`);await row.locator('[data-action="cancel"]').waitFor({timeout:20000});
  const cancel=responseFor(`/api/v2/jobs/${id}/cancel`,'POST');await row.locator('[data-action="cancel"]').click();assert.equal((await cancel).status(),200);
  await row.locator('[data-action="resume"]').waitFor({timeout:90000});const interrupted=await (await page.request.get(`${baseURL}/api/v2/jobs/${id}`)).json();assert.equal(interrupted.status,'cancelled');assert(interrupted.completed_rows<interrupted.total_rows);
  const incomplete=await page.request.get(`${baseURL}/api/v2/datasets/${id}/view`);assert.equal(incomplete.status(),409);assert(await page.locator(`.vol-dataset[data-dataset-id="${id}"]`).isDisabled());
  const snapshotBefore=await (await page.request.get(`${baseURL}/api/v2/datasets/${id}`)).json();const resume=responseFor(`/api/v2/jobs/${id}/resume`,'POST');await row.locator('[data-action="resume"]').click();const resumed=await (await resume).json();assert.equal(resumed.dataset_id,id);
  await page.locator(`.vol-dataset[data-dataset-id="${id}"]:not(:disabled)`).waitFor({timeout:120000});await page.locator(`.vol-dataset[data-dataset-id="${id}"]`).click();await page.waitForFunction(()=>document.querySelector('#vol-view-note').textContent.startsWith('Reading saved data only')&&Number(document.querySelector('#vol-xy').dataset.rows)===64,{},{timeout:90000});
  const snapshotAfter=await (await page.request.get(`${baseURL}/api/v2/datasets/${id}`)).json();assert.equal(snapshotAfter.complete,true);assert.deepEqual(snapshotAfter.request,snapshotBefore.request);
  await page.locator('#vol-gate-start').fill('.2');await page.locator('#vol-gate-end').fill('.6');const gate=page.waitForResponse(response=>response.url().includes(`/api/v2/datasets/${id}/view?`));await page.locator('#vol-apply-gate').click();assert.equal((await gate).status(),200);await page.waitForFunction(()=>document.querySelector('#vol-view-note').textContent.startsWith('Reading saved data only'));
  await page.locator('#vol-window').selectOption('.05');await page.locator('#vol-time').evaluate(element=>{element.value=120;element.dispatchEvent(new Event('input',{bubbles:true}));});await page.waitForFunction(()=>document.querySelector('#vol-time-value').textContent==='0.3 µs');await page.locator('.vol-inspector').evaluate(element=>element.scrollTop=0);
  await page.screenshot({path:path.join(output,'sam-volume-workspace.png')});assert.deepEqual(errors,[]);
  await writeFile(path.join(output,'verification.json'),JSON.stringify({passed:true,datasetId:id,cancelled_rows:interrupted.completed_rows,total_rows:interrupted.total_rows,shape:snapshotAfter.shape,checks:['native cancel button','incomplete view rejected and UI disabled','native resume button','same dataset and immutable request after resume','complete saved view and accurate peak gate'],errors},null,2));console.log(`Volume cancellation/resume verification passed: ${output}`);
}catch(error){await page.screenshot({path:path.join(output,'failure.png')}).catch(()=>{});await writeFile(path.join(output,'failure.json'),JSON.stringify({error:error.stack,datasetId:id,errors},null,2));throw error;}finally{await browser.close();}
