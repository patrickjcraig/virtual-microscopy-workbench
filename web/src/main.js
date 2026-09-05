import './style.css';
import { TwinViewer } from './scene.js';
import { mapPlot, ascanPlot, bscanPlot, pointFromEvent } from './plots.js';
import { HBMEditor } from './hbm.js';
import { VOXEL_PATHS,CONTINUOUS_PATHS,pathModelLabel,pathModelOptions } from './path-model.js';
import { VolumeWorkspace } from './volumes.js';

const icons = {
  cube:'<path d="m12 2 9 5v10l-9 5-9-5V7l9-5Z"/><path d="m3 7 9 5 9-5M12 12v10M7.5 4.5l9 5v5"/>',
  xray:'<path d="M12 2v5M12 17v5M2 12h5M17 12h5M5 5l3 3M16 16l3 3M5 19l3-3M16 8l3-3"/><circle cx="12" cy="12" r="2.5"/>',
  acoustic:'<path d="M3 12h3l3-8 6 16 3-8h3"/>',
  layers:'<path d="m3 7 9-5 9 5-9 5-9-5ZM3 12l9 5 9-5M3 17l9 5 9-5"/>',
  upload:'<path d="M12 16V3m-5 5 5-5 5 5M4 15v5h16v-5"/>',
  download:'<path d="M12 3v13m-5-5 5 5 5-5M4 16v5h16v-5"/>',
  reset:'<path d="M3 10a9 9 0 1 1 1 7M3 3v7h7"/>',
  info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7v1"/>',
  play:'<path d="m9 5 10 7-10 7V5Z"/>',
  close:'<path d="m6 6 12 12M6 18 18 6"/>',
  chart:'<path d="M3 3v18h18M6 14l4-5 4 7 6-11"/>',
  sliders:'<path d="M4 7h7m5 0h4M4 17h12m3 0h1"/><circle cx="13" cy="7" r="2"/><circle cx="17" cy="17" r="2"/>',
  target:'<circle cx="12" cy="12" r="6"/><path d="M12 2v5m0 10v5M2 12h5m10 0h5"/>',
};
const icon = name => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || icons.info}</svg>`;
const $ = selector => document.querySelector(selector);
const app = $('#app');
app.innerHTML = `
  <header class="topbar">
    <div class="brand"><div class="brand-mark">${icon('cube')}</div><div><h1>Virtual microscopy</h1><p>Microelectronics simulation workbench</p></div></div>
    <div class="top-actions"><span class="mode-label"><i></i>Synthetic forward model</span><button id="volumes-btn" title="Saved acoustic RF volumes">${icon('layers')}Saved volumes</button><button id="causal-volumes-btn" title="Saved causal multilayer independent-column volumes">${icon('acoustic')}Causal volumes</button><button id="causal-comparisons-btn" title="Compare saved causal responses and residual bounds">${icon('sliders')}Causal comparisons</button><button id="recipes-btn">${icon('sliders')}Recipes & comparisons</button><button id="xray-recipes-btn">${icon('sliders')}X-ray recipes</button><button id="xray-volumes-btn">${icon('xray')}X-ray volumes</button><button id="reconstruction-btn">${icon('layers')}CT reconstruction</button><button id="layered-acoustics-btn">${icon('acoustic')}Layered acoustics</button><button id="sam-depth-btn">${icon('acoustic')}SAM depth</button><button id="assumptions-btn">${icon('info')}Model & assumptions</button><button id="export-results" disabled>${icon('download')}Export acquisition</button></div>
  </header>
  <main class="workspace">
    <aside class="sidebar" aria-label="Specimen and acquisition controls">
      <div class="sidebar-content">
        <section class="control-section"><h2 class="section-heading">${icon('cube')}Digital twin</h2>
          <div class="field"><label for="example-picker">Specimen</label><select id="example-picker" disabled><option>Loading specimens…</option></select></div>
          <p class="specimen-desc" id="specimen-description">Load a microelectronic package to inspect its structure across modalities.</p>
          <button class="specimen-reference" id="specimen-reference-btn" hidden>${icon('info')}<span>Reference model · geometry assumed</span></button>
          <button class="specimen-reference" id="hbm-editor-btn" hidden>${icon('layers')}<span>Edit HBM stacks & inspect layers</span></button>
          <div class="button-pair"><button class="small" id="import-twin">${icon('upload')}Import JSON</button><button class="small" id="export-twin" disabled>${icon('download')}Export twin</button></div>
          <input id="twin-file" type="file" accept=".json,application/json" class="sr-only" aria-label="Import digital twin JSON"/>
          <div class="checkbox-row"><label class="check-label"><input id="include-defects" type="checkbox" checked/>Include embedded defects</label><span id="defect-count" class="badge">—</span></div>
        </section>
        <section class="control-section"><h2 class="section-heading">${icon('xray')}X-ray source</h2>
          <div class="field"><div class="field-top"><label for="energy">Photon energy</label><output for="energy" id="energy-value">80 keV</output></div><input id="energy" type="range" min="40" max="150" step="5" value="80"/><div class="range-limits"><span>40 keV</span><span>150 keV</span></div></div>
          <div class="field"><div class="field-top"><label for="angle">Beam angle</label><output for="angle" id="angle-value">0°</output></div><input id="angle" type="range" min="-45" max="45" step="1" value="0"/><div class="range-limits"><span>−45°</span><span>+45°</span></div></div>
          <div class="field"><label for="photons">Incident photons / pixel</label><select id="photons"><option value="1000">1,000</option><option value="10000">10,000</option><option value="50000" selected>50,000</option><option value="100000">100,000</option><option value="1000000">1,000,000</option></select></div>
          <label class="check-label"><input id="noise" type="checkbox" checked/>Photon counting noise</label>
        </section>
        <section class="control-section"><h2 class="section-heading">${icon('acoustic')}Acoustic transducer</h2>
          <div class="field"><div class="field-top"><label for="frequency">Center frequency</label><output for="frequency" id="frequency-value">50 MHz</output></div><input id="frequency" type="range" min="10" max="150" step="5" value="50"/><div class="range-limits"><span>10 MHz</span><span>150 MHz</span></div></div>
          <div class="field"><div class="field-top"><label for="gate-start">Time gate</label><span class="range-limits">µs from top plane</span></div><div class="gate-inputs"><input id="gate-start" type="number" aria-label="Time gate start in microseconds" min="0" max="10" step="0.01" value="0.42"/><span>to</span><input id="gate-end" type="number" aria-label="Time gate end in microseconds" min="0.01" max="12" step="0.01" value="0.56"/></div><p class="gate-hint" id="gate-hint">Die-attach inspection gate</p></div>
          <div class="field"><div class="field-top"><label for="focus">Focal depth</label><output for="focus" id="focus-value">0.50 mm</output></div><input id="focus" type="range" min="0" max="1.9" step="0.01" value="0.5"/><div class="range-limits"><span>Top surface</span><span id="depth-max">1.90 mm</span></div></div>
        </section>
        <section class="control-section acquisition-controls"><h2 class="section-heading">${icon('sliders')}Acquisition</h2><div class="field path-choice"><label for="path-model">Material paths</label><select id="path-model" aria-describedby="path-model-note path-angle-error">${pathModelOptions}</select><p id="path-model-note" class="gate-hint"></p><p id="path-angle-error" class="path-angle-error" role="status" hidden></p></div><div class="field"><label for="resolution">Lateral sampling grid</label><select id="resolution"><option value="64">64 × 64</option><option value="128" selected>128 × 128</option><option value="192">192 × 192</option></select></div><div class="field"><label for="depth-samples">Material depth samples <span id="depth-samples-state"></span></label><select id="depth-samples"><option value="">Automatic (2 × lateral)</option><option value="128">128 planes</option><option value="256">256 planes</option><option value="512">512 planes</option><option value="1024">1,024 planes</option></select></div><div id="roi-controls" hidden><div class="field"><label for="roi-site">HBM region of interest</label><select id="roi-site"></select></div><div class="button-pair"><button id="roi-stack" class="small">Scan stack</button><button id="roi-full" class="small">Full package</button></div></div><p id="roi-summary" class="gate-hint">Full specimen · depth retained</p><p id="roi-angle-hint" class="gate-hint" hidden>ROI acquisition uses a normal X-ray beam (0°).</p></section>
      </div>
      <div class="run-area"><button class="primary" id="run-btn">${icon('play')}<span>Run acquisition</span></button><p id="run-help" aria-live="polite">Preparing the virtual instruments…</p></div>
    </aside>
    <div class="instrument" id="instrument">
      <div class="notice" id="notice" role="status">${icon('info')}<p></p><button aria-label="Dismiss notification">${icon('close')}</button></div>
      <section class="panel twin-panel">
        <div class="panel-header"><div class="panel-heading">${icon('cube')}<div><h2 id="model-title">Digital twin</h2><p class="panel-subtitle">Shared geometry for both virtual instruments</p></div></div><div class="view-toolbar"><button class="small" id="explode-btn" aria-pressed="false">${icon('layers')}Explode</button><button class="small" id="return-package" hidden>Return to package</button><button class="small" id="reset-view">${icon('reset')}Reset view</button></div></div>
        <div class="twin-body"><div class="scene-container" id="twin-view"><div class="scene-overline"><span>3D specimen</span><span id="scene-mode">Epoxy translucent for inspection</span></div><span class="scene-hint">Drag to orbit · scroll to zoom · R to reset</span><span class="axis-label">x / y in mm · depth from top</span></div>
          <div class="twin-info" tabindex="0" role="region" aria-label="Specimen dimensions and material key"><h3>Specimen dimensions</h3><div class="dimension" id="dimensions">— <span>mm</span></div><div class="twin-details" id="primitive-count">Primitive digital twin</div><h3>Material key</h3><div class="material-key" id="material-key"></div><p class="defect-note" id="defect-note">Amber highlights identify modeled defects in the 3D view.</p></div>
        </div>
      </section>
      <div class="result-grid">
        <section class="panel image-panel"><div class="panel-header"><div class="panel-heading">${icon('xray')}<div><h2>X-ray transmission</h2><p class="panel-subtitle" id="xray-settings">Projection through the shared digital twin</p></div></div><div class="header-meta"><span class="badge">I / I₀</span></div></div><div class="map-wrap"><canvas id="xray-map" class="map-canvas" tabindex="0" role="img" aria-label="X-ray transmission map. Click to inspect a point; use arrow keys to move the probe." aria-describedby="probe-instructions"></canvas><div class="empty-layer" id="xray-empty">${icon('xray')}<span>Waiting for acquisition</span></div></div><div class="map-info"><span>Mean transmission <strong id="xray-mean">—</strong></span><span class="legend-inline"><span>0</span><i class="legend-bar"></i><span>1</span></span></div></section>
        <section class="panel image-panel"><div class="panel-header"><div class="panel-heading">${icon('acoustic')}<div><h2>Acoustic C-scan</h2><p class="panel-subtitle" id="sam-settings">Gated pulse-echo amplitude</p></div></div><div class="header-meta"><label class="window-label" for="sam-window">Window<select id="sam-window" title="Display maximum only; numerical data is unchanged"><option value="0.05">0–0.05</option><option value="0.1">0–0.10</option><option value="0.2" selected>0–0.20</option><option value="0.5">0–0.50</option><option value="1">0–1.00</option></select></label></div></div><div class="map-wrap"><canvas id="sam-map" class="map-canvas" tabindex="0" role="img" aria-label="Scanning acoustic microscopy C-scan. Click to inspect a point; use arrow keys to move the probe." aria-describedby="probe-instructions"></canvas><div class="empty-layer" id="sam-empty">${icon('acoustic')}<span>Waiting for acquisition</span></div></div><div class="map-info"><span>Peak echo <strong id="sam-peak">—</strong></span><span class="legend-inline" id="sam-window-legend" title="Display clips at the selected maximum. Exported amplitudes are unchanged."><span>0</span><i class="legend-bar sam"></i><span id="sam-ceiling-label">0.20</span></span></div></section>
      </div>
      <div class="scan-grid">
        <section class="panel scan-panel"><div class="panel-header"><div class="panel-heading">${icon('target')}<div><h2>Probe A-scan</h2><p class="panel-subtitle" id="probe-state">Select a position on either map</p></div></div><span class="probe-coordinate" id="probe-coordinate">x — / y — mm</span></div><div class="plot-wrap"><canvas class="plot-canvas" id="ascan" role="img" aria-label="Acoustic A-scan waveform and envelope at selected probe location"></canvas></div></section>
        <section class="panel scan-panel"><div class="panel-header"><div class="panel-heading">${icon('layers')}<div><h2>Acoustic B-scan</h2><p class="panel-subtitle" id="bscan-section">Echo section across the specimen</p></div></div><span class="legend-inline"><span>0</span><i class="legend-bar sam"></i><span>1</span></span></div><div class="plot-wrap"><canvas class="plot-canvas" id="bscan" role="img" aria-label="Acoustic B-scan amplitude section, horizontal position versus time"></canvas></div></section>
      </div>
      <div class="instrument-footer"><span id="result-details">Reduced-order simulation · awaiting first acquisition</span><span class="status busy" id="acquisition-status" aria-live="polite">Initializing</span></div>
      <span class="sr-only" id="probe-instructions">Click within an image to move the acoustic probe. With a map focused, arrow keys move one detector pixel and Shift plus arrow moves ten pixels. X-ray linkage is available at zero beam angle.</span>
    </div>
  </main>
  <dialog id="specimen-reference-dialog" aria-labelledby="specimen-reference-title" aria-describedby="specimen-reference-summary"><div class="dialog-header"><div><h2 id="specimen-reference-title">Specimen references</h2><p id="specimen-reference-summary"></p></div><button class="quiet" id="close-specimen-reference" aria-label="Close specimen references">${icon('close')}</button></div><div class="dialog-body"><p class="method-note">Published product facts and the geometry used by this simulator are listed separately below. Simulated microscope outputs are synthetic and have not been validated against measurements of this product.</p><h3>Published product facts</h3><dl id="specimen-published-facts" class="reference-facts"></dl><h3>Modeling assumptions</h3><ul id="specimen-geometry-assumptions"></ul><h3>Source documentation</h3><ol id="specimen-sources" class="reference-sources"></ol><p class="reference-export-note">These references and assumptions are retained in exported twin and acquisition JSON.</p></div></dialog>
  <dialog id="assumptions-dialog" aria-labelledby="assumptions-title"><div class="dialog-header"><div><h2 id="assumptions-title">Model & assumptions</h2><p>Know what the virtual instruments can tell you.</p></div><button class="quiet" id="close-assumptions" aria-label="Close model assumptions">${icon('close')}</button></div><div class="dialog-body"><p class="method-note">This workbench generates synthetic microscope signals from a shared material geometry. It is a reduced-order forward simulator and has not been experimentally validated. It does not solve coupled full-wave multiphysics.</p><h3>X-ray transmission</h3><p>Monoenergetic Beer–Lambert attenuation along rays through the specimen. Photon counting noise is optional and uses a reproducible seed. The display window is fixed at I / I₀ = 0 to 1; exported arrays retain the numerical values. Tilt rotates the ray direction about the y axis, so detector positions no longer correspond exactly to specimen x/y.</p><h3>Scanning acoustic microscopy</h3><p>A water-coupled, normal-incidence pulse-echo approximation uses material impedance boundaries, propagation loss, and a finite-bandwidth pulse. The C-scan reports gated echo amplitude; the A-scan shows the local waveform and envelope, and the B-scan shows an x/time section. Time starts at the specimen top plane and excludes water standoff.</p><h3>Material paths</h3><p>Voxel-center paths sample material occupancy on an XYZ grid. Continuous normal-incidence paths intersect the authored primitives along sampled vertical XY columns, retaining their continuous interface depths. The continuous option requires a 0° beam and does not use the geometry depth-sample setting. Both paths retain the model’s material assumptions, lateral response and finite RF sampling. Continuous paths are an opt-in numerical method, not a claim of measured accuracy. Saved angular X-ray projections retain their separate voxel geometry.</p><h3>Shared specimen</h3><p>The JSON digital twin uses millimetres, with x right, y down in images, and z depth from the top surface. Boxes, spheres, and z-aligned cylinders reference the built-in material library. Later objects replace earlier objects where they overlap. 3D translucency and exploded spacing are display aids; the simulated geometry retains its original positions.</p><h3>Run-specific assumptions</h3><ul id="run-assumptions"><li>Run an acquisition to read the engine's assumptions and warnings.</li></ul><h3>Import & reproducibility</h3><p>Import a <code>schema_version: 1</code> JSON twin, or export a built-in specimen as a starting template. Acquisition exports include the numerical maps, waveforms, settings, digital twin, and available engine provenance. This workbench runs locally; imported files are sent only to the local simulation server.</p><h3>Material properties</h3><div id="material-provenance"></div></div></dialog>
