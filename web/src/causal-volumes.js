import './causal-volumes.css';
import {drawLayeredCurve} from './layered-plots.js';

const KIND='sam_causal_rf_volume';
const $=id=>document.getElementById(`cv-${id}`);
const fmt=value=>value==null?'—':!Number.isFinite(Number(value))?String(value):Math.abs(value)>0&&Math.abs(value)<.001?Number(value).toExponential(4):Number(value).toLocaleString(undefined,{maximumFractionDigits:6});
const bytes=value=>value==null?'—':value>=2**20?`${(value/2**20).toFixed(2)} MiB`:`${(value/1024).toFixed(1)} KiB`;
const field=(id,label,value,min,max,step='any')=>`<label for="cv-${id}">${label}<input id="cv-${id}" type="number" value="${value}" min="${min}" max="${max}" step="${step}" required></label>`;
const isComplete=item=>item.complete===true||['complete','completed'].includes(item.state||item.status);
const isActive=status=>['queued','running','preparing','cancelling','cancel_requested','resuming'].includes(status);
const identity=item=>item.dataset_id||item.id;
const numericFields=['scan_nx','scan_ny','center_frequency_mhz','fractional_bandwidth','gamma_order','precision_bits','absolute_tolerance','sample_rate_mhz','record_start_us','record_duration_us','surface_standoff_mm'];
const productLabel=product=>({rf:'Signed real pressure',imaginary:'Imaginary quadrature',envelope:'Complex-pressure magnitude'}[product]);

