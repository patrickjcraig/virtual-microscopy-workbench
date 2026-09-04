import './volumes.css';
import { ascanPlot } from './plots.js';

const $=selector=>document.querySelector(selector);
const humanBytes=value=>{const n=Number(value)||0;return n>=1073741824?`${(n/1073741824).toFixed(2)} GiB`:n>=1048576?`${(n/1048576).toFixed(1)} MiB`:`${(n/1024).toFixed(1)} KiB`;};
const decimal=value=>Number(value).toLocaleString(undefined,{maximumFractionDigits:4});
const labelNumber=(id,label,value,min,max,step='any')=>`<label for="vol-${id}">${label}<input id="vol-${id}" type="number" value="${value}" min="${min}" max="${max}" step="${step}" required></label>`;
const activeStatus=value=>['queued','running','cancelling','cancel_requested','pending','resuming'].includes(String(value).toLowerCase());
const complete=manifest=>manifest.complete===true || ['complete','completed'].includes(String(manifest.state || manifest.status).toLowerCase());

/** Saved RF acquisition has its own frozen specimen and settings, independent of preview scans. */
export class VolumeWorkspace {
  constructor({request,getSnapshot}) {
    Object.assign(this,{request,getSnapshot});this.jobs=[];this.datasets=[];this.ceiling=.5;this.cursor={x_index:0,y_index:0,time_index:0};this.gate=null;
    document.body.insertAdjacentHTML('beforeend',`
      <dialog id="vol-dialog" aria-labelledby="vol-title" aria-describedby="vol-intro">
        <div class="vol-header"><div><h2 id="vol-title">Saved acoustic volumes</h2><p id="vol-intro">Synthetic RF across x, y and recording time. Time is not reconstructed depth.</p></div><button id="vol-close" class="quiet" aria-label="Close saved volumes">✕</button></div>
        <div class="vol-layout">
          <aside class="vol-acquisition" aria-label="Volume acquisition and saved datasets">
            <details id="vol-acquire-panel" open><summary>New SAM volume</summary>
              <p id="vol-snapshot" class="vol-snapshot"></p><button id="vol-refresh-snapshot" class="small" type="button">Use current specimen & ROI</button>
              <form id="vol-form">
                <fieldset><legend>Spatial sampling</legend><div class="vol-fields">${labelNumber('scan_nx','X positions',64,16,256,1)}${labelNumber('scan_ny','Y positions',64,16,256,1)}<label for="vol-depth_samples">Geometry depth samples<select id="vol-depth_samples"><option>128</option><option>256</option><option selected>512</option><option>1024</option></select></label></div><p id="vol-pitch" class="vol-hint"></p></fieldset>
                <fieldset><legend>Transducer</legend><div class="vol-fields">${labelNumber('frequency_mhz','Frequency (MHz)',50,10,150)}${labelNumber('fractional_bandwidth','Fractional bandwidth',.5,.2,1)}${labelNumber('focus_mm','Focus depth (mm)',.5,0,6)}${labelNumber('water_standoff_mm','Water standoff (mm)',0,0,5)}</div></fieldset>
                <fieldset><legend>RF recording</legend><div class="vol-fields">${labelNumber('record_start_us','Record start (µs)',0,0,12)}${labelNumber('record_duration_us','Duration (µs)',2,.05,12)}${labelNumber('sample_rate_mhz','Sample rate (MHz)',400,80,2400)}</div><p id="vol-sampling-hint" class="vol-hint">At least eight time samples per carrier period.</p></fieldset>
                <div id="vol-estimate" class="vol-estimate" aria-live="polite">Review the acquisition settings to estimate storage.</div>
                <details id="vol-estimate-warnings" hidden><summary>Sampling and model notes</summary><ul></ul></details>
                <button id="vol-estimate-btn" class="small" type="button">Estimate resources</button><button id="vol-start" class="primary" type="submit" disabled>Start SAM volume</button>
              </form>
            </details>
            <section class="vol-jobs" aria-labelledby="vol-jobs-title"><h3 id="vol-jobs-title">Acquisition jobs</h3><div id="vol-jobs-list"><p class="vol-hint">No jobs yet.</p></div></section>
            <section class="vol-saved" aria-labelledby="vol-saved-title"><div class="vol-list-heading"><h3 id="vol-saved-title">Saved datasets</h3><button id="vol-refresh-list" class="quiet" aria-label="Refresh saved datasets">↻</button></div><div id="vol-datasets"><p class="vol-hint">Loading saved datasets…</p></div></section>
          </aside>
          <main class="vol-inspector">
            <div id="vol-message" class="vol-message" role="status" aria-live="polite">Saved volumes remain available after a page reload.</div>
            <div id="vol-empty" class="vol-empty"><svg viewBox="0 0 100 90" fill="none" aria-hidden="true"><path d="m15 31 35-19 35 19-35 19-35-19Zm0 14 35 19 35-19M15 59l35 19 35-19"/><path d="M50 12v66" stroke-dasharray="3 4"/></svg><h3>Keep the complete acoustic signal</h3><p>Start a volume with the current digital twin, or open a saved dataset. Move through recording time and change gates without running the propagation again.</p><p class="vol-hint">The third axis is time in microseconds. These are synthetic, uncalibrated relative amplitudes; they are not a reconstructed depth volume.</p></div>
            <div id="vol-viewer" hidden>
              <div class="vol-dataset-heading"><div><h3 id="vol-dataset-name"></h3><p id="vol-dataset-detail"></p></div><a id="vol-export" class="vol-download" href="#" download>Download Zarr archive</a></div>
              <div class="vol-cursors"><label for="vol-x">X position <output id="vol-x-value"></output><input id="vol-x" type="range" min="0" max="0" value="0" step="1"></label><label for="vol-y">Y position <output id="vol-y-value"></output><input id="vol-y" type="range" min="0" max="0" value="0" step="1"></label><label for="vol-time">Recording time <output id="vol-time-value"></output><input id="vol-time" type="range" min="0" max="0" value="0" step="1"></label></div>
              <div class="vol-processing"><form id="vol-gate-form"><label for="vol-gate-start">Gate start (µs)<input id="vol-gate-start" type="number" min="0" step="any" required></label><label for="vol-gate-end">Gate end (µs)<input id="vol-gate-end" type="number" min="0" step="any" required></label><label for="vol-gate-mode">Gate statistic<select id="vol-gate-mode"><option value="peak_envelope">Peak envelope</option><option value="rms_rf">RMS of signed RF</option></select></label><button id="vol-apply-gate" class="small" type="submit">Apply gate</button></form><label class="vol-window" for="vol-window">Display ceiling<select id="vol-window"><option value=".05">0.05</option><option value=".1">0.10</option><option value=".2">0.20</option><option value=".5" selected>0.50</option><option value="1">1.00</option></select></label></div>
              <p id="vol-view-note" class="vol-view-note">Gates read the saved RF/envelope arrays. They do not submit an acquisition.</p>
              <div class="vol-map-grid">
                <section class="vol-plot"><div><h4>XY envelope at time</h4><span id="vol-xy-caption"></span></div><canvas id="vol-xy" tabindex="0" role="img" aria-label="Saved acoustic envelope XY slice; click to select X and Y"></canvas></section>
                <section class="vol-plot"><div><h4>Gated C-scan</h4><span id="vol-cscan-caption"></span></div><canvas id="vol-cscan" tabindex="0" role="img" aria-label="C-scan calculated from saved data; click to select X and Y"></canvas></section>
                <section class="vol-plot"><div><h4>X–time section</h4><span id="vol-xt-caption"></span></div><canvas id="vol-xt" tabindex="0" role="img" aria-label="Saved envelope X versus time; click to select position and time"></canvas></section>
                <section class="vol-plot"><div><h4>Y–time section</h4><span id="vol-yt-caption"></span></div><canvas id="vol-yt" tabindex="0" role="img" aria-label="Saved envelope Y versus time; click to select position and time"></canvas></section>
              </div>
              <section class="vol-rf"><div><h4>Signed RF at the selected scan position</h4><span>Relative pressure</span></div><canvas id="vol-ascan" role="img" aria-label="Saved signed acoustic RF waveform with its envelope and processing gate"></canvas></section>
              <p id="vol-provenance" class="vol-hint"></p>
            </div>
          </main>
        </div>
      </dialog>`);
    $('#vol-close').addEventListener('click',()=>$('#vol-dialog').close());
    $('#vol-dialog').addEventListener('close',()=>{clearTimeout(this.pollTimer);clearTimeout(this.estimateTimer);clearTimeout(this.viewTimer);this.catalogController?.abort();this.viewController?.abort();this.estimateController?.abort();this.selectionController?.abort();});
    $('#vol-refresh-snapshot').addEventListener('click',()=>this.capture());
    $('#vol-form').addEventListener('input',()=>this.changed());
    $('#vol-form').addEventListener('submit',event=>{event.preventDefault();this.start();});
    $('#vol-estimate-btn').addEventListener('click',()=>this.estimate());
    $('#vol-refresh-list').addEventListener('click',()=>this.refreshCatalog());
    for(const [id,key] of [['x','x_index'],['y','y_index'],['time','time_index']])$(`#vol-${id}`).addEventListener('input',event=>{this.cursor[key]=Number(event.target.value);this.loadViewSoon();});
    $('#vol-gate-form').addEventListener('submit',event=>{event.preventDefault();this.applyGate();});
    $('#vol-window').addEventListener('change',event=>{this.ceiling=Number(event.target.value);this.draw();});
    for(const id of ['xy','cscan','xt','yt']){const canvas=$(`#vol-${id}`);canvas.addEventListener('click',event=>this.pick(id,event));canvas.addEventListener('keydown',event=>this.keyboard(id,event));}
    new ResizeObserver(()=>this.draw()).observe($('#vol-inspector') || $('.vol-inspector'));
  }
  message(text,error=false){$('#vol-message').textContent=text;$('#vol-message').classList.toggle('error',error);}
  open(){if($('#vol-dialog').open)return;$('#vol-dialog').showModal();this.capture();this.refreshCatalog();requestAnimationFrame(()=>this.draw());}
  capture(){
    const snapshot=this.getSnapshot();if(!snapshot?.twin){this.message('Load a specimen before starting a volume.',true);return;}
    this.snapshot=structuredClone(snapshot);const s=snapshot.settings || {};
    for(const [key,fallback] of [['frequency_mhz',50],['focus_mm',.5],['depth_samples',512]])$(`#vol-${key}`).value=s[key] ?? fallback;
    $('#vol-sample_rate_mhz').value=Math.max(400,8*(s.frequency_mhz || 50));$('#vol-focus_mm').max=snapshot.twin.size_mm[2];
    const roi=s.roi_mm || [0,0,...snapshot.twin.size_mm.slice(0,2)];
    $('#vol-snapshot').textContent=`${snapshot.twin.name}. ${s.roi_mm?'Selected ROI':'Full specimen'}: x ${decimal(roi[0])}–${decimal(roi[2])} mm, y ${decimal(roi[1])}–${decimal(roi[3])} mm. Embedded defects ${s.include_defects===false?'excluded':'included'}.`;
    this.changed();
  }
  acquisition(){const fields=['scan_nx','scan_ny','depth_samples','frequency_mhz','fractional_bandwidth','focus_mm','water_standoff_mm','record_start_us','record_duration_us','sample_rate_mhz'];return {...Object.fromEntries(fields.map(key=>[key,Number($(`#vol-${key}`).value)])),include_defects:this.snapshot.settings?.include_defects!==false,...(this.snapshot.settings?.roi_mm?{roi_mm:[...this.snapshot.settings.roi_mm]}:{})};}
  payload(){if(!this.snapshot)throw new Error('Load a specimen first.');return {twin:this.snapshot.twin,acquisition:this.acquisition()};}
  changed(){
    this.estimateKey=null;$('#vol-start').disabled=true;this.estimateController?.abort();clearTimeout(this.estimateTimer);
    const min=8*Number($('#vol-frequency_mhz').value);$('#vol-sample_rate_mhz').min=min;$('#vol-sampling-hint').textContent=`Sample rate must be at least ${decimal(min)} MHz for this carrier frequency.`;
    if(this.snapshot){const roi=this.snapshot.settings?.roi_mm || [0,0,...this.snapshot.twin.size_mm.slice(0,2)];$('#vol-pitch').textContent=`Stage pitch: ${decimal((roi[2]-roi[0])*1000/Number($('#vol-scan_nx').value))} × ${decimal((roi[3]-roi[1])*1000/Number($('#vol-scan_ny').value))} µm. Pitch is not instrument resolution.`;}
    $('#vol-estimate').textContent='Settings changed. Estimating resource use…';this.estimateTimer=setTimeout(()=>this.estimate(),350);
  }
  async estimate(){
    clearTimeout(this.estimateTimer);if(!this.snapshot || !$('#vol-dialog').open)return;
    if(!$('#vol-form').checkValidity()){$('#vol-estimate').textContent='Correct the acquisition fields to estimate resources.';return;}
    const payload=this.payload(),key=JSON.stringify(payload);this.estimateController?.abort();const controller=this.estimateController=new AbortController();$('#vol-start').disabled=true;
    try{const estimate=await this.request('/api/v2/estimate',payload,controller.signal);if(controller!==this.estimateController || key!==JSON.stringify(this.payload()))return;this.estimateKey=key;this.estimateResult=estimate;
      $('#vol-estimate').textContent=`${estimate.shape.join(' × ')} samples (y, x, time). RF ${humanBytes(estimate.rf_bytes)} + envelope ${humanBytes(estimate.envelope_bytes)}. Storage ${humanBytes(estimate.total_bytes)}; estimated peak memory ${humanBytes(estimate.estimated_peak_bytes)}.${estimate.free_disk_bytes!==undefined?` Disk available: ${humanBytes(estimate.free_disk_bytes)}.`:''}`;
      const notes=$('#vol-estimate-warnings');notes.hidden=!estimate.warnings?.length;notes.querySelector('ul').replaceChildren();for(const warning of estimate.warnings || []){const li=document.createElement('li');li.textContent=warning;notes.querySelector('ul').append(li);}
      $('#vol-start').disabled=this.starting===true;
    }catch(error){if(error.name==='AbortError'||controller!==this.estimateController)return;this.estimateKey=null;$('#vol-estimate').textContent=error.message;}
  }
  async start(){
    if(this.starting || !$('#vol-form').reportValidity())return;const payload=this.payload();if(this.estimateKey!==JSON.stringify(payload)){await this.estimate();if(this.estimateKey!==JSON.stringify(payload))return;}
    this.starting=true;$('#vol-start').disabled=true;$('#vol-start').textContent='Submitting…';
    try{const job=await this.request('/api/v2/jobs',payload);this.pendingDataset=job.dataset_id;$('#vol-acquire-panel').open=false;this.message('Volume submitted. The frozen specimen and acquisition settings are saved with the dataset.');await this.refreshCatalog();}
    catch(error){this.message(`Acquisition could not start: ${error.message}`,true);}
    finally{this.starting=false;$('#vol-start').disabled=!this.estimateKey;$('#vol-start').textContent='Start SAM volume';}
  }
  async refreshCatalog(){
    clearTimeout(this.pollTimer);this.catalogController?.abort();const controller=this.catalogController=new AbortController();
    try{const [jobs,datasets]=await Promise.all([this.request('/api/v2/jobs',null,controller.signal),this.request('/api/v2/datasets',null,controller.signal)]);if(controller!==this.catalogController)return;
      this.jobs=jobs.jobs.filter(item=>item.kind==='sam_rf_volume'||!item.kind);this.datasets=datasets.datasets.filter(item=>item.kind==='sam_rf_volume'||!item.kind);this.renderJobs();this.renderDatasets();
      const completed=this.datasets.find(item=>item.dataset_id===this.pendingDataset && complete(item));
      if(completed){this.pendingDataset=null;await this.selectDataset(completed.dataset_id || completed.id);}
      else if(!this.manifest){let saved;try{saved=localStorage.getItem('virtual-microscopy-last-volume');}catch{}const chosen=this.datasets.find(item=>complete(item)&&(item.dataset_id || item.id)===saved) || this.datasets.find(complete);if(chosen)await this.selectDataset(chosen.dataset_id || chosen.id);}
    }catch(error){if(error.name!=='AbortError')this.message(`Saved volume catalog unavailable: ${error.message}`,true);}
    finally{if($('#vol-dialog').open && controller===this.catalogController)this.pollTimer=setTimeout(()=>this.refreshCatalog(),this.jobs.some(job=>activeStatus(job.status))?1200:6000);}
  }
  renderJobs(){
    const list=$('#vol-jobs-list'),signature=JSON.stringify(this.jobs);if(signature===this.jobsSignature)return;this.jobsSignature=signature;list.replaceChildren();
    if(!this.jobs.length){const p=document.createElement('p');p.className='vol-hint';p.textContent='No acquisition jobs yet.';list.append(p);return;}
    for(const job of [...this.jobs].sort((a,b)=>String(b.created_at || '').localeCompare(a.created_at || '')).slice(0,8)){const row=document.createElement('div');row.className='vol-job';row.dataset.jobId=job.id;const title=document.createElement('strong');title.textContent=`${String(job.status).replaceAll('_',' ')} · ${job.completed_rows || 0}/${job.total_rows || '—'} rows`;const progress=document.createElement('progress');progress.max=job.total_rows || 1;progress.value=job.completed_rows || 0;progress.setAttribute('aria-label',`Job ${job.id} progress`);row.append(title,progress);const id=document.createElement('span');id.className='vol-hint';id.textContent=job.id.slice(0,12);row.append(id);
      if(job.error){const text=document.createElement('p');text.className='vol-hint';text.textContent=job.error;row.append(text);}
      const status=String(job.status).toLowerCase();const action=activeStatus(status)?'cancel':['cancelled','canceled','failed','interrupted'].includes(status)?'resume':null;
      if(action){const button=document.createElement('button');button.className='small';button.textContent=action==='cancel'?'Cancel acquisition':'Resume acquisition';button.dataset.action=action;button.addEventListener('click',async()=>{button.disabled=true;try{const result=await this.request(`/api/v2/jobs/${encodeURIComponent(job.id)}/${action}`,{});if(action==='resume')this.pendingDataset=result.dataset_id;this.message(action==='cancel'?'Cancellation requested. Completed rows remain saved.':'Resuming the saved acquisition.');await this.refreshCatalog();}catch(error){this.message(error.message,true);button.disabled=false;}});row.append(button);}list.append(row);}
  }
  renderDatasets(){
    const list=$('#vol-datasets'),signature=JSON.stringify([this.datasets,this.manifest?.dataset_id || this.manifest?.id]);if(signature===this.datasetsSignature)return;this.datasetsSignature=signature;list.replaceChildren();
    if(!this.datasets.length){const p=document.createElement('p');p.className='vol-hint';p.textContent='No saved volumes. Acquire a volume above to retain every RF trace.';list.append(p);return;}
    for(const manifest of [...this.datasets].sort((a,b)=>String(b.created_at || '').localeCompare(a.created_at || ''))){const button=document.createElement('button'),id=manifest.dataset_id || manifest.id;button.className='vol-dataset';button.dataset.datasetId=id;button.disabled=!complete(manifest);button.setAttribute('aria-pressed',String(id===(this.manifest?.dataset_id || this.manifest?.id)));const name=document.createElement('strong');name.textContent=manifest.name || 'Acoustic volume';const metadata=document.createElement('span');metadata.textContent=`${(manifest.shape || []).join(' × ')} · ${manifest.state || manifest.status || (complete(manifest)?'complete':'incomplete')}`;const date=document.createElement('span');date.textContent=`${new Date(manifest.created_at).toLocaleString()} · ${String(id).slice(0,8)}`;button.append(name,metadata,date);button.addEventListener('click',()=>this.selectDataset(id));list.append(button);}
  }
  async selectDataset(id){
    this.viewController?.abort();this.selectionController?.abort();const controller=this.selectionController=new AbortController();
    this.selecting=true;$('#vol-view-note').textContent='Opening saved dataset…';for(const key of ['x','y','time','apply-gate'])$(`#vol-${key}`).disabled=true;
    try{const manifest=await this.request(`/api/v2/datasets/${encodeURIComponent(id)}`,null,controller.signal);if(controller!==this.selectionController || !$('#vol-dialog').open)return;if(!complete(manifest)){this.message('This dataset is incomplete. Resume its acquisition to inspect the completed volume.');return;}
      this.manifest=manifest;this.view=null;for(const canvas of $('#vol-viewer').querySelectorAll('canvas')){canvas.getContext('2d').clearRect(0,0,canvas.width,canvas.height);delete canvas.dataset.rows;delete canvas.dataset.columns;canvas._volumePlot=null;}const shape=manifest.shape;this.cursor={x_index:Math.floor(shape[1]/2),y_index:Math.floor(shape[0]/2),time_index:Math.floor(shape[2]/4)};
      const range=manifest.time_range_us || [manifest.acquisition.record_start_us,manifest.acquisition.record_start_us+manifest.acquisition.record_duration_us];this.gate={start_us:range[0],end_us:range[1],mode:'peak_envelope'};
      $('#vol-gate-start').value=this.gate.start_us;$('#vol-gate-end').value=this.gate.end_us;$('#vol-gate-mode').value=this.gate.mode;
      for(const [name,axis] of [['x',1],['y',0],['time',2]])$(`#vol-${name}`).max=shape[axis]-1;
      $('#vol-dataset-name').textContent=manifest.name;$('#vol-dataset-detail').textContent=`${shape.join(' × ')} samples · y, x, time · saved ${new Date(manifest.created_at).toLocaleString()}`;
      $('#vol-provenance').textContent=`Dataset ${id}. Synthetic, uncalibrated relative amplitudes. The frozen specimen and acquisition recipe are included in the archive. The source RF is retained; gates and display windows change only the derived view. Time is not depth.`;
      $('#vol-export').href=`/api/v2/datasets/${encodeURIComponent(id)}/export`;
      $('#vol-empty').hidden=true;$('#vol-viewer').hidden=false;try{localStorage.setItem('virtual-microscopy-last-volume',id);}catch{}
      this.renderDatasets();this.selecting=false;await this.loadView();
    }catch(error){if(error.name!=='AbortError')this.message(`Could not open dataset: ${error.message}`,true);}
    finally{if(controller===this.selectionController){this.selecting=false;for(const key of ['x','y','time','apply-gate'])$(`#vol-${key}`).disabled=false;}}
  }
  applyGate(){const start=Number($('#vol-gate-start').value),end=Number($('#vol-gate-end').value);if(!Number.isFinite(start)||!Number.isFinite(end)||end<=start){this.message('Gate end must be greater than gate start.',true);return;}this.gate={start_us:start,end_us:end,mode:$('#vol-gate-mode').value};this.loadViewSoon();}
  loadViewSoon(){if(this.selecting)return;this.viewController?.abort();clearTimeout(this.viewTimer);this.viewTimer=setTimeout(()=>this.loadView(),80);}
  async loadView(){
    if(!this.manifest || !this.gate || !$('#vol-dialog').open)return;this.viewController?.abort();const controller=this.viewController=new AbortController(),id=this.manifest.dataset_id || this.manifest.id;
    const query=new URLSearchParams({...this.cursor,gate_start_us:this.gate.start_us,gate_end_us:this.gate.end_us,gate_mode:this.gate.mode});$('#vol-view-note').textContent='Reading slices from saved arrays…';
    try{const view=await this.request(`/api/v2/datasets/${encodeURIComponent(id)}/view?${query}`,null,controller.signal);if(controller!==this.viewController || id!==(this.manifest?.dataset_id || this.manifest?.id))return;this.view=view;this.cursor={x_index:view.cursor.x_index,y_index:view.cursor.y_index,time_index:view.cursor.time_index};
      for(const [name,key] of [['x','x_index'],['y','y_index'],['time','time_index']])$(`#vol-${name}`).value=this.cursor[key];
      $('#vol-x-value').textContent=`${decimal(view.cursor.x_mm)} mm`;$('#vol-y-value').textContent=`${decimal(view.cursor.y_mm)} mm`;$('#vol-time-value').textContent=`${decimal(view.cursor.time_us)} µs`;
      $('#vol-xy-caption').textContent=`t = ${decimal(view.cursor.time_us)} µs`;$('#vol-cscan-caption').textContent=`${view.gate.mode==='rms_rf'?'RMS RF':'Peak envelope'} · ${decimal(view.gate.start_us)}–${decimal(view.gate.end_us)} µs`;
      $('#vol-xt-caption').textContent=`y = ${decimal(view.cursor.y_mm)} mm`;$('#vol-yt-caption').textContent=`x = ${decimal(view.cursor.x_mm)} mm`;
      $('#vol-view-note').textContent='Reading saved data only. Gates and cursor changes do not run propagation. Colors clip at the display ceiling; stored values are unchanged.';this.message('Saved volume ready. Select a position or recording time to inspect the retained RF.');this.draw();
    }catch(error){if(error.name==='AbortError'||controller!==this.viewController)return;this.message(`Slice read failed: ${error.message}`,true);$('#vol-view-note').textContent='The previous view is retained; requested processing settings were not applied.';}
  }
  draw(){if(!this.view || !$('#vol-dialog').open)return;const c=this.view.cursor;
    for(const id of ['xy','cscan'])drawMap($(`#vol-${id}`),this.view[id],['x (mm)','y (mm)'],[c.x_mm,c.y_mm],this.ceiling);
    drawMap($('#vol-xt'),this.view.xt,['x (mm)','Time (µs)'],[c.x_mm,c.time_us],this.ceiling);drawMap($('#vol-yt'),this.view.yt,['y (mm)','Time (µs)'],[c.y_mm,c.time_us],this.ceiling);
    ascanPlot($('#vol-ascan'),this.view.ascan,{gate_start_us:this.view.gate.start_us,gate_end_us:this.view.gate.end_us});
  }
  pick(id,event){const canvas=$(`#vol-${id}`),plot=canvas._volumePlot;if(!plot||!this.manifest)return;const rect=canvas.getBoundingClientRect(),u=(event.clientX-rect.left-plot.left)/plot.width,v=(event.clientY-rect.top-plot.top)/plot.height;if(u<0||u>1||v<0||v>1)return;const shape=this.manifest.shape,at=(f,n)=>Math.min(n-1,Math.floor(f*n));if(id==='xy'||id==='cscan'){this.cursor.x_index=at(u,shape[1]);this.cursor.y_index=at(v,shape[0]);}else{this.cursor[id==='xt'?'x_index':'y_index']=at(u,shape[id==='xt'?1:0]);this.cursor.time_index=at(v,shape[2]);}this.loadViewSoon();}
  keyboard(id,event){if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key)||!this.manifest)return;event.preventDefault();const horizontal=event.key==='ArrowLeft'||event.key==='ArrowRight',key=(id==='xy'||id==='cscan')?(horizontal?'x_index':'y_index'):horizontal?(id==='xt'?'x_index':'y_index'):'time_index';const axis=key==='x_index'?1:key==='y_index'?0:2,delta=(event.key==='ArrowLeft'||event.key==='ArrowUp'?-1:1)*(event.shiftKey?10:1);this.cursor[key]=Math.max(0,Math.min(this.manifest.shape[axis]-1,this.cursor[key]+delta));this.loadViewSoon();}
}