`;

const defaults = { path_model:VOXEL_PATHS,resolution:128,energy_kev:80,angle_deg:0,photons:50000,noise:true,frequency_mhz:50,gate_start_us:.42,gate_end_us:.56,focus_mm:.5,probe_x_mm:3.1,probe_y_mm:3.1,include_defects:true,seed:42 };
const state = { twin:null,materials:[],examples:[],settings:{...defaults},result:null,busy:false,stale:false,exploded:false,probePromise:null,pendingProbe:null,probeToken:0,samCeiling:.2 };
let hbmEditor;
let viewer;
try { viewer = new TwinViewer($('#twin-view')); }
catch(error) { const warning = document.createElement('div');warning.className='empty-layer';warning.textContent='3D display requires WebGL. The simulation maps remain available.';$('#twin-view').append(warning);console.error('3D initialization:',error); }

function notify(message, error = false) {
  const notice=$('#notice');notice.classList.add('visible');notice.classList.toggle('error',error);notice.querySelector('p').textContent=message;
}
function notifyWarnings(warnings) {
  const remaining=warnings.length-2;
  notify(warnings.slice(0,2).join(' ')+(remaining>0?` ${remaining} more warnings. Open Model & assumptions for the full list.`:''));
}
$('#notice button').addEventListener('click',()=>$('#notice').classList.remove('visible'));
function status(text, kind='ready') { const el=$('#acquisition-status');el.textContent=text;el.className=`status ${kind}`; }
async function request(path, body, signal) {
  const response=await fetch(path,{...(body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{}),signal});
  let data;try{data=await response.json();}catch{throw new Error(`The local server returned an unreadable response (${response.status}).`);}
  if(!response.ok) {
    const detail=data.detail || data.error || data.message;
    const message=Array.isArray(detail)?detail.map(item=>`${(item.loc || []).slice(1).join('.')}: ${item.msg || 'Invalid value'}`).join('; '):typeof detail==='string'?detail:detail?.message?`${detail.message}${detail.issues?.length?' '+detail.issues.map(issue=>`${issue.field}: ${issue.reason}${issue.max_absolute_difference!=null?` (maximum coordinate difference ${issue.max_absolute_difference})`:''}`).join('; '):''}`:`Request failed (${response.status}).`;
    throw new Error(message);
  }
  return data;
}
function settingsFromControls() {
  const s={...state.settings};
  for(const [id,key] of [['energy','energy_kev'],['angle','angle_deg'],['photons','photons'],['frequency','frequency_mhz'],['gate-start','gate_start_us'],['gate-end','gate_end_us'],['focus','focus_mm'],['resolution','resolution']])s[key]=Number($(`#${id}`).value);
  s.path_model=$('#path-model').value;s.noise=$('#noise').checked;s.include_defects=$('#include-defects').checked;return s;
}
function refreshLabels() {
  refreshPathControls();const s=state.settings;$('#energy-value').textContent=`${s.energy_kev} keV`;$('#angle-value').textContent=`${s.angle_deg}°`;$('#frequency-value').textContent=`${s.frequency_mhz} MHz`;$('#focus-value').textContent=`${s.focus_mm.toFixed(2)} mm`;
}
function refreshPathControls(){
  const continuous=state.settings.path_model===CONTINUOUS_PATHS;
  $('#depth-samples').disabled=state.busy||continuous;$('#depth-samples-state').textContent=continuous?'(inactive)':'';
  $('#path-model-note').textContent=continuous?'Continuous intersections follow authored layer boundaries along vertical columns. No Z voxel grid is used. XY sampling, lateral blur and finite RF sampling remain.':'Material occupancy is sampled at voxel centers. Depth samples control the Z grid; lateral sampling still limits detail.';
  const tilted=continuous&&state.settings.angle_deg!==0;$('#path-angle-error').hidden=!tilted;$('#path-angle-error').textContent=tilted?'Continuous paths require 0°. Set the beam angle to 0° or choose voxel-center paths.':'';
}
function busy(value,message) {
  state.busy=value;
  document.querySelectorAll('.sidebar input,.sidebar select,.sidebar button').forEach(el=>el.disabled=value);
  $('#export-twin').disabled=value || !state.twin;
  $('#angle').disabled=value || Boolean(state.settings.roi_mm);
  refreshROI();
  $('#run-btn').innerHTML=value?'<i class="loading-spinner"></i><span>Acquiring…</span>':`${icon('play')}<span>Run acquisition</span>`;
  if(message)$('#run-help').textContent=message;
  if(value)status('Acquisition in progress','busy');
}
function markStale() {
  state.stale=Boolean(state.result);$('#instrument').classList.toggle('stale-result',state.stale);
  $('#run-help').textContent='Settings ready. Run to update the images.';status(state.stale?'Settings changed':'Ready to acquire',state.stale?'stale':'ready');
}
function refreshViewer() { if(state.twin && viewer){viewer.setTwin(state.twin,state.materials,state.settings.include_defects,state.exploded,state.microstructure);viewer.setProbe(state.settings.probe_x_mm,state.settings.probe_y_mm);viewer.setROI(state.settings.roi_mm);} }
let microController;
async function refreshMicrostructure(twin){
  microController?.abort();state.microstructure=null;const assembly=twin.hbm_assemblies?.find(a=>a.microstructure?.enabled);if(!assembly)return;
  const controller=microController=new AbortController();
  try{const response=await request('/api/hbm/microstructure',{twin,assembly_id:assembly.id},controller.signal);if(controller!==microController||twin!==state.twin)return;state.microstructure=response.microstructure;refreshViewer();}
  catch(error){if(error.name!=='AbortError'&&twin===state.twin)notify(`Microstructure display metadata unavailable: ${error.message}`,true);}
}
if(viewer)viewer.onViewMode=focused=>{$('#return-package').hidden=!focused;$('#twin-view').classList.toggle('microstructure-focus',focused);$('#scene-mode').textContent=focused?'Explicit patch only · surrounding layers hidden':state.exploded?'Exploded display · simulation geometry unchanged':'Epoxy translucent for inspection';$('#twin-view .axis-label').textContent=focused?'Air spheres are void markers, not mesh subtraction':'x / y in mm · depth from top';};
function referenceLink(source) {
  const link=document.createElement('a');
  try {
    const url=new URL(source.url);
    if(!['https:','http:'].includes(url.protocol))return null;
    link.href=url.href;
  } catch { return null; }
  link.target='_blank';link.rel='noopener noreferrer';link.textContent=source.title;
  return link;
}
function updateSpecimenReference(twin) {
  const reference=twin.reference;
  $('#specimen-reference-btn').hidden=!reference;
  $('#specimen-reference-dialog').close();
  $('#specimen-published-facts').replaceChildren();$('#specimen-geometry-assumptions').replaceChildren();$('#specimen-sources').replaceChildren();
  if(!reference)return;
  $('#specimen-reference-title').textContent=reference.product;
  $('#specimen-reference-summary').textContent=reference.summary;
  const sources=new Map(reference.sources.map(source=>[source.id,source]));
  for(const fact of reference.published_facts) {
    const row=document.createElement('div'),label=document.createElement('dt'),value=document.createElement('dd');
    label.textContent=fact.label;value.append(document.createTextNode(fact.value));
    const citations=document.createElement('span');citations.className='fact-citations';
    for(const id of fact.source_ids){const source=sources.get(id),link=source && referenceLink(source);if(link)citations.append(link);}
    value.append(citations);row.append(label,value);$('#specimen-published-facts').append(row);
  }
  for(const assumption of reference.assumptions){const item=document.createElement('li');item.textContent=assumption;$('#specimen-geometry-assumptions').append(item);}
  for(const source of reference.sources){const item=document.createElement('li'),link=referenceLink(source);item.append(link || document.createTextNode(source.title));$('#specimen-sources').append(item);}
}
function setSpecimenURL(id) {
  const url=new URL(window.location.href);
  if(id)url.searchParams.set('specimen',id);else url.searchParams.delete('specimen');
  window.history.replaceState(null,'',url);
}
function syncSettingsControls() {
  for(const [id,key] of [['energy','energy_kev'],['angle','angle_deg'],['photons','photons'],['frequency','frequency_mhz'],['gate-start','gate_start_us'],['gate-end','gate_end_us'],['focus','focus_mm'],['resolution','resolution']]) {
    const control=$(`#${id}`),value=String(state.settings[key]);
    if(control.type==='range') {
      const increment={energy:5,angle:1,frequency:5,focus:.01}[id],steps=(Number(value)-Number(control.min))/increment;
      control.step=Math.abs(steps-Math.round(steps))<1e-8?String(increment):'any';
    }
    // Valid imported presets may use photon counts outside the convenience menu.
    if(control.tagName==='SELECT' && ![...control.options].some(option=>option.value===value)) {
      const option=document.createElement('option');option.value=value;option.textContent=Number(value).toLocaleString();control.append(option);
    }
    control.value=value;
  }
  $('#noise').checked=state.settings.noise;$('#include-defects').checked=state.settings.include_defects;
  $('#path-model').value=state.settings.path_model || VOXEL_PATHS;$('#depth-samples').value=state.settings.depth_samples?String(state.settings.depth_samples):'';refreshROI();
}
function setTwin(twin, presetId, preserve=false) {
  const previousSettings=state.settings,previousResult=state.result;
  state.twin=twin;
  state.settings=preserve?{...previousSettings}:{...defaults,...(twin.recommended_settings || {})};
  state.samCeiling=/H100/i.test(twin.reference?.product || '')?.5:.2;
  $('#sam-window').value=String(state.samCeiling);$('#sam-ceiling-label').textContent=state.samCeiling.toFixed(2);
  if(!preserve && !twin.recommended_settings) {
    if(presetId==='power-die'){state.settings.gate_start_us=.25;state.settings.gate_end_us=.4;}
    state.settings.probe_x_mm=presetId?Math.min(defaults.probe_x_mm,twin.size_mm[0]/2):twin.size_mm[0]/2;
    state.settings.probe_y_mm=presetId?Math.min(defaults.probe_y_mm,twin.size_mm[1]/2):twin.size_mm[1]/2;
  }
  state.settings.probe_x_mm=Math.min(state.settings.probe_x_mm,twin.size_mm[0]);
  state.settings.probe_y_mm=Math.min(state.settings.probe_y_mm,twin.size_mm[1]);
  state.settings.focus_mm=Math.min(state.settings.focus_mm,twin.size_mm[2]);
  $('#gate-hint').textContent=twin.recommended_settings?'Specimen preset · synthetic inspection gate':presetId?'Die-attach inspection gate':'Adjust to the interfaces in your specimen';
  $('#focus').max=twin.size_mm[2];$('#depth-max').textContent=`${twin.size_mm[2].toFixed(2)} mm`;syncSettingsControls();
  updateSpecimenReference(twin);setSpecimenURL(presetId);
  refreshMicrostructure(twin);hbmEditor?.setTwin(twin);updateROISites(twin,preserve);
  $('#model-title').textContent=twin.name;$('#specimen-description').textContent=twin.description || 'Imported primitive digital twin.';
  $('#dimensions').replaceChildren(document.createTextNode(twin.size_mm.map(n=>Number(n.toFixed(2))).join(' × ')+' '));const unit=document.createElement('span');unit.textContent='mm';$('#dimensions').append(unit);
  $('#primitive-count').textContent=`${twin.objects.length} primitives · ${new Set(twin.objects.map(o=>o.material)).size} materials`;
  const count=twin.objects.filter(o=>o.role==='defect').length;$('#defect-count').textContent=count;$('#defect-note').textContent=count?`${count} modeled defect${count===1?'':'s'}. Amber air spheres mark voids; missing bumps use epoxy replacements. Translucency and exploded spacing affect only this view.`:'No defects in this specimen. Translucency and exploded spacing affect only this view.';
  const key=$('#material-key');key.replaceChildren();
  for(const mat of state.materials.filter(m=>twin.objects.some(o=>o.material===m.id))) {
    const item=document.createElement('span');item.className='material-item';const dot=document.createElement('i');if(/^#[\da-f]{6}$/i.test(mat.color))dot.style.background=mat.color;const label=document.createElement('span');label.textContent=mat.name || mat.id;item.append(dot,label);key.append(item);
  }
  state.result=null;state.stale=false;state.probeToken++;$('#instrument').classList.remove('stale-result');$('#export-results').disabled=true;$('#export-twin').disabled=false;
  ['#xray-empty','#sam-empty'].forEach(id=>$(id).classList.remove('hidden'));
  ['#xray-map','#sam-map','#ascan','#bscan'].forEach(id=>{const c=$(id);c.getContext('2d').clearRect(0,0,c.width,c.height);c._plot=null;});
  $('#xray-mean').textContent='—';$('#sam-peak').textContent='—';$('#xray-settings').textContent='Projection through the shared digital twin';$('#sam-settings').textContent='Gated pulse-echo amplitude';$('#bscan-section').textContent='Echo section across the specimen';$('#probe-coordinate').textContent='x — / y — mm';$('#probe-state').textContent='Select a position on either map';
  refreshLabels();refreshViewer();markStale();
  if(preserve && previousResult){state.result=previousResult;$('#export-results').disabled=false;['#xray-empty','#sam-empty'].forEach(id=>$(id).classList.add('hidden'));renderResultDetails();updateProbeLabels();updateAssumptions();drawAll();markStale();$('#run-help').textContent='HBM geometry changed. Run to acquire the edited specimen.';}
}

function updateROISites(twin,preserve=false) {
  const active=!preserve && twin.hbm_assemblies?.find(assembly=>assembly.microstructure?.enabled);
  const selected=active?.id || $('#roi-site').value;$('#roi-site').replaceChildren();
  for(const assembly of twin.hbm_assemblies || []){const option=document.createElement('option');option.value=assembly.id;option.textContent=assembly.name || assembly.id;$('#roi-site').append(option);}
  if([...$('#roi-site').options].some(option=>option.value===selected))$('#roi-site').value=selected;
  $('#hbm-editor-btn').hidden=!twin.hbm_assemblies?.length;refreshROI();
}
function refreshROI() {
  const roi=state.settings.roi_mm;
  $('#roi-controls').hidden=!state.twin?.hbm_assemblies?.length && !roi;
  $('#roi-site').disabled=state.busy || !state.twin?.hbm_assemblies?.length;$('#roi-stack').disabled=state.busy || !state.twin?.hbm_assemblies?.length;
  $('#roi-summary').textContent=roi?`x ${roi[0].toFixed(4)}–${roi[2].toFixed(4)} / y ${roi[1].toFixed(4)}–${roi[3].toFixed(4)} mm · full depth`:'Full specimen · depth retained';
  $('#roi-angle-hint').hidden=!roi;$('#angle').disabled=state.busy || Boolean(roi);refreshPathControls();
}
function selectROI(assembly,selection) {
  if(!state.twin || state.busy)return;
  if(assembly){const [x,y]=assembly.center_xy_mm,[w,h]=assembly.footprint_mm;state.settings.roi_mm=[x-w/2,y-h/2,x+w/2,y+h/2];state.settings.probe_x_mm=x;state.settings.probe_y_mm=y;state.settings.angle_deg=0;if(!state.settings.depth_samples&&state.settings.path_model!==CONTINUOUS_PATHS){state.settings.depth_samples=1024;$('#depth-samples').value='1024';}$('#angle').value='0';$('#roi-site').value=assembly.id;}
  else delete state.settings.roi_mm;
  if(assembly&&selection?.bounds_mm){state.settings.roi_mm=[...selection.bounds_mm];const [x0,y0,x1,y1]=selection.bounds_mm;state.settings.probe_x_mm=selection.probe_mm?.[0] ?? (x0+x1)/2;state.settings.probe_y_mm=selection.probe_mm?.[1] ?? (y0+y1)/2;if(selection.microstructure){state.settings.resolution=64;if(state.settings.path_model!==CONTINUOUS_PATHS)state.settings.depth_samples=1024;state.settings.frequency_mhz=100;syncSettingsControls();}}
  refreshROI();refreshLabels();refreshViewer();markStale();
}
function renderResultDetails() {
  if(!state.result)return;const result=state.result,settings=result.settings,meta=result.metadata,pitch=meta.pixel_pitch_um;
  $('#xray-settings').textContent=`${settings.energy_kev} keV · ${settings.angle_deg}° incidence · ${settings.noise?'Poisson noise':'noise off'}`;
  $('#sam-settings').textContent=`${settings.frequency_mhz} MHz · gate ${settings.gate_start_us.toFixed(2)}–${settings.gate_end_us.toFixed(2)} µs`;
  $('#xray-mean').textContent=`${(result.xray.mean_transmission*100).toFixed(1)}%`;$('#sam-peak').textContent=Number(result.sam.peak_amplitude).toFixed(3);
  const model=meta.path_model ?? settings.path_model ?? VOXEL_PATHS,continuous=model===CONTINUOUS_PATHS;
  const lateral=Array.isArray(pitch)&&pitch.length>=2&&pitch.every(Number.isFinite)?`${pitch.map(v=>Number(v.toFixed(2))).join(' × ')} µm XY pitch`:'XY pitch unavailable';
  const depth=continuous?'Z voxel grid inactive':`${settings.depth_samples || settings.resolution*2} depth samples`;
  $('#result-details').textContent=`${pathModelLabel(model)} · ${settings.resolution} × ${settings.resolution} pixels · ${lateral} · ${depth} · ${(meta.runtime_ms/1000).toFixed(2)} s · seed ${meta.seed}`;
  $('#result-details').dataset.pathModel=model;
}
function acquiredSettings() { return state.result?.settings || state.settings; }
function drawAll() {
  if(!state.result)return;
  const result=state.result,s=acquiredSettings(),inspection=result.probe_inspection;
  const activeAscan=inspection?.ascan || result.ascan,activeBscan=inspection?.bscan || result.bscan;
  const p=activeAscan?.probe_mm || [s.probe_x_mm,s.probe_y_mm];
  mapPlot($('#xray-map'),result.xray,'xray',p,{showProbe:s.angle_deg===0});
  mapPlot($('#sam-map'),result.sam,'sam',p,{ceiling:state.samCeiling});
  ascanPlot($('#ascan'),activeAscan,s);bscanPlot($('#bscan'),activeBscan,p);
}
const observer=new ResizeObserver(()=>requestAnimationFrame(drawAll));document.querySelectorAll('.map-wrap,.plot-wrap').forEach(el=>observer.observe(el));
function updateProbeLabels() {
  const p=(state.result?.probe_inspection?.ascan || state.result?.ascan)?.probe_mm;if(!p)return;
  $('#probe-coordinate').textContent=`x ${p[0].toFixed(2)} / y ${p[1].toFixed(2)} mm`;
  $('#probe-state').textContent='Waveform & envelope · time from top plane';
  const section=state.result.probe_inspection?.bscan || state.result.bscan;
  if(section)$('#bscan-section').textContent=`Section at y = ${section.y_mm.toFixed(2)} mm · amplitude 0–1`;
}
function updateAssumptions() {
  const list=$('#run-assumptions');list.replaceChildren();
  const assumptions=state.result?.metadata?.assumptions || [];
  const warnings=state.result?.metadata?.warnings || [];
  for(const text of [...assumptions,...warnings]){const li=document.createElement('li');li.textContent=text;list.append(li);}
  if(!list.children.length){const li=document.createElement('li');li.textContent='No additional assumptions were reported by this engine version.';list.append(li);}
}
async function acquire() {
  if(state.busy)return;
  if(!state.twin){await initialize();return;}
  const settings=settingsFromControls();
  if(!Number.isFinite(settings.gate_start_us)||!Number.isFinite(settings.gate_end_us)||settings.gate_end_us<=settings.gate_start_us){notify('The acoustic time gate must end after it starts. Update the gate in microseconds and run again.',true);$('#gate-end').focus();return;}
  state.settings=settings;state.probeToken++;state.pendingProbe=null;busy(true,'Tracing X-ray rays and acoustic echoes…');
  try{
    if(state.probePromise)await state.probePromise;
    const result=await request('/api/simulate',{twin:state.twin,settings});
    result.twin ||= structuredClone(state.twin);result.settings ||= structuredClone(settings);
    state.result=result;state.stale=false;$('#instrument').classList.remove('stale-result');
    ['#xray-empty','#sam-empty'].forEach(id=>$(id).classList.add('hidden'));
    const meta=result.metadata;renderResultDetails();
    $('#export-results').disabled=false;updateProbeLabels();updateAssumptions();drawAll();
    const warnings=meta.warnings || [];if(warnings.length)notifyWarnings(warnings);else $('#notice').classList.remove('visible');
    busy(false,'Acquired. Click a map to inspect the echoes.');status('Acquisition complete');refreshViewer();
  }catch(error){busy(false,'Acquisition failed. Adjust settings or retry.');status('Acquisition failed','error');notify(error.message || 'The local simulation server could not be reached. Start the server and run again.',true);}
}
async function inspect(point) {
  if(!state.result||state.busy)return;
  if(state.stale){notify('Run the pending specimen or scanning changes before moving the probe. The displayed images belong to the previous acquisition.');return;}
  state.pendingProbe=point;
  $('#probe-state').textContent='Calculating local echoes…';
  if(state.probePromise)return;
  state.probePromise=(async()=>{
    while(state.pendingProbe && !state.busy){
      const selected=state.pendingProbe;state.pendingProbe=null;
      const token=state.probeToken,snapshot=state.result;
      if(!snapshot)break;
      const s={...snapshot.settings,probe_x_mm:selected[0],probe_y_mm:selected[1]};
      state.settings.probe_x_mm=selected[0];state.settings.probe_y_mm=selected[1];
      try{
        const result=await request('/api/probe',{twin:snapshot.twin,settings:s});
        if(token!==state.probeToken||snapshot!==state.result)continue;
        // Retain original run arrays/hash and place subsequent probe data in a separate provenance record.
        state.result.probe_inspection={...result,probe_mm:selected,created_at:new Date().toISOString(),settings:s,parent_run_id:snapshot.run_id};
        updateProbeLabels();drawAll();viewer?.setProbe(...selected);
      }catch(error){if(token!==state.probeToken)continue;$('#probe-state').textContent='Probe unavailable; previous waveform retained';notify(`Probe inspection failed: ${error.message}`,true);}
    }
  })();
  try{await state.probePromise;}finally{state.probePromise=null;}
}
for(const id of ['xray-map','sam-map']){
  const canvas=$(`#${id}`);
  canvas.addEventListener('click',event=>{
    if(id==='xray-map'&&acquiredSettings().angle_deg!==0){notify('The tilted X-ray view is a projection. Inspect physical x/y positions on the acoustic map, or acquire at 0° to link both images.');return;}
    const point=pointFromEvent(canvas,event);if(point)inspect(point);
  });
  canvas.addEventListener('keydown',event=>{
    if(!state.result||!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key))return;
    event.preventDefault();
    if(id==='xray-map'&&acquiredSettings().angle_deg!==0){notify('Use the acoustic map to inspect physical positions while the X-ray beam is tilted.');return;}
    const p=[...(state.pendingProbe || state.result.probe_inspection?.ascan?.probe_mm || state.result.ascan.probe_mm)],n=state.result.settings.resolution,jump=event.shiftKey?10:1;
    const [x0,x1,y0,y1]=state.result.sam.extent_mm,dx=(x1-x0)/n,dy=(y1-y0)/n;
    if(event.key==='ArrowLeft')p[0]-=dx*jump;if(event.key==='ArrowRight')p[0]+=dx*jump;if(event.key==='ArrowUp')p[1]-=dy*jump;if(event.key==='ArrowDown')p[1]+=dy*jump;
    p[0]=Math.min(x1-dx/2,Math.max(x0+dx/2,p[0]));p[1]=Math.min(y1-dy/2,Math.max(y0+dy/2,p[1]));inspect(p);
  });
}
for(const id of ['path-model','energy','angle','photons','noise','frequency','gate-start','gate-end','focus','resolution','include-defects']){
  $(`#${id}`).addEventListener('input',()=>{state.settings=settingsFromControls();refreshLabels();markStale();if(id==='include-defects')refreshViewer();if(id==='gate-start'||id==='gate-end')$('#gate-hint').textContent='Custom time gate';});
}
$('#run-btn').addEventListener('click',acquire);
$('#depth-samples').addEventListener('change',()=>{if($('#depth-samples').value)state.settings.depth_samples=Number($('#depth-samples').value);else delete state.settings.depth_samples;markStale();});
$('#roi-stack').addEventListener('click',()=>selectROI(state.twin?.hbm_assemblies?.find(item=>item.id===$('#roi-site').value)));
$('#roi-full').addEventListener('click',()=>selectROI(null));
hbmEditor=new HBMEditor({request,getTwin:()=>state.twin,onApply:twin=>{setTwin(twin,undefined,true);const picker=$('#example-picker');picker.querySelector('option[value="__edited"]')?.remove();const option=document.createElement('option');option.value='__edited';option.textContent=`Edited: ${twin.name}`;picker.append(option);picker.value='__edited';},onSelectROI:selectROI,onCausalVolume:options=>openCausalVolumes(options),onInspectColumn:options=>openLayeredAcoustics(options),getPathModel:()=>state.settings.path_model,onSelectSite:id=>{$('#roi-site').value=id;},getIncludeDefects:()=>state.settings.include_defects,onFocusPatch:summary=>{state.microstructure=summary;state.exploded=false;$('#explode-btn').setAttribute('aria-pressed','false');$('#explode-btn').classList.remove('active');refreshViewer();viewer?.focusMicrostructure(summary);$('#twin-view').scrollIntoView({block:'center'});}});
const volumeWorkspace=new VolumeWorkspace({request,onCausal:()=>openCausalVolumes(),getSnapshot:()=>({twin:state.twin,settings:settingsFromControls()}),onRecipes:options=>openAcquisitionComparisons(options)});
let acquisitionComparisonsPromise;
async function openAcquisitionComparisons(options){try{acquisitionComparisonsPromise ||= import('./acquisition-comparisons.js').then(({AcquisitionComparisons})=>new AcquisitionComparisons({request,getSnapshot:()=>({twin:state.twin,settings:settingsFromControls()})}));await (await acquisitionComparisonsPromise).open(options);}catch(error){acquisitionComparisonsPromise=null;notify(`Unable to open recipes and comparisons: ${error.message}`,true);}}
$('#recipes-btn').addEventListener('click',()=>openAcquisitionComparisons());
let xrayComparisonsPromise;
async function openXrayComparisons(options){try{xrayComparisonsPromise ||= import('./xray-acquisition-comparisons.js').then(({XrayAcquisitionComparisons})=>new XrayAcquisitionComparisons({request,getSnapshot:()=>({twin:state.twin,settings:settingsFromControls()})}));await (await xrayComparisonsPromise).open(options);}catch(error){xrayComparisonsPromise=null;notify(`Unable to open X-ray recipes: ${error.message}`,true);}}
$('#xray-recipes-btn').addEventListener('click',()=>openXrayComparisons());

