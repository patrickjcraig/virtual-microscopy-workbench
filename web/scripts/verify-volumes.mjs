import { chromium } from 'playwright-core';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';

const baseURL=process.env.MICROSCOPY_URL || 'http://127.0.0.1:8765';
const output=path.resolve('test-artifacts',`volumes-${Date.now()}`);await mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true,...(process.env.MICROSCOPY_CHROME_PATH?{executablePath:process.env.MICROSCOPY_CHROME_PATH}:{}),args:['--enable-unsafe-swiftshader']});
const context=await browser.newContext({acceptDownloads:true,viewport:{width:1600,height:1200}}),page=await context.newPage(),errors=[],submissions=[];
page.on('pageerror',error=>errors.push(error.message));page.on('request',request=>{if(request.method()==='POST' && request.url().endsWith('/api/v2/jobs'))submissions.push(request.postDataJSON());});
const ready=()=>page.waitForFunction(()=>document.querySelector('#vol-viewer')&&!document.querySelector('#vol-viewer').hidden&&Number(document.querySelector('#vol-xy').dataset.rows)>0&&document.querySelector('#vol-view-note').textContent.startsWith('Reading saved data only'),{},{timeout:90000});
const responseTo=(suffix,method='GET')=>page.waitForResponse(response=>response.request().method()===method&&response.url().includes(suffix),{timeout:90000});
const setNumber=async(id,value)=>page.locator(`#vol-${id}`).fill(String(value));
const slider=async(id,value)=>{await page.locator(`#vol-${id}`).evaluate((element,number)=>{element.value=String(number);element.dispatchEvent(new Event('input',{bubbles:true}));},value);};
const noOverflow=async()=>page.evaluate(()=>{const dialog=document.querySelector('#vol-dialog'),layout=document.querySelector('.vol-layout');return {dialog:dialog.scrollWidth<=dialog.clientWidth+2,layout:layout.scrollWidth<=layout.clientWidth+2,body:document.documentElement.scrollWidth<=innerWidth+2};});
let datasetId,firstView,createdJob;
try{
  await page.goto(`${baseURL}/?specimen=nvidia-h100-sxm`,{waitUntil:'networkidle'});await page.locator('#acquisition-status').filter({hasText:'Acquisition complete'}).waitFor({timeout:60000});
  await page.locator('#roi-site').selectOption('hbm-6');await page.locator('#roi-stack').click();await page.locator('#volumes-btn').click();assert(await page.locator('#vol-dialog').isVisible());assert.match(await page.locator('#vol-snapshot').innerText(),/Selected ROI/);
  await setNumber('scan_nx',32);await setNumber('scan_ny',24);await page.locator('#vol-depth_samples').selectOption('512');await setNumber('record_duration_us',1);await setNumber('sample_rate_mhz',400);await page.locator('#vol-estimate-btn').click();await page.waitForFunction(()=>!document.querySelector('#vol-start').disabled);assert.match(await page.locator('#vol-estimate').innerText(),/24 × 32 × 40[01]/);
  const submitted=responseTo('/api/v2/jobs','POST');await page.locator('#vol-start').click();const submittedResponse=await submitted;assert.equal(submittedResponse.status(),202);createdJob=await submittedResponse.json();datasetId=createdJob.dataset_id;
  assert.deepEqual(submissions.at(-1).acquisition.roi_mm,[45.5,35.5,53.5,44.5]);assert.equal(submissions.at(-1).acquisition.scan_nx,32);assert.equal(submissions.at(-1).acquisition.scan_ny,24);
  await page.locator(`.vol-dataset[data-dataset-id="${datasetId}"]:not(:disabled)`).waitFor({timeout:90000});await page.locator(`.vol-dataset[data-dataset-id="${datasetId}"]`).click();await ready();assert.equal(await page.locator('#vol-x').getAttribute('max'),'31');assert.equal(await page.locator('#vol-y').getAttribute('max'),'23');
  let response=responseTo(`/api/v2/datasets/${datasetId}/view?`);await page.evaluate(()=>{for(const [id,value] of [['x',7],['y',15],['time',120]]){const element=document.querySelector(`#vol-${id}`);element.value=value;element.dispatchEvent(new Event('input',{bubbles:true}));}});let responseData=await (await response).json();await ready();
  // The first request may be superseded by another slider event; inspect the
  // authoritative displayed cursor and read the same physical sample directly.
  await page.waitForFunction(()=>document.querySelector('#vol-x').value==='7'&&document.querySelector('#vol-y').value==='15'&&document.querySelector('#vol-time').value==='120');
  firstView=await (await context.request.get(`${baseURL}/api/v2/datasets/${datasetId}/view?x_index=7&y_index=15&time_index=120&gate_start_us=0&gate_end_us=.25&gate_mode=peak_envelope`)).json();
  assert.match(await page.locator('#vol-x-value').innerText(),new RegExp(String(firstView.cursor.x_mm)));assert.match(await page.locator('#vol-y-value').innerText(),new RegExp(String(firstView.cursor.y_mm)));assert.equal(firstView.xy.image.length,24);assert.equal(firstView.xt.image[0].length,32);assert.equal(firstView.yt.image[0].length,24);
  const countBeforeGate=submissions.length;
  await page.locator('#vol-gate-start').fill('.34');await page.locator('#vol-gate-end').fill('.6');await page.locator('#vol-gate-mode').selectOption('rms_rf');response=responseTo(`/api/v2/datasets/${datasetId}/view?`);await page.locator('#vol-apply-gate').click();responseData=await (await response).json();await ready();assert.equal(responseData.gate.mode,'rms_rf');assert.equal(responseData.gate.processing_source,'saved signed RF');assert.equal(submissions.length,countBeforeGate);
  assert(responseData.ascan.amplitude.some(value=>value<0),'Retained RF must preserve polarity.');assert(responseData.ascan.envelope.every(value=>value>=0));
  const jobsBeforeReload=(await (await context.request.get(`${baseURL}/api/v2/jobs`)).json()).jobs.length;
  await page.locator('#vol-acquire-panel').evaluate(element=>element.open=false);await page.screenshot({path:path.join(output,'saved-sam-volume-desktop.png')});assert.deepEqual(await noOverflow(),{dialog:true,layout:true,body:true});
  const waiting=page.waitForEvent('download');await page.locator('#vol-export').click();const download=await waiting,archive=path.join(output,'sam-volume.zip');await download.saveAs(archive);assert.equal(await download.failure(),null);assert.equal((await readFile(archive)).subarray(0,2).toString(),'PK');
  await page.reload({waitUntil:'networkidle'});await page.locator('#acquisition-status').filter({hasText:'Acquisition complete'}).waitFor({timeout:60000});await page.locator('#volumes-btn').click();await ready();assert.equal(await page.locator('#vol-export').getAttribute('href'),`/api/v2/datasets/${datasetId}/export`);assert.equal((await (await context.request.get(`${baseURL}/api/v2/jobs`)).json()).jobs.length,jobsBeforeReload,'Reopening saved data must not acquire again.');
  await page.setViewportSize({width:390,height:844});await page.locator('#vol-acquire-panel').evaluate(element=>element.open=false);await page.locator('#vol-viewer').scrollIntoViewIfNeeded();await ready();await page.screenshot({path:path.join(output,'saved-sam-volume-mobile.png')});assert.deepEqual(await noOverflow(),{dialog:true,layout:true,body:true});
  assert.deepEqual(errors,[]);await writeFile(path.join(output,'verification.json'),JSON.stringify({passed:true,datasetId,createdJob,shape:[24,32,firstView.ascan.time_us.length],checks:['real acquisition and resource estimate','ROI frozen in request','saved RF polarity','XY/X-time/Y-time axes and physical cursor','post hoc RMS gate without acquisition','Zarr archive download','reload persistence','desktop and mobile layout'],errors},null,2));console.log(`Saved SAM volume verification passed: ${output}`);
}catch(error){await page.screenshot({path:path.join(output,'failure.png'),fullPage:true}).catch(()=>{});await writeFile(path.join(output,'failure.json'),JSON.stringify({error:error.stack,datasetId,errors},null,2));throw error;}
finally{await browser.close();}