function drawMap(canvas,data,axes,cursor,ceiling){
  const width=canvas.clientWidth,height=canvas.clientHeight;if(!width||!height||!data?.image?.length)return;const dpr=Math.min(devicePixelRatio||1,2);canvas.width=width*dpr;canvas.height=height*dpr;const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);ctx.fillStyle='#f8fbfd';ctx.fillRect(0,0,width,height);
  const rows=data.image.length,cols=data.image[0].length,left=51,top=12,w=width-left-18,h=height-top-37,extent=data.extent_mm || data.extent,[u0,u1,v0,v1]=extent;
  const bins=data.time_bin_edges_us,pooled=bins?.length===rows+1,paintRows=pooled?Math.max(1,Math.ceil(h*dpr)):rows;
  const off=document.createElement('canvas');off.width=cols;off.height=paintRows;const small=off.getContext('2d'),pixels=small.createImageData(cols,paintRows);
  const colors=[[19,39,68],[29,97,129],[55,167,177],[172,219,177],[255,244,199]];
  let first=0;
  for(let y=0;y<paintRows;y++){
    let last=y;if(pooled){const t0=v0+y/paintRows*(v1-v0),t1=v0+(y+1)/paintRows*(v1-v0);while(first<rows-1&&bins[first+1]<=t0)first++;last=first;while(last<rows-1&&bins[last+1]<t1)last++;}else first=y;
    for(let x=0;x<cols;x++){let value=0;for(let row=first;row<=last;row++)value=Math.max(value,Math.abs(data.image[row][x]));const v=Math.max(0,Math.min(1,value/ceiling))*4,lo=Math.min(3,Math.floor(v)),f=v-lo,i=(y*cols+x)*4;for(let c=0;c<3;c++)pixels.data[i+c]=Number.isFinite(value)?Math.round(colors[lo][c]+f*(colors[lo+1][c]-colors[lo][c])):210;pixels.data[i+3]=255;}
  }
  small.putImageData(pixels,0,0);ctx.imageSmoothingEnabled=false;ctx.drawImage(off,left,top,w,h);
  ctx.strokeStyle='#acbecf';ctx.strokeRect(left,top,w,h);canvas._volumePlot={left,top,width:w,height:h};ctx.font='10px "Segoe UI",sans-serif';ctx.fillStyle='#536b83';
  for(let n=0;n<=4;n++){const f=n/4;ctx.textAlign='center';ctx.fillText(decimal(u0+(u1-u0)*f),left+w*f,top+h+16);ctx.textAlign='right';ctx.fillText(decimal(v0+(v1-v0)*f),left-6,top+h*f+3);}
  ctx.textAlign='center';ctx.fillText(axes[0],left+w/2,height-3);ctx.save();ctx.translate(11,top+h/2);ctx.rotate(-Math.PI/2);ctx.fillText(axes[1],0,0);ctx.restore();
  if(cursor){const x=left+(cursor[0]-u0)/(u1-u0)*w,y=top+(cursor[1]-v0)/(v1-v0)*h;ctx.save();ctx.beginPath();ctx.rect(left,top,w,h);ctx.clip();ctx.strokeStyle='#fff1ae';ctx.lineWidth=1;ctx.setLineDash([4,3]);ctx.beginPath();ctx.moveTo(x,top);ctx.lineTo(x,top+h);ctx.moveTo(left,y);ctx.lineTo(left+w,y);ctx.stroke();ctx.restore();}
  canvas.dataset.rows=rows;canvas.dataset.columns=cols;
}