$('#volumes-btn').addEventListener('click',()=>volumeWorkspace.open());
let causalWorkspacePromise;
async function openCausalVolumes(options){try{causalWorkspacePromise ||= import('./causal-volumes.js').then(({CausalVolumeWorkspace})=>new CausalVolumeWorkspace({request,onCompare:options=>openCausalComparisons(options),getSnapshot:()=>({twin:state.twin,settings:settingsFromControls()})}));(await causalWorkspacePromise).open(options);}catch(error){causalWorkspacePromise=null;notify(`Unable to open causal volumes: ${error.message}`,true);}}
$('#causal-volumes-btn').addEventListener('click',()=>openCausalVolumes());
let causalComparisonPromise;
async function openCausalComparisons(options){try{causalComparisonPromise ||= import('./causal-comparisons.js').then(({CausalComparisonWorkspace})=>new CausalComparisonWorkspace({request}));await (await causalComparisonPromise).open(options);}catch(error){causalComparisonPromise=null;notify(`Unable to open causal comparisons: ${error.message}`,true);}}
$('#causal-comparisons-btn').addEventListener('click',()=>openCausalComparisons());
let xrayWorkspacePromise;
$('#xray-volumes-btn').addEventListener('click',async()=>{try{xrayWorkspacePromise ||= import('./xray.js').then(({XrayWorkspace})=>new XrayWorkspace({request,onRecipes:options=>openXrayComparisons(options),getSnapshot:()=>({twin:state.twin,settings:settingsFromControls()})}));(await xrayWorkspacePromise).open();}catch(error){xrayWorkspacePromise=null;notify(`Unable to open the X-ray workspace: ${error.message}`,true);}});
let reconstructionWorkspacePromise;
$('#reconstruction-btn').addEventListener('click',async()=>{try{reconstructionWorkspacePromise ||= import('./reconstruction.js').then(({ReconstructionWorkspace})=>new ReconstructionWorkspace({request}));(await reconstructionWorkspacePromise).open();}catch(error){reconstructionWorkspacePromise=null;notify(`Unable to open CT reconstruction: ${error.message}`,true);}});
let depthWorkspacePromise;
let layeredAcousticsPromise;
async function openLayeredAcoustics(options){try{layeredAcousticsPromise ||= import('./layered-acoustics.js').then(({LayeredAcousticsWorkspace})=>new LayeredAcousticsWorkspace({request,getSnapshot:()=>({twin:state.twin,settings:settingsFromControls()})}));await (await layeredAcousticsPromise).open(options);}catch(error){layeredAcousticsPromise=null;notify(`Unable to open layered acoustics: ${error.message}`,true);}}
$('#layered-acoustics-btn').addEventListener('click',()=>openLayeredAcoustics());
$('#sam-depth-btn').addEventListener('click',async()=>{try{depthWorkspacePromise ||= import('./depth.js').then(({DepthWorkspace})=>new DepthWorkspace({request}));(await depthWorkspacePromise).open();}catch(error){depthWorkspacePromise=null;notify(`Unable to open SAM depth mapping: ${error.message}`,true);}});
$('#hbm-editor-btn').addEventListener('click',()=>hbmEditor.open($('#roi-site').value));
$('#sam-window').addEventListener('change',event=>{state.samCeiling=Number(event.target.value);$('#sam-ceiling-label').textContent=state.samCeiling.toFixed(2);drawAll();});
$('#return-package').addEventListener('click',()=>viewer?.reset());
$('#reset-view').addEventListener('click',()=>viewer?.reset());
$('#explode-btn').addEventListener('click',()=>{state.exploded=!state.exploded;$('#explode-btn').setAttribute('aria-pressed',String(state.exploded));$('#explode-btn').classList.toggle('active',state.exploded);$('#scene-mode').textContent=state.exploded?'Exploded display · simulation geometry unchanged':'Epoxy translucent for inspection';refreshViewer();});
$('#example-picker').addEventListener('change',async event=>{const example=state.examples.find(e=>e.id===event.target.value);if(example){setTwin(structuredClone(example.twin),example.id);await acquire();}});
$('#import-twin').addEventListener('click',()=>$('#twin-file').click());
$('#twin-file').addEventListener('change',async event=>{
  const file=event.target.files?.[0];if(!file)return;
  if(file.size>2*1024*1024){notify('Choose a digital twin JSON file smaller than 2 MB.',true);event.target.value='';return;}
  busy(true,'Validating imported specimen…');
  try{
    let twin;try{twin=JSON.parse(await file.text());}catch{throw new Error('This file is not valid JSON. Export a built-in twin to see the supported format.');}
    const result=await request('/api/validate',twin);setTwin(result.twin);
    const picker=$('#example-picker');picker.querySelector('option[value="__imported"]')?.remove();const option=document.createElement('option');option.value='__imported';option.textContent=`Imported: ${result.twin.name}`;picker.append(option);picker.value='__imported';
    if(result.warnings?.length)notifyWarnings(result.warnings);busy(false);await acquire();
  }catch(error){busy(false,'Import failed. Current specimen retained.');status(state.result?'Acquisition retained':'Import failed','error');notify(`Import failed: ${error.message}`,true);}finally{event.target.value='';}
});
function downloadJSON(value,filename) {
  const blob=new Blob([JSON.stringify(value,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),link=document.createElement('a');
  link.href=url;link.download=filename;link.style.display='none';document.body.append(link);link.click();link.remove();
  // Native download managers may open a large Blob after the click handler returns.
  setTimeout(()=>URL.revokeObjectURL(url),60000);
}
const safeName=name=>String(name).replace(/[^a-z0-9_-]+/gi,'-').replace(/^-|-$/g,'').slice(0,75)||'specimen';
$('#export-twin').addEventListener('click',()=>{if(state.twin)downloadJSON(state.twin,`${safeName(state.twin.name)}.json`);});
$('#export-results').addEventListener('click',()=>{if(state.result)downloadJSON({...state.result,export_metadata:{application:'Virtual microscopy workbench',version:'0.14.0',exported_at:new Date().toISOString(),display_windows:{xray:[0,1],sam:[0,state.samCeiling],bscan:[0,1]},settings_changed_since_acquisition:state.stale}},`${safeName(state.result.twin.name)}-acquisition-${safeName(state.result.run_id || new Date().toISOString())}.json`);});
$('#assumptions-btn').addEventListener('click',()=>$('#assumptions-dialog').showModal());
$('#specimen-reference-btn').addEventListener('click',()=>$('#specimen-reference-dialog').showModal());
$('#close-specimen-reference').addEventListener('click',()=>$('#specimen-reference-dialog').close());
$('#close-assumptions').addEventListener('click',()=>$('#assumptions-dialog').close());
$('#assumptions-dialog').addEventListener('click',event=>{if(event.target===$('#assumptions-dialog')){const bounds=event.target.getBoundingClientRect();if(event.clientX<bounds.left||event.clientX>bounds.right||event.clientY<bounds.top||event.clientY>bounds.bottom)event.target.close();}});
async function initialize() {
  if(state.busy)return;busy(true,'Loading digital twins and material properties…');
  try{
    const [examples,materials]=await Promise.all([request('/api/examples'),request('/api/materials')]);
    state.examples=examples;state.materials=materials;
    const picker=$('#example-picker');picker.replaceChildren();for(const example of examples){const option=document.createElement('option');option.value=example.id;option.textContent=example.name;picker.append(option);}
    if(!examples.length)throw new Error('The simulation server has no example twins. Import a supported JSON specimen.');
    const provenance=$('#material-provenance');provenance.replaceChildren();
    for(const mat of materials){
      const entry=document.createElement('details');entry.className='material-entry';const summary=document.createElement('summary');summary.textContent=mat.name || mat.id;entry.append(summary);
      const properties=document.createElement('p');properties.textContent=`${Number(mat.impedance_mrayl).toFixed(2)} MRayl impedance · ${mat.sound_speed_m_s} m/s sound speed · ${mat.density_g_cm3} g/cm³ density`;entry.append(properties);
      const source=mat.provenance;
      const descriptions=typeof source==='string'?[source]:[source?.xray,source?.acoustic,source?.calibration];
      for(const description of descriptions.filter(Boolean)){const p=document.createElement('p');p.textContent=description;entry.append(p);}
      if(source && typeof source==='object')for(const [i,url] of [...(source.xray_sources || []),source.acoustic_reference].filter(Boolean).entries()){
        if(!/^https?:\/\//i.test(url))continue;const a=document.createElement('a');a.href=url;a.target='_blank';a.rel='noopener noreferrer';a.textContent=i<(source.xray_sources?.length||0)?`X-ray reference ${i+1}`:'Acoustic reference';entry.append(a);
      }
      provenance.append(entry);
    }
    const requested=new URL(window.location.href).searchParams.get('specimen');
    const selected=examples.find(example=>example.id===requested) || examples[0];
    picker.value=selected.id;setTwin(structuredClone(selected.twin),selected.id);busy(false);await acquire();
  }catch(error){busy(false,'Start the local server, then run acquisition.');status('Local server unavailable','error');notify(`Unable to initialize the workbench: ${error.message}`,true);}
}
initialize();
