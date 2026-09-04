import { chromium } from 'playwright-core';
import { mkdir, readFile, stat, writeFile } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';

// Start the local Python server and build the frontend before this check.
// Set MICROSCOPY_CHROME_PATH to an installed Chrome/Chromium executable.
const baseURL=process.env.MICROSCOPY_URL || 'http://127.0.0.1:8765';
const output=path.resolve('test-artifacts',`h100-${Date.now()}`);
await mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true,
  ...(process.env.MICROSCOPY_CHROME_PATH?{executablePath:process.env.MICROSCOPY_CHROME_PATH}:{}),
  args:['--enable-unsafe-swiftshader'],
});
const context=await browser.newContext({acceptDownloads:true,viewport:{width:1600,height:1150}});
const page=await context.newPage(),errors=[];
page.on('pageerror',error=>errors.push(error.message));
const responseFor=route=>page.waitForResponse(response=>response.url().endsWith(route),{timeout:60000});
const complete=()=>page.locator('#acquisition-status').filter({hasText:'Acquisition complete'}).waitFor({timeout:60000});
const download=async(selector,filename)=>{
  const event=page.waitForEvent('download',{timeout:20000});
  await page.locator(selector).click();const file=await event,destination=path.join(output,filename);
  await file.saveAs(destination);assert.equal(await file.failure(),null);assert((await stat(destination)).size>0);
  return JSON.parse(await readFile(destination,'utf8'));
};
try {
  const response=await context.request.get(`${baseURL}/api/examples`);assert(response.ok());
  const examples=await response.json(),example=examples.find(item=>item.id==='nvidia-h100-sxm');
  assert(example,'H100 must appear in the public specimen catalog.');
  const sourceTwin=example.twin,reference=sourceTwin.reference;
  assert.equal(sourceTwin.objects.filter(item=>/^HBM3/.test(item.display_label || '')).length,6);
  assert(reference.sources.length>0);assert(reference.published_facts.length>0);assert(reference.assumptions.length>0);

  const initialResponse=responseFor('/api/simulate');
  await page.goto(`${baseURL}/?specimen=nvidia-h100-sxm`,{waitUntil:'networkidle'});
  const initial=await initialResponse;assert.equal(initial.status(),200);
  await complete();
  const initialResult=await initial.json();
  assert.equal(await page.locator('#example-picker').inputValue(),'nvidia-h100-sxm');
  assert.equal(await page.locator('#model-title').innerText(),sourceTwin.name);
  assert.deepEqual(initial.request().postDataJSON().settings,sourceTwin.recommended_settings);
  const expectedLabels=sourceTwin.objects.filter(item=>item.display_label && item.role!=='defect' && item.id!=='interposer').map(item=>item.display_label);
  assert.deepEqual(await page.locator('.scene-part-label').allTextContents(),expectedLabels);
  for(const label of await page.locator('.scene-part-label').all())assert(await label.isVisible());
  const labelBoxes=await page.locator('.scene-part-label').evaluateAll(labels=>labels.map(label=>{const box=label.getBoundingClientRect();return {left:box.left,right:box.right,top:box.top,bottom:box.bottom};}));
  for(let i=0;i<labelBoxes.length;i++)for(let j=i+1;j<labelBoxes.length;j++) {
    const a=labelBoxes[i],b=labelBoxes[j];
    assert(!(a.left<b.right && a.right>b.left && a.top<b.bottom && a.bottom>b.top),'Component labels must not overlap.');
  }
  await page.locator('#explode-btn').click();
  assert.deepEqual(await page.locator('.scene-part-label').allTextContents(),sourceTwin.objects.filter(item=>item.display_label).map(item=>item.display_label));
  await page.locator('#explode-btn').click();
  assert.equal(await page.locator('#sam-window').inputValue(),'0.5');
  assert(await page.locator('#specimen-reference-btn').isVisible());
  await page.locator('#specimen-reference-btn').click();
  assert(await page.locator('#specimen-reference-dialog').isVisible());
  assert.equal(await page.locator('#specimen-reference-title').innerText(),reference.product);
  assert.equal(await page.locator('#specimen-reference-summary').innerText(),reference.summary);
  assert.equal(await page.locator('#specimen-published-facts dt').count(),reference.published_facts.length);
  assert.deepEqual(await page.locator('#specimen-geometry-assumptions li').allTextContents(),reference.assumptions);
  const links=await page.locator('#specimen-sources a').evaluateAll(items=>items.map(item=>item.href));
  assert.deepEqual(links,reference.sources.map(source=>new URL(source.url).href));
  await page.keyboard.press('Escape');assert(!(await page.locator('#specimen-reference-dialog').isVisible()));
  await page.screenshot({path:path.join(output,'h100-workbench.png'),fullPage:true});

  const exportedTwin=await download('#export-twin','h100-twin.json');
  assert.deepEqual(exportedTwin,sourceTwin,'Twin export must preserve all reference and preset fields.');
  // Pick physical x/y positions beyond the previous 30 mm schema limit.
  const map=page.locator('#sam-map'),bounds=await map.boundingBox();
  const plot=await map.evaluate(canvas=>canvas._plot);
  const probeResponse=responseFor('/api/probe');
  await map.click({position:{x:plot.left+plot.w*.78,y:plot.top+plot.h*.68}});
  const probe=await probeResponse;assert.equal(probe.status(),200);
  const probeSettings=probe.request().postDataJSON().settings;
  assert(probeSettings.probe_x_mm>30 && probeSettings.probe_y_mm>30);
  await page.waitForFunction(()=>document.querySelector('#probe-state').textContent.startsWith('Waveform'));
  const acquired=await download('#export-results','h100-acquisition.json');
  assert.deepEqual(acquired.twin.reference,reference);
  assert.deepEqual(acquired.twin.recommended_settings,sourceTwin.recommended_settings);
  assert.deepEqual(acquired.settings,sourceTwin.recommended_settings);
  assert(acquired.input_sha256);assert(acquired.run_id);
  assert.deepEqual(acquired.metadata.warnings,initialResult.metadata.warnings);
  assert.deepEqual(acquired.export_metadata.display_windows.sam,[0,.5]);
  assert.equal(acquired.probe_inspection.parent_run_id,acquired.run_id);
  assert(acquired.probe_inspection.ascan.probe_mm.every(value=>value>30));
  assert(acquired.probe_inspection.ascan.time_us.length>0);
  assert(acquired.xray.image.length===acquired.settings.resolution);
  assert(acquired.sam.image.length===acquired.settings.resolution);
  assert(bounds.width>0);

  const imported=structuredClone(exportedTwin);
  imported.name='Imported H100 preset verification';
  imported.reference.summary='<b>Imported geometry assumptions remain plain text.</b>';
  imported.recommended_settings={...imported.recommended_settings,resolution:64,energy_kev:94.25,photons:12345,
    frequency_mhz:35.25,gate_start_us:.19,gate_end_us:.27,focus_mm:.823,probe_x_mm:45,probe_y_mm:40,
    seed:2026,noise:false,include_defects:false};
  const importResponse=responseFor('/api/simulate');
  await page.locator('#twin-file').setInputFiles({name:'h100-import.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(imported))});
  const simulation=await importResponse;assert.equal(simulation.status(),200);await complete();
  assert.equal(await page.locator('#model-title').innerText(),imported.name);
  assert.deepEqual(simulation.request().postDataJSON().settings,imported.recommended_settings);
  assert.equal(await page.locator('#photons').inputValue(),'12345');
  assert.equal(await page.locator('#energy').inputValue(),'94.25');
  assert.equal(await page.locator('#frequency').inputValue(),'35.25');
  assert.equal(await page.locator('#gate-start').inputValue(),'0.19');
  assert.equal(await page.locator('#focus').inputValue(),'0.823');
  assert.equal(await page.locator('#noise').isChecked(),false);
  assert.equal(await page.locator('#include-defects').isChecked(),false);
  assert.equal(new URL(page.url()).searchParams.get('specimen'),null);
  await page.locator('#specimen-reference-btn').click();
  assert.equal(await page.locator('#specimen-reference-summary').innerText(),imported.reference.summary);
  assert.equal(await page.locator('#specimen-reference-summary b').count(),0,'Imported descriptions must never become HTML.');
  await page.keyboard.press('Escape');
  assert.deepEqual((await download('#export-results','imported-h100-acquisition.json')).twin.reference,imported.reference);

  const fallbackResponse=responseFor('/api/simulate');
  await page.locator('#example-picker').selectOption(examples[0].id);
  assert.equal((await fallbackResponse).status(),200);await complete();
  assert.equal(new URL(page.url()).searchParams.get('specimen'),examples[0].id);
  assert(!(await page.locator('#specimen-reference-btn').isVisible()));
  assert.equal(await page.locator('.scene-part-label').count(),0);
  assert.equal(await page.locator('#noise').isChecked(),true);
  assert.equal(await page.locator('#include-defects').isChecked(),true);
  assert.equal(await page.locator('#sam-window').inputValue(),'0.2');
  const fallback=await download('#export-results','default-acquisition.json');
  assert.equal(fallback.settings.probe_x_mm,3.1);
  assert.deepEqual(errors,[]);
  const report={status:'passed',artifacts:output,checks:['H100 URL and catalog selection','published facts and source links',
    `${expectedLabels.length} component labels without overlap`,'native twin and acquisition exports','unaltered reference metadata and warnings',
    'probe inspection beyond 30 mm','imported generic acquisition preset','imported text safety','original BGA defaults restored','no uncaught browser errors']};
  await writeFile(path.join(output,'verification.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify(report));
} finally { await context.close();await browser.close(); }