// Instrument palette: white paper, pale blue controls, navy axes, blue signed RF,
// purple quadrature and green complex magnitude. Maps and saved evidence lead.
export class CausalVolumeWorkspace {
  constructor({request,getSnapshot}) {
    Object.assign(this,{request,getSnapshot});
    this.jobs=[];this.datasets=[];this.cursor={x_index:0,y_index:0,time_index:0};this.product='envelope';this.ceiling=.5;
    document.body.insertAdjacentHTML('beforeend',`
      <dialog id="cv-dialog" aria-labelledby="cv-title" aria-describedby="cv-intro">
        <header class="cv-header"><div><h2 id="cv-title">Causal multilayer · independent columns</h2><p id="cv-intro">Saved complex pressure across x, y and recording time.</p></div><button id="cv-close" class="quiet" aria-label="Close causal volumes">✕</button></header>
        <div class="cv-layout">
          <aside class="cv-acquisition" aria-label="Causal acquisition and saved datasets">
            <details id="cv-acquire-panel" open><summary>New causal ROI volume</summary>
              <p id="cv-snapshot" class="cv-hint"></p><button id="cv-capture" class="small" type="button">Use current specimen & ROI</button>
              <form id="cv-form">
                <fieldset><legend>Complete material columns</legend><p class="cv-hint">Continuous boundaries, normal incidence, full specimen thickness. Independent, unfocused columns; no depth voxel grid or lateral blur.</p>
                  <div class="cv-fields">${field('scan_nx','X positions',32,16,64,1)}${field('scan_ny','Y positions',64,16,64,1)}</div>
                  <details id="cv-roi-panel"><summary>Global ROI bounds (mm)</summary><div class="cv-fields">${field('x0','X minimum',0,0,1000)}${field('x1','X maximum',1,0,1000)}${field('y0','Y minimum',0,0,1000)}${field('y1','Y maximum',1,0,1000)}</div></details>
                  <p id="cv-pitch" class="cv-hint"></p><button id="cv-patch-preset" type="button" class="small" hidden>Use patch ROI · 0.15 × 0.25 mm · 32 × 64</button>
                  <label class="cv-check"><input id="cv-include_defects" type="checkbox" checked>Include authored defects</label>
                </fieldset>
                <fieldset><legend>Causal gamma excitation</legend><div class="cv-fields">${field('center_frequency_mhz','Carrier (MHz)',50,10,150)}${field('fractional_bandwidth','Fractional bandwidth',.5,.2,1)}${field('gamma_order','Gamma order',12,4,24,1)}${field('surface_standoff_mm','Water standoff (mm)',0,0,5)}</div><p class="cv-hint">Gamma envelope-peak delay is excitation latency, separate from material propagation. Fixed water exterior media and nominal lossless layers; no receiver response or noise.</p></fieldset>
                <fieldset><legend>Retained recording</legend><div class="cv-fields">${field('record_start_us','Start (µs)',0,0,12)}${field('record_duration_us','Duration (µs)',2,.05,12)}${field('sample_rate_mhz','Sample rate (MHz)',400,80,2400)}</div><p id="cv-sampling" class="cv-hint"></p></fieldset>
                <fieldset><legend>Numerical acceptance</legend><div class="cv-fields">${field('absolute_tolerance','Absolute error tolerance',1e-7,1e-12,1e-3)}<label for="cv-precision_bits">Arithmetic precision (bits)<select id="cv-precision_bits"><option>64</option><option>96</option><option selected>128</option><option>192</option><option>256</option></select></label></div><p class="cv-hint">The saved bound covers the declared scalar model at retained sample centers. It does not establish material accuracy, spatial resolution or experimental calibration. Final arithmetic acceptance may fail; inputs are retained.</p></fieldset>
                <div id="cv-estimate" class="cv-evidence" role="status">Estimate the complete ROI before starting.</div><details id="cv-estimate-details" hidden><summary>Resource and model details</summary><pre id="cv-estimate-json"></pre></details>
                <button id="cv-estimate-btn" class="small" type="button">Estimate resources</button><button id="cv-start" class="primary" type="submit" disabled>Start causal volume</button>
              </form>
            </details>
            <section class="cv-catalog"><h3>Acquisition jobs</h3><div id="cv-jobs"></div></section>
            <section class="cv-catalog"><div class="cv-list-heading"><h3>Saved causal volumes</h3><button id="cv-refresh" class="quiet" aria-label="Refresh causal datasets">↻</button></div><div id="cv-datasets"></div></section>
          </aside>
          <main class="cv-inspector">
            <p id="cv-status" class="cv-status" role="status" aria-live="polite">Choose an ROI and estimate its saved causal response.</p>
            <section id="cv-empty" class="cv-empty"><h3>Retain the full reflected waveform.</h3><p>Each XY position follows its complete material column. Coherent repeated reflections produce signed real pressure, imaginary quadrature and their complex magnitude.</p><p>Recording time stays in microseconds. Repeated echoes do not identify unique physical depths.</p></section>
            <section id="cv-viewer" hidden>
              <div class="cv-result-heading"><div><h3 id="cv-dataset-name"></h3><p id="cv-dataset-detail" class="cv-hint"></p></div><div class="cv-result-actions"><button id="cv-use-saved" class="small">Use saved settings as draft</button><a id="cv-export" class="cv-download" download>Download Zarr ZIP</a></div></div>
              <p class="cv-model-note">Full coherent gamma response; independent, unfocused columns. Repeated echoes have no unique depth. SAM depth mapping, legacy comparisons and recipes are unavailable for this mode.</p>
              <div class="cv-cursors">${['x','y','time'].map(key=>`<label for="cv-${key}">${key==='time'?'Recorded time':key.toUpperCase()} <output id="cv-${key}-value"></output><input id="cv-${key}" type="range" min="0" max="0" value="0" step="1"></label>`).join('')}</div>
              <div class="cv-processing"><label for="cv-product">Map product<select id="cv-product"><option value="envelope">Complex-pressure magnitude</option><option value="rf">Signed real pressure</option><option value="imaginary">Imaginary quadrature</option></select></label><label for="cv-ceiling">Display amplitude ceiling<input id="cv-ceiling" type="number" min="1e-16" value=".5" step="any"></label><button id="cv-fit" class="small">Fit selected trace</button><label class="cv-check"><input id="cv-quadrature" type="checkbox">Show quadrature trace</label></div>
              <form id="cv-gate-form" class="cv-gate"><label for="cv-gate-start">Gate start (µs)<input id="cv-gate-start" type="number" min="0" step="any" required></label><label for="cv-gate-end">Gate end (µs)<input id="cv-gate-end" type="number" min="0" step="any" required></label><label for="cv-gate-mode">Gate statistic<select id="cv-gate-mode"><option value="peak_envelope">Peak complex magnitude</option><option value="rms_rf">RMS signed RF</option></select></label><button class="small" id="cv-apply-gate">Apply gate</button></form>
              <p id="cv-view-note" class="cv-hint"></p>
              <div class="cv-map-grid">${[['xy','XY at saved time'],['cscan','Gated C-scan'],['xt','X–time section'],['yt','Y–time section']].map(([id,label])=>`<section class="cv-plot"><header><h4>${label}</h4><span id="cv-${id}-caption"></span></header><canvas id="cv-${id}" tabindex="0" role="img" aria-label="Causal ${label}; select saved position and time"></canvas></section>`).join('')}</div>
              <section class="cv-trace"><header><h4>Saved complex-pressure trace</h4><span>Blue: signed RF · Green: magnitude · Purple: optional quadrature</span></header><canvas id="cv-ascan" tabindex="0" role="img" aria-label="Signed causal RF, complex-pressure magnitude and optional imaginary pressure"></canvas></section>
              <p id="cv-readout" class="cv-readout"></p><p id="cv-bound-summary" class="cv-evidence"></p>
              <details id="cv-certificate"><summary>Selected column numerical certificate</summary><div id="cv-bound-table"></div><p class="cv-hint">The saved total is authoritative. This is a bound on retained complex pressure and magnitude, not a new bound on every gate statistic. Gamma timing and material assumptions remain separate from numerical error.</p><p class="cv-hint">Kernel column diagnostics (before volume storage), retained within the saved volume certificate:</p><pre id="cv-certificate-json"></pre></details>
              <details id="cv-provenance"><summary>Frozen acquisition and model identity</summary><pre></pre></details>
            </section>
          </main>
        </div>
      </dialog>`);
    $('close').onclick=()=>$('dialog').close();
    $('dialog').addEventListener('close',()=>{clearTimeout(this.pollTimer);clearTimeout(this.viewTimer);for(const key of ['estimateController','catalogController','selectionController','viewController'])this[key]?.abort();});
    $('capture').onclick=()=>this.capture();$('patch-preset').onclick=()=>this.applyPatchPreset();
    $('form').addEventListener('input',()=>this.changed());$('form').onsubmit=event=>{event.preventDefault();this.start();};$('estimate-btn').onclick=()=>this.estimate();$('refresh').onclick=()=>this.refreshCatalog();
    $('use-saved').onclick=()=>this.useSaved();
    for(const key of ['x','y','time'])$(key).oninput=()=>{this.cursor[`${key}_index`]=Number($(key).value);this.loadViewSoon();};
    $('product').onchange=()=>{this.product=$('product').value;this.loadViewSoon();};
    $('ceiling').oninput=()=>{const value=Number($('ceiling').value);if(value>0&&Number.isFinite(value)){this.ceiling=value;this.draw();}};
    $('quadrature').onchange=()=>this.draw();$('fit').onclick=()=>{if(!this.view)return;this.ceiling=Math.max(1e-12,...this.view.ascan.envelope.map(Math.abs))*1.04;$('ceiling').value=this.ceiling;this.draw();};
    $('gate-form').onsubmit=event=>{event.preventDefault();if(!$('gate-form').reportValidity())return;this.gate={start_us:Number($('gate-start').value),end_us:Number($('gate-end').value),mode:$('gate-mode').value};this.loadViewSoon();};
    for(const id of ['xy','cscan','xt','yt','ascan']){$(id).onclick=event=>this.pick(id,event);$(id).onkeydown=event=>this.keyboard(id,event);}
    new ResizeObserver(()=>this.draw()).observe(document.querySelector('.cv-inspector'));
  }
  message(text,error=false){$('status').textContent=text;$('status').classList.toggle('error',error);}
  open(options={}){
    if(!$('dialog').open)$('dialog').showModal();
    if(!this.snapshot||options.roiMm)this.capture(options);
    else {const current=this.getSnapshot();if(JSON.stringify(current?.twin)!==JSON.stringify(this.snapshot.twin)||JSON.stringify(current?.settings?.roi_mm)!==JSON.stringify(this.snapshot.settings?.roi_mm))this.message('The current specimen or preview ROI differs from this draft. Use current specimen & ROI to capture it explicitly. Saved data is unchanged.');}
    this.refreshCatalog();requestAnimationFrame(()=>this.draw());
  }
  capture(options={}){
    const source=this.getSnapshot();if(!source?.twin){this.message('Load a specimen before creating a causal volume.',true);return;}
    this.snapshot=structuredClone(source);const s=this.snapshot.settings||{},roi=options.roiMm||s.roi_mm||[0,0,...this.snapshot.twin.size_mm.slice(0,2)];
    for(const [key,value] of Object.entries({x0:roi[0],y0:roi[1],x1:roi[2],y1:roi[3]}))$(key).value=value;
    $('include_defects').checked=options.includeDefects??s.include_defects!==false;
    this.patchCenter=options.patchCenterMm||null;
    if(!this.patchCenter){const assembly=this.snapshot.twin.hbm_assemblies?.find(a=>a.microstructure?.enabled);if(assembly){const [ox,oy]=assembly.microstructure.center_offset_xy_um;this.patchCenter=[assembly.center_xy_mm[0]+ox/1000,assembly.center_xy_mm[1]+oy/1000];}}
    $('patch-preset').hidden=!this.patchCenter;
    $('snapshot').textContent=`Frozen ${this.snapshot.twin.name}. Full ${fmt(this.snapshot.twin.size_mm[2])} mm thickness; ${this.snapshot.twin.hbm_assemblies?.length||0} HBM sites retained. Excitation controls are independent of the preview.`;
    this.changed();this.message('Current specimen and ROI captured. Review the independent causal settings before estimating.');
  }
  applyPatchPreset(){
    if(!this.patchCenter)return;const [x,y]=this.patchCenter,[sx,sy]=this.snapshot.twin.size_mm;
    const roi=[x-.075,y-.125,x+.075,y+.125];if(roi[0]<0||roi[1]<0||roi[2]>sx||roi[3]>sy){this.message('The proposed patch ROI extends outside this specimen. Edit bounds explicitly.',true);return;}
    for(const [key,value] of Object.entries({x0:roi[0],y0:roi[1],x1:roi[2],y1:roi[3],scan_nx:32,scan_ny:64}))$(key).value=value;
    this.changed();this.message('Applied the explicit 0.15 × 0.25 mm patch ROI with 32 × 64 positions. Gamma and time controls are unchanged.');
  }
  acquisition(){return {path_model:'continuous_columns_v1',observation_model:'independent_columns_v1',...Object.fromEntries(numericFields.map(key=>[key,Number($(key).value)])),roi_mm:['x0','y0','x1','y1'].map(key=>Number($(key).value)),include_defects:$('include_defects').checked};}
  payload(){if(!this.snapshot)throw new Error('Capture a specimen first.');return {kind:KIND,twin:this.snapshot.twin,acquisition:this.acquisition()};}
  changed(){
    this.estimateController?.abort();this.estimateKey=null;$('start').disabled=true;$('estimate').textContent='Draft changed. Estimate resources to review the new request.';$('estimate-details').hidden=true;
    const a=this.acquisition(),[x0,y0,x1,y1]=a.roi_mm;
    $('pitch').textContent=`XY pitch ${fmt((x1-x0)*1000/a.scan_nx)} × ${fmt((y1-y0)*1000/a.scan_ny)} µm. Sampling pitch is not spatial resolution.`;
    $('sampling').textContent=`Required rate ≥ ${fmt(8*a.center_frequency_mhz)} MHz; at most 2,049 actual saved time centers. Record end ${fmt(a.record_start_us+a.record_duration_us)} µs (maximum 12). Values are never raised automatically.`;
  }
  async estimate(){
    if(!$('form').reportValidity())return;let payload;try{payload=this.payload();}catch(error){this.message(error.message,true);return;}
    const key=JSON.stringify(payload);this.estimateController?.abort();const controller=this.estimateController=new AbortController();this.estimateKey=null;$('start').disabled=true;$('estimate').textContent='Preparing the complete column and resource estimate…';
    try{const estimate=await this.request('/api/v2/estimate',payload,controller.signal);if(controller!==this.estimateController||key!==JSON.stringify(this.payload()))return;
      this.estimateKey=key;this.estimateResult=estimate;$('estimate').textContent=`${estimate.shape.join(' × ')} samples (y, x, time). Three float64 signals ${bytes(estimate.signal_bytes)}; bound/class maps ${bytes(estimate.map_bytes)}. Total ${bytes(estimate.total_bytes)}; peak workspace ${bytes(estimate.estimated_peak_bytes)}.${estimate.unique_stack_count!=null?` ${estimate.unique_stack_count} exact stack classes.`:''}${estimate.free_disk_bytes!=null?` Disk available ${bytes(estimate.free_disk_bytes)}.`:''} Numerical acceptance is checked during synthesis.`;
      $('estimate-json').textContent=JSON.stringify(estimate,null,2);$('estimate-details').hidden=false;$('start').disabled=this.starting===true;this.message('Causal resource estimate ready. The full specimen and this request will be frozen on submission.');
    }catch(error){if(error.name!=='AbortError'&&controller===this.estimateController){$('estimate').textContent=error.message;this.message(`Estimate rejected: ${error.message}`,true);}}
  }
  async start(){
    if(this.starting||!$('form').reportValidity())return;if(this.estimateKey!==JSON.stringify(this.payload())){await this.estimate();if(this.estimateKey!==JSON.stringify(this.payload()))return;}
    this.starting=true;$('start').disabled=true;$('start').textContent='Submitting…';
    try{const job=await this.request('/api/v2/jobs',this.payload());this.pendingDataset=job.dataset_id;$('acquire-panel').open=false;this.message('Causal volume queued. Inputs and numerical acceptance policy are frozen.');await this.refreshCatalog();}
    catch(error){this.message(`Acquisition could not start: ${error.message}`,true);}
    finally{this.starting=false;$('start').disabled=!this.estimateKey;$('start').textContent='Start causal volume';}
  }
  async refreshCatalog(){
    clearTimeout(this.pollTimer);this.catalogController?.abort();const controller=this.catalogController=new AbortController();
    try{const [jobs,datasets]=await Promise.all([this.request('/api/v2/jobs',null,controller.signal),this.request('/api/v2/datasets',null,controller.signal)]);if(controller!==this.catalogController)return;
      this.jobs=jobs.jobs.filter(item=>item.kind===KIND);this.datasets=datasets.datasets.filter(item=>item.kind===KIND);this.renderCatalog();
      const finished=this.datasets.find(item=>identity(item)===this.pendingDataset&&isComplete(item));
      if(finished){this.pendingDataset=null;await this.selectDataset(identity(finished));}
      else if(!this.manifest&&!this.selecting){let saved;try{saved=localStorage.getItem('vm-last-causal-volume');}catch{}const chosen=this.datasets.find(item=>isComplete(item)&&identity(item)===saved)||this.datasets.find(isComplete);if(chosen)await this.selectDataset(identity(chosen));}
    }catch(error){if(error.name!=='AbortError')this.message(`Causal catalog unavailable: ${error.message}`,true);}
    finally{if($('dialog').open&&controller===this.catalogController)this.pollTimer=setTimeout(()=>this.refreshCatalog(),this.jobs.some(j=>isActive(j.status))?1000:6000);}
  }
  renderCatalog(){
    const jobSignature=JSON.stringify(this.jobs);if(jobSignature!==this.jobsSignature){this.jobsSignature=jobSignature;$('jobs').replaceChildren();
      if(!this.jobs.length)$('jobs').textContent='No causal jobs yet.';
      for(const job of [...this.jobs].sort((a,b)=>String(b.created_at).localeCompare(a.created_at)).slice(0,8)){
        const row=document.createElement('div');row.className='cv-job';row.dataset.jobId=job.id;const title=document.createElement('strong');title.textContent=`${job.status.replaceAll('_',' ')} · ${job.completed_rows||0}/${job.total_rows||'—'} committed rows`;row.append(title);
        const detail=document.createElement('p');detail.className='cv-hint';detail.textContent=[job.status==='running'&&!job.completed_rows?'Preparing geometry and causal responses; no rows committed':job.phase,job.error,`Job ${job.id.slice(0,8)}`].filter(Boolean).join(' · ');row.append(detail);
        const progress=document.createElement('progress');progress.max=job.total_rows||1;progress.value=job.completed_rows||0;progress.setAttribute('aria-label',`Committed rows for ${job.id}`);row.append(progress);
        const action=isActive(job.status)?'cancel':['cancelled','canceled','failed','interrupted'].includes(job.status)?'resume':null;
        if(action){const button=document.createElement('button');button.className='small';button.dataset.action=action;button.textContent=action==='cancel'?'Cancel acquisition':'Resume frozen acquisition';button.onclick=async()=>{button.disabled=true;try{const result=await this.request(`/api/v2/jobs/${job.id}/${action}`,{});if(action==='resume')this.pendingDataset=result.dataset_id;this.message(action==='cancel'?'Cancellation requested. The active bounded solve may finish before the next checkpoint; committed rows remain saved.':'Resuming the exact frozen acquisition and its accepted numerical policy.');await this.refreshCatalog();}catch(error){this.message(error.message,true);button.disabled=false;}};row.append(button);}$('jobs').append(row);
      }
    }
    const signature=JSON.stringify([this.datasets,this.manifest&&identity(this.manifest)]);if(signature===this.datasetsSignature)return;this.datasetsSignature=signature;$('datasets').replaceChildren();
    if(!this.datasets.length)$('datasets').textContent='No saved causal volumes. Estimate a bounded ROI above.';
    for(const item of [...this.datasets].sort((a,b)=>String(b.created_at).localeCompare(a.created_at))){const button=document.createElement('button');button.className='cv-dataset';button.dataset.datasetId=identity(item);button.disabled=!isComplete(item);button.setAttribute('aria-pressed',String(identity(item)===identity(this.manifest||{})));const name=document.createElement('strong');name.textContent=item.name||'Causal volume';const detail=document.createElement('span');detail.textContent=`${(item.shape||[]).join(' × ')} · ${item.state||item.status} · ${identity(item).slice(0,8)}`;button.append(name,detail);button.onclick=()=>this.selectDataset(identity(item));$('datasets').append(button);}
  }
  async selectDataset(id){
    this.viewController?.abort();this.selectionController?.abort();const controller=this.selectionController=new AbortController();this.selecting=true;this.message('Opening frozen causal dataset…');
    try{const manifest=await this.request(`/api/v2/datasets/${id}`,null,controller.signal);if(controller!==this.selectionController||!$('dialog').open)return;if(manifest.kind!==KIND||!isComplete(manifest))throw new Error('Only a completed causal volume can open here.');
      this.manifest=manifest;this.view=null;$('viewer').hidden=true;const [ny,nx,nt]=manifest.shape;this.cursor={x_index:Math.floor(nx/2),y_index:Math.floor(ny/2),time_index:Math.floor(nt/4)};const range=manifest.time_range_us;this.gate={start_us:range[0],end_us:range[1],mode:'peak_envelope'};
      for(const [key,max] of [['x',nx-1],['y',ny-1],['time',nt-1]])$(key).max=max;
      $('gate-start').value=range[0];$('gate-end').value=range[1];$('gate-mode').value='peak_envelope';$('dataset-name').textContent=manifest.name;const a=manifest.acquisition||manifest.request.acquisition;$('dataset-detail').textContent=`${manifest.shape.join(' × ')} (y, x, time), three float64 pressure products. Saved gamma: ${fmt(a.center_frequency_mhz)} MHz, bandwidth ${fmt(a.fractional_bandwidth)}, order ${a.gamma_order}; ${fmt(a.sample_rate_mhz)} MHz sampling, ${a.precision_bits}-bit arithmetic. ${new Date(manifest.created_at).toLocaleString()}.`;
      $('export').href=`/api/v2/datasets/${id}/export`;$('provenance').querySelector('pre').textContent=JSON.stringify({dataset_id:id,request:manifest.request,acquisition:manifest.acquisition,metadata:manifest.metadata,solver:manifest.solver},null,2);
      $('empty').hidden=true;try{localStorage.setItem('vm-last-causal-volume',id);}catch{}this.renderCatalog();this.selecting=false;await this.loadView();
    }catch(error){if(error.name!=='AbortError')this.message(`Could not open causal volume: ${error.message}`,true);}
    finally{if(controller===this.selectionController)this.selecting=false;}
  }
  useSaved(){
    const request=this.manifest?.request;if(!request?.twin||!request.acquisition){this.message('The frozen request is unavailable for drafting.',true);return;}
    this.snapshot={twin:structuredClone(request.twin),settings:{}};const a=request.acquisition;for(const key of numericFields)$(key).value=a[key];$('include_defects').checked=a.include_defects;
    const roi=a.roi_mm||[0,0,...request.twin.size_mm.slice(0,2)];for(const [key,value] of Object.entries({x0:roi[0],y0:roi[1],x1:roi[2],y1:roi[3]}))$(key).value=value;
    this.patchCenter=null;$('patch-preset').hidden=true;$('snapshot').textContent=`Draft from saved dataset ${identity(this.manifest)}; frozen ${request.twin.name}. Current specimen edits are not substituted.`;$('acquire-panel').open=true;this.changed();this.message('Saved settings staged as a new draft. The existing saved arrays remain in the viewer.');
  }
  loadViewSoon(){if(this.selecting)return;this.viewController?.abort();clearTimeout(this.viewTimer);this.viewTimer=setTimeout(()=>this.loadView(),90);}
  async loadView(){
    if(!this.manifest||!this.gate||!$('dialog').open)return;this.viewController?.abort();const controller=this.viewController=new AbortController(),id=identity(this.manifest),query=new URLSearchParams({...this.cursor,product:this.product,gate_start_us:this.gate.start_us,gate_end_us:this.gate.end_us,gate_mode:this.gate.mode});
    $('view-note').textContent='Reading saved float64 slices and certificate…';this.message('Reading saved causal slices and certificate…');
    try{const view=await this.request(`/api/v2/causal-datasets/${id}/view?${query}`,null,controller.signal);if(controller!==this.viewController||id!==identity(this.manifest))return;
      this.view={...view,product:this.product};$('viewer').hidden=false;this.cursor={x_index:view.cursor.x_index,y_index:view.cursor.y_index,time_index:view.cursor.time_index};for(const key of ['x','y','time']){$(key).value=this.cursor[`${key}_index`];$(`${key}-value`).textContent=`${fmt(view.cursor[`${key}_${key==='time'?'us':'mm'}`])} ${key==='time'?'µs':'mm'}`;}
      $('xy-caption').textContent=`${productLabel(this.product)} · ${fmt(view.cursor.time_us)} µs`;$('xt-caption').textContent=`y ${fmt(view.cursor.y_mm)} mm`;$('yt-caption').textContent=`x ${fmt(view.cursor.x_mm)} mm`;$('cscan-caption').textContent=`${view.gate.mode==='rms_rf'?'RMS signed RF':'Peak complex magnitude'} · saved ${fmt(view.gate.actual_start_us??view.gate.start_us)}–${fmt(view.gate.actual_end_us??view.gate.end_us)} µs`;
      $('view-note').textContent=`Saved data only; sections retain every recorded time center. ${this.product==='envelope'?'Magnitude scale 0':'Signed scale −'+fmt(this.ceiling)} to ${fmt(this.ceiling)}; colors clip at the ceiling. Axes scale independently.`;
      this.renderCertificate();this.message('Saved causal volume ready. Cursor, gates and display settings do not run propagation.');this.draw();
    }catch(error){if(error.name==='AbortError'||controller!==this.viewController)return;this.message(`Saved view rejected: ${error.message}`,true);$('view-note').textContent='Previous accepted view retained; requested cursor, product or gate was not applied.';}
  }
  renderCertificate(){
    const c=this.view.cursor,cert=this.view.certificate||{},record=cert.diagnostics||cert.selected||{},d=record.diagnostics||record,bound=c.error_bound??this.view.ascan.error_bound??cert.selected_bound;
    const request=this.manifest.request?.acquisition||this.manifest.acquisition,tolerance=cert.requested_tolerance??request.absolute_tolerance;
    $('bound-summary').textContent=`Selected column bound ${fmt(bound)}; requested tolerance ${fmt(tolerance)}.${cert.volume_max_bound!=null?` Volume maximum ${fmt(cert.volume_max_bound)}.`:''} Gamma peak delay ${fmt(d.gamma_peak_us)} µs after excitation onset; surface reference ${fmt(d.surface_time_us)} µs. These are distinct from interface depth.`;
    Object.assign($('bound-summary').dataset,{bound,tolerance,classIndex:c.class_index??cert.class_index??''});
    $('bound-table').replaceChildren();const table=document.createElement('table');for(const [label,value] of [['Analytic aliasing',d.analytic_alias_bound],['Frequency cutoff',d.frequency_cutoff_bound],['Complex-pressure arithmetic',d.arithmetic_complex_bound],['Envelope arithmetic',d.arithmetic_envelope_bound],['Saved total',d.total_error_bound??bound],['Requested tolerance',tolerance],['Arithmetic precision (bits)',d.precision_bits??request.precision_bits],['Frequency terms',d.frequency_terms]]){const row=table.insertRow();row.insertCell().textContent=label;row.insertCell().textContent=fmt(value);}$('bound-table').append(table);$('certificate-json').textContent=JSON.stringify(cert,null,2);
  }
  draw(){
    if(!this.view||!$('dialog').open)return;const v=this.view,c=v.cursor,product=v.product||this.product;
    $('view-note').textContent=`Saved data only; each section display row uses the nearest saved time center, with no pooled values. ${product==='envelope'?'Magnitude maps: 0':'Signed maps: −'+fmt(this.ceiling)} to ${fmt(this.ceiling)}. Gate map: 0 to ${fmt(this.ceiling)}; trace: ±${fmt(this.ceiling)}. Display limits clip colors and curves. Axes scale independently.`;
    for(const key of ['xy','cscan','xt','yt'])drawCausalMap($(key),v[key],key==='xy'||key==='cscan'?['x (mm)','y (mm)']:[key==='xt'?'x (mm)':'y (mm)','Time (µs)'],key==='xy'||key==='cscan'?[c.x_mm,c.y_mm]:[key==='xt'?c.x_mm:c.y_mm,c.time_us],this.ceiling,key==='cscan'?'envelope':product);
    const a=v.ascan,curves=[{values:a.rf,color:'#2864d7'},{values:a.envelope,color:'#368470'}];if($('quadrature').checked)curves.push({values:a.imaginary,color:'#8560aa',dashed:true});
    drawLayeredCurve($('ascan'),a.time_us,curves,{range:[-this.ceiling,this.ceiling],labelX:'Recorded time (µs)',labelY:'Relative pressure',cursor:c.time_us});
    const i=c.time_index;$('readout').textContent=`x ${fmt(c.x_mm)} mm, y ${fmt(c.y_mm)} mm, t ${fmt(c.time_us)} µs: signed RF ${fmt(a.rf[i])}, imaginary ${fmt(a.imaginary[i])}, complex magnitude ${fmt(a.envelope[i])}. Display limits leave these saved values unchanged.`;
    Object.assign($('readout').dataset,{xIndex:c.x_index,yIndex:c.y_index,timeIndex:i,xMm:c.x_mm,yMm:c.y_mm,timeUs:c.time_us,rf:a.rf[i],imaginary:a.imaginary[i],envelope:a.envelope[i]});
  }
  pick(id,event){
    if(!this.view)return;const canvas=$(id),p=id==='ascan'?canvas._layeredPlot:canvas._causalPlot;if(!p)return;const rect=canvas.getBoundingClientRect(),u=(event.clientX-rect.left-p.left)/p.width,v=(event.clientY-rect.top-p.top)/p.height;if(u<0||u>1||v<0||v>1)return;
    const [ny,nx,nt]=this.manifest.shape,at=(f,n)=>Math.min(n-1,Math.max(0,Math.floor(f*n)));
    if(id==='ascan')this.cursor.time_index=Math.round(u*(nt-1));else if(id==='xy'||id==='cscan'){this.cursor.x_index=at(u,nx);this.cursor.y_index=at(v,ny);}else{this.cursor[id==='xt'?'x_index':'y_index']=at(u,id==='xt'?nx:ny);this.cursor.time_index=Math.round(v*(nt-1));}this.loadViewSoon();
  }
  keyboard(id,event){
    if(!this.manifest||!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key))return;event.preventDefault();const horizontal=['ArrowLeft','ArrowRight'].includes(event.key),key=id==='ascan'?'time_index':id==='xy'||id==='cscan'?(horizontal?'x_index':'y_index'):horizontal?(id==='xt'?'x_index':'y_index'):'time_index',axis={x_index:1,y_index:0,time_index:2}[key],delta=['ArrowLeft','ArrowUp'].includes(event.key)?-1:1;
    this.cursor[key]=Math.min(this.manifest.shape[axis]-1,Math.max(0,this.cursor[key]+delta*(event.shiftKey?10:1)));this.loadViewSoon();
  }
}

function drawCausalMap(canvas,data,axes,cursor,ceiling,product){
  if(!data?.image?.length)return;const w=canvas.clientWidth,h=canvas.clientHeight;if(!w||!h)return;const ratio=Math.min(devicePixelRatio||1,2);canvas.width=w*ratio;canvas.height=h*ratio;const ctx=canvas.getContext('2d');ctx.scale(ratio,ratio);ctx.fillStyle='#f8fbfd';ctx.fillRect(0,0,w,h);
  const rows=data.image.length,cols=data.image[0].length,left=57,top=12,width=w-left-16,height=h-top-42,extent=data.extent_mm||data.extent,[x0,x1,y0,y1]=extent;
  const times=data.time_us,paintRows=times?.length===rows?Math.max(1,Math.ceil(height*ratio)):rows;
  const off=document.createElement('canvas');off.width=cols;off.height=paintRows;const small=off.getContext('2d'),pixels=small.createImageData(cols,paintRows);
  for(let y=0;y<paintRows;y++){let row=y;if(times?.length===rows){const time=y0+(y+.5)/paintRows*(y1-y0);let lo=0,hi=rows-1;while(lo<hi){const mid=Math.floor((lo+hi)/2);if(times[mid]<time)lo=mid+1;else hi=mid;}row=lo>0&&time-times[lo-1]<times[lo]-time?lo-1:lo;}
    for(let x=0;x<cols;x++){const value=data.image[row][x],i=(y*cols+x)*4,v=Math.max(-1,Math.min(1,value/ceiling));let color;if(!Number.isFinite(value))color=[210,215,222];else if(product==='envelope'){const a=Math.max(0,v);color=[19+236*a,39+205*a,68+131*a];}else{const a=Math.abs(v),end=v<0?[42,96,177]:[187,67,61];color=end.map(c=>246+(c-246)*a);}for(let c=0;c<3;c++)pixels.data[i+c]=color[c];pixels.data[i+3]=255;}}
  small.putImageData(pixels,0,0);ctx.imageSmoothingEnabled=false;
  ctx.drawImage(off,left,top,width,height);
  ctx.strokeStyle='#afc4d6';ctx.strokeRect(left,top,width,height);ctx.font='10px "Segoe UI",sans-serif';ctx.fillStyle='#536b83';
  for(let n=0;n<=3;n++){const f=n/3;ctx.textAlign='center';ctx.fillText(fmt(x0+(x1-x0)*f),left+width*f,top+height+16);ctx.textAlign='right';ctx.fillText(fmt(y0+(y1-y0)*f),left-6,top+height*f+3);}
  ctx.textAlign='center';ctx.fillText(axes[0],left+width/2,h-3);ctx.save();ctx.translate(11,top+height/2);ctx.rotate(-Math.PI/2);ctx.fillText(axes[1],0,0);ctx.restore();
  ctx.save();ctx.beginPath();ctx.rect(left,top,width,height);ctx.clip();ctx.strokeStyle=product==='envelope'?'#fff1ae':'#193c61';ctx.setLineDash([4,3]);const x=left+(cursor[0]-x0)/(x1-x0)*width,y=top+(cursor[1]-y0)/(y1-y0)*height;ctx.beginPath();ctx.moveTo(x,top);ctx.lineTo(x,top+height);ctx.moveTo(left,y);ctx.lineTo(left+width,y);ctx.stroke();ctx.restore();
  canvas._causalPlot={left,top,width,height};Object.assign(canvas.dataset,{rows,columns:cols,minimum:product==='envelope'?0:-ceiling,maximum:ceiling,product});
}
