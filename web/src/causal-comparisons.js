import './causal-comparisons.css';
import {drawComparisonMap,drawComparisonTrace} from './comparison-plots.js';

const API='/api/v2/causal-comparisons',KIND='sam_causal_rf_volume';
const $=id=>document.getElementById(`cc-${id}`);
const identity=item=>item.dataset_id||item.report_id||item.comparison_id||item.id;
const fmt=value=>value==null?'—':typeof value!=='number'?String(value):Math.abs(value)>0&&(Math.abs(value)<.001||Math.abs(value)>=1e6)?value.toExponential(5):value.toLocaleString(undefined,{maximumFractionDigits:7});
const label=key=>({rf:'Signed real pressure',imaginary:'Imaginary quadrature',envelope:'Saved complex magnitude'}[key]||key.replaceAll('_',' '));
const complete=item=>item.complete===true||['complete','completed'].includes(item.state||item.status);
const sides=['reference','candidate','difference'];
const sideLabel=['A · reference','B · candidate','B − A · signed difference'];
const colors=['#2766ba','#a85926','#675b9b'];
const rangeOf=arrays=>{let min=Infinity,max=-Infinity;for(const array of arrays)for(const value of array.flat(Infinity))if(Number.isFinite(value)){min=Math.min(min,value);max=Math.max(max,value);}return min===Infinity?[0,0]:[min,max];};
const visualRange=range=>range[0]===range[1]?[range[0]-1e-12,range[1]+1e-12]:range;
const symmetric=range=>{const peak=Math.max(Math.abs(range[0]),Math.abs(range[1]));return [-peak,peak];};
const numberField=(id,title,value)=>`<label for="cc-${id}">${title}<input id="cc-${id}" type="number" step="any" value="${value}" required></label>`;
const plot=(id,title)=>`<section class="cc-plot"><header><h4>${title}</h4><span id="cc-${id}-caption"></span></header><canvas id="cc-${id}" role="img" tabindex="0" aria-label="${title}; select the saved coordinate"></canvas></section>`;

// A/B alignment is the visual anchor: identical map frames, explicit shared
// ranges, and a separate symmetric residual. The data are never normalized.
export class CausalComparisonWorkspace {
  constructor({request}) {
    this.request=request;this.datasets=[];this.reports=[];this.sourceManifests=new Map();this.product='rf';this.gateProduct='peak_envelope';this.cursor={x_index:0,y_index:0,time_index:0};
    document.body.insertAdjacentHTML('beforeend',`
      <dialog id="cc-dialog" aria-labelledby="cc-title">
        <header class="cc-header"><div><h2 id="cc-title">Causal comparisons</h2><p>Compare saved independent-column responses with the same excitation.</p></div><button id="cc-close" class="quiet" aria-label="Close causal comparisons">✕</button></header>
        <div class="cc-layout">
          <aside class="cc-history"><details id="cc-history-panel" open><summary>Immutable report history</summary><p class="cc-hint">Catalog order is descending report ID, not creation time. A new gate creates a new report.</p><div class="cc-actions"><button id="cc-refresh" class="small">Refresh catalog</button><button id="cc-more" class="small" hidden>Load more</button></div><div id="cc-reports"></div></details></aside>
          <main class="cc-inspector">
            <p id="cc-status" class="cc-status" role="status" aria-live="polite">Select two completed causal volumes. Comparison reads saved samples; it does not run propagation.</p>
            <details id="cc-setup" open><summary>Sources, compatibility and new report gate</summary>
              <form id="cc-form"><div class="cc-sources"><label for="cc-reference">A · chosen reference<select id="cc-reference" required><option value="">Select a saved causal volume</option></select><span id="cc-reference-note" class="cc-hint"></span></label><label for="cc-candidate">B · candidate<select id="cc-candidate" required><option value="">Select a saved causal volume</option></select><span id="cc-candidate-note" class="cc-hint"></span></label></div>
                <div class="cc-fields"><label for="cc-name">Report name (optional)<input id="cc-name" maxlength="160" placeholder="Describe this comparison"></label>${numberField('gate-start','Gate start (µs)',.32)}${numberField('gate-end','Gate end (µs)',.4)}<button id="cc-create" type="submit" class="primary">Create saved comparison</button></div>
                <details class="cc-initial-inputs"><summary>Initial saved cursor (optional zero-based indices)</summary><div class="cc-fields">${['x','y','time'].map(key=>`<label for="cc-initial-${key}">${key.toUpperCase()} index<input id="cc-initial-${key}" type="number" min="0" max="${key==='time'?2048:63}" step="1" placeholder="Source midpoint"></label>`).join('')}</div></details>
                <p class="cc-hint">Fixed policy: same excitation, identical saved coordinates and supported model semantics. No alignment, resampling, phase rotation or normalization. Numerical precision and specimen inputs may differ; every difference is retained. A is a baseline, not measured truth. Gate bounds are never adjusted automatically.</p>
              </form>
            </details>
            <section id="cc-empty" class="cc-empty"><h3>Inspect the residual, retain its evidence.</h3><p>Compare real pressure, imaginary quadrature and saved complex magnitude on aligned XY maps and recording-time traces. Source certificates and subtraction arithmetic remain separate from ordinary summary statistics.</p></section>
            <section id="cc-result" hidden>
              <div class="cc-heading"><div><h3 id="cc-report-name"></h3><p id="cc-source-summary" class="cc-hint"></p></div><div class="cc-actions"><a id="cc-json" download>JSON report</a><a id="cc-csv" download>CSV report</a><button id="cc-use-pair" class="small">Use pair & gate as draft</button></div></div>
              <p id="cc-report-summary" class="cc-model-note"></p>
              <div class="cc-cursors">${['x','y','time'].map(key=>`<label for="cc-${key}">${key==='time'?'Recorded time':key.toUpperCase()} <output id="cc-${key}-value"></output><input id="cc-${key}" type="range" min="0" max="0" step="1" value="0"></label>`).join('')}</div>
              <div class="cc-controls"><label for="cc-product">Signal<select id="cc-product"><option value="rf">Signed real pressure</option><option value="imaginary">Imaginary quadrature</option><option value="envelope">Saved complex magnitude</option></select></label><label for="cc-shared-ceiling">A/B amplitude ceiling<input id="cc-shared-ceiling" type="number" min="1e-300" step="any" value="1"></label><label for="cc-difference-ceiling">Difference ± ceiling<input id="cc-difference-ceiling" type="number" min="1e-300" step="any" value="1"></label><button id="cc-fit" class="small">Fit current saved view</button><button id="cc-initial" class="small">Restore frozen initial view</button></div>
              <p id="cc-scale-note" class="cc-hint"></p>
              <div class="cc-map-grid">${sides.map((side,i)=>plot(`xy-${side}`,sideLabel[i])).join('')}</div>
              <div class="cc-trace-grid">${plot('trace-pair','A and B · shared scale')}${plot('trace-difference','B − A · signed difference')}</div>
              <p id="cc-readout" class="cc-readout"></p>
              <section class="cc-cert"><div><h4>Selected column residual bound</h4><p id="cc-bound-scope" class="cc-hint">Uniform over retained time samples of the declared scalar model. Bounds are absolute, not relative. Display values are rounded; exports retain the authoritative float64 values.</p></div><div id="cc-bounds"></div><p class="cc-hint">Source sum and subtraction arithmetic are rounded outward separately; the published total encloses their sum. Complex residual and difference of saved magnitudes have separate bounds. Neither certifies gate reductions, summary metrics, material accuracy, spatial resolution or physical detectability.</p></section>
              <details id="cc-gate-panel"><summary>Saved gate maps and ordinary statistics</summary><p id="cc-gate-note" class="cc-hint"></p><label class="cc-gate-select" for="cc-gate-product">Gate statistic<select id="cc-gate-product"><option value="peak_envelope">Peak complex magnitude</option><option value="rms_rf">RMS real pressure</option></select></label><p id="cc-gate-scale" class="cc-hint"></p><div class="cc-map-grid">${sides.map((side,i)=>plot(`gate-${side}`,`${sideLabel[i]} · gate`)).join('')}</div><div class="cc-metrics-grid"><section><h4>Full recorded time · ordinary diagnostics</h4><div id="cc-metrics-full"></div></section><section><h4>Selected gate · ordinary diagnostics</h4><div id="cc-metrics-gate"></div></section></div></details>
              <details id="cc-evidence"><summary>Compatibility, changed inputs and source identities</summary><p id="cc-warnings" class="cc-hint"></p><p class="cc-hint">Local stack class numbers are not shared identities across sources. A changed descriptive hash does not, by itself, identify a physical defect. Repeated echoes have no unique depth.</p><div class="cc-actions"><a id="cc-source-a" download>A · source Zarr ZIP</a><a id="cc-source-b" download>B · source Zarr ZIP</a></div><pre id="cc-evidence-json"></pre></details>
            </section>
          </main>
        </div>
      </dialog>`);
    $('close').onclick=()=>$('dialog').close();
    $('dialog').addEventListener('close',()=>{clearTimeout(this.viewTimer);this.selectionController?.abort();this.viewController?.abort();this.catalogController?.abort();});
    $('form').onsubmit=event=>{event.preventDefault();this.create();};
    for(const side of ['reference','candidate'])$(side).onchange=()=>this.sourceNote(side);
    $('refresh').onclick=()=>this.refresh();$('more').onclick=()=>this.refresh(true);
    $('use-pair').onclick=()=>this.stageReport();$('initial').onclick=()=>this.restoreInitial();
    for(const key of ['x','y','time'])$(key).oninput=()=>{this.cursor[`${key}_index`]=Number($(key).value);this.loadViewSoon();};
    $('product').onchange=()=>{this.product=$('product').value;this.fit();};$('gate-product').onchange=()=>{this.gateProduct=$('gate-product').value;this.draw();};
    for(const key of ['shared-ceiling','difference-ceiling'])$(key).oninput=()=>this.draw();$('fit').onclick=()=>this.fit();
    for(const side of sides)for(const prefix of ['xy','gate']){const id=`${prefix}-${side}`;$(id).onclick=event=>this.pick(id,event);$(id).onkeydown=event=>this.keyboard(id,event);}
    for(const id of ['trace-pair','trace-difference']){$(id).onclick=event=>this.pick(id,event);$(id).onkeydown=event=>this.keyboard(id,event);}
    $('gate-panel').ontoggle=()=>this.draw();new ResizeObserver(()=>this.draw()).observe(document.querySelector('.cc-inspector'));
  }
  message(text,error=false){$('status').textContent=text;$('status').classList.toggle('error',error);}
  async open(options={}){
    if(!$('dialog').open)$('dialog').showModal();if(options.datasetId)this.message('Reading the saved source catalog before staging this volume as A…');await this.refresh();
    if(options.datasetId){if(this.datasets.some(item=>identity(item)===options.datasetId)){$('reference').value=options.datasetId;this.sourceNote('reference');$('setup').open=true;this.message('Saved volume staged as A. Choose B and review the gate; no comparison has been created.');}}
    else if(options.reportId)await this.openReport(options.reportId);
    else if(!this.report){let id;try{id=localStorage.getItem('vm-last-causal-comparison');}catch{}if(id)await this.openReport(id);}
    requestAnimationFrame(()=>this.draw());
  }
  async refresh(append=false){
    this.catalogController?.abort();const controller=this.catalogController=new AbortController();
    try{const query=`?limit=20&offset=${append?this.nextOffset||0:0}`;
      const [catalog,history]=await Promise.all([this.request('/api/v2/datasets',null,controller.signal),this.request(API+query,null,controller.signal)]);if(controller!==this.catalogController)return;
      this.datasets=catalog.datasets.filter(item=>item.kind===KIND&&complete(item));
      for(const side of ['reference','candidate']){const select=$(side),old=select.value;select.replaceChildren(new Option('Select a saved causal volume',''));for(const item of this.datasets)select.add(new Option(`${item.name||'Causal volume'} · ${identity(item).slice(0,8)}`,identity(item)));select.value=old;this.sourceNote(side);}
      this.reports=append?[...this.reports,...history.comparisons]:history.comparisons;this.nextOffset=history.next_offset??null;$('more').hidden=this.nextOffset==null;this.renderHistory();
    }catch(error){if(error.name!=='AbortError')this.message(`Catalog unavailable: ${error.message}. An already opened report remains available.`,true);}
  }
  async sourceNote(side){
    let item=this.datasets.find(item=>identity(item)===$(side).value);if(!item){$(`${side}-note`).textContent='';return;}const id=identity(item);
    try{if(item.acquisition?.center_frequency_mhz==null){$(`${side}-note`).textContent='Reading the frozen excitation and recording…';if(!this.sourceManifests.has(id))this.sourceManifests.set(id,this.request(`/api/v2/datasets/${id}`));item=await this.sourceManifests.get(id);if($(side).value!==id)return;}
      const a=item.acquisition||item.request?.acquisition||{};$(`${side}-note`).textContent=`${item.shape?.join(' × ')||'Saved'} (y, x, time); ${fmt(a.center_frequency_mhz)} MHz carrier, bandwidth ${fmt(a.fractional_bandwidth)}, order ${fmt(a.gamma_order)}, ${fmt(a.sample_rate_mhz)} MHz sampling. ${fmt(a.precision_bits)} bits; tolerance ${fmt(a.absolute_tolerance)}. Recording ${item.time_range_us?.map(fmt).join('–')||'from frozen source'} µs.`;
    }catch(error){this.sourceManifests.delete(id);if($(side).value===id)$(`${side}-note`).textContent=`Frozen source metadata unavailable: ${error.message}. Compatibility is checked on submission.`;}
  }
  renderHistory(){
    $('reports').replaceChildren();if(!this.reports.length)$('reports').textContent='No saved comparisons yet.';
    for(const item of this.reports){const button=document.createElement('button');button.className='cc-report';button.dataset.reportId=identity(item);button.setAttribute('aria-pressed',String(identity(item)===identity(this.report||{})));const title=document.createElement('strong');title.textContent=item.name||'Saved causal comparison';const text=document.createElement('span');text.textContent=`${identity(item)}${item.created_at?` · ${new Date(item.created_at).toLocaleString()}`:''}`;button.append(title,text);button.onclick=()=>this.openReport(identity(item));$('reports').append(button);}
  }
  payload(){return {name:$('name').value.trim()||undefined,reference_dataset_id:$('reference').value,candidate_dataset_id:$('candidate').value,gate_start_us:Number($('gate-start').value),gate_end_us:Number($('gate-end').value),policy:'same_excitation_v1',...Object.fromEntries(['x','y','time'].filter(key=>$(`initial-${key}`).value!=='').map(key=>[`${key}_index`,Number($(`initial-${key}`).value)]))};}
  async create(){
    if(this.creating||!$('form').reportValidity())return;this.creating=true;$('create').disabled=true;this.selectionController?.abort();this.viewController?.abort();clearTimeout(this.viewTimer);const generation=this.generation=(this.generation||0)+1;
    const payload=this.payload();this.message('Verifying saved sources and computing the comparison. No propagation job is started.');
    try{const report=await this.request(API,payload);if(generation!==this.generation)return;this.acceptReport(report);await this.refresh();}
    catch(error){if(generation===this.generation)this.message(`Comparison rejected: ${error.message}. Inputs and the previous saved report are retained.`,true);}
    finally{this.creating=false;$('create').disabled=false;}
  }
  async openReport(id){
    this.selectionController?.abort();this.viewController?.abort();clearTimeout(this.viewTimer);const controller=this.selectionController=new AbortController(),generation=this.generation=(this.generation||0)+1;this.message('Reading immutable comparison and its frozen initial snapshot…');
    try{const report=await this.request(`${API}/${id}`,null,controller.signal);if(generation!==this.generation)return;this.acceptReport(report);}
    catch(error){if(error.name!=='AbortError'&&generation===this.generation)this.message(`Report unavailable: ${error.message}`,true);}
  }
  acceptReport(report){
    if(!report.initial_view)throw new Error('The report has no retained initial snapshot.');
    this.report=report;this.view=report.initial_view;this.cursor={...this.view.cursor};this.snapshot=true;
    $('empty').hidden=true;$('result').hidden=false;$('setup').open=false;
    if(innerWidth<=800)$('history-panel').open=false;
    $('report-name').textContent=report.name||report.request?.name||'Saved causal comparison';$('result').dataset.reportId=identity(report);
    const summaries=report.source_summaries||this.view.source_summaries||{},a=summaries.reference||{},b=summaries.candidate||{};
    $('source-summary').textContent=`A: ${a.name||'Reference'} (${(a.dataset_id||report.request.reference_dataset_id).slice(0,8)}) · B: ${b.name||'Candidate'} (${(b.dataset_id||report.request.candidate_dataset_id).slice(0,8)}). Frozen sources; current specimen edits are unrelated.`;
    const gate=report.gate||this.view.gate,pulse=a.acquisition||{};$('report-summary').textContent=`Same-excitation comparison: ${fmt(pulse.center_frequency_mhz)} MHz gamma carrier, bandwidth ${fmt(pulse.fractional_bandwidth)}, order ${fmt(pulse.gamma_order)}. ${this.view.shape.join(' × ')} saved samples (y, x, time). Gate requested ${fmt(gate.requested_start_us??report.request.gate_start_us)}–${fmt(gate.requested_end_us??report.request.gate_end_us)} µs; selected ${fmt(gate.actual_start_us??gate.start_us)}–${fmt(gate.actual_end_us??gate.end_us)} µs (${fmt(gate.sample_count)} centers). B − A is a model residual, not a detection claim.`;
    for(const format of ['json','csv'])$(format).href=`${API}/${identity(report)}/export?format=${format}`;
    $('source-a').href=`/api/v2/datasets/${report.request.reference_dataset_id}/export`;$('source-b').href=`/api/v2/datasets/${report.request.candidate_dataset_id}/export`;
    $('gate-note').textContent=`Inclusive saved-time gate. ${report.gate_maps?.scope||this.view.gate_maps?.scope||'Gate products and summary statistics are ordinary floating reductions, without an additional numerical certificate.'} Repeated echoes do not map to unique depths.`;
    $('warnings').textContent=(report.warnings||[]).map(v=>typeof v==='string'?v:JSON.stringify(v)).join(' ');
    $('evidence-json').textContent=JSON.stringify({report_id:identity(report),created_at:report.created_at,request:report.request,compatibility:report.compatibility,input_differences:report.input_differences||report.differences,source_summaries:summaries,bound_definition:report.bounds?.definition,arithmetic_policy:report.bounds?.arithmetic_policy,formulas:report.formulas,resources:report.resource_estimate||report.resources},null,2);
    this.renderMetrics('metrics-full',report.metrics?.full_record);this.renderMetrics('metrics-gate',report.metrics?.gate);
    try{localStorage.setItem('vm-last-causal-comparison',identity(report));}catch{}
    this.updateCursor();this.fit();this.renderHistory();this.message('Saved comparison ready · frozen initial snapshot. Cursor changes read verified saved sources; display changes remain local.');
  }
  stageReport(){if(!this.report)return;const r=this.report.request;for(const side of ['reference','candidate']){$(side).value=r[`${side}_dataset_id`];this.sourceNote(side);}$('gate-start').value=r.gate_start_us;$('gate-end').value=r.gate_end_us;$('name').value=this.report.name||r.name||'';for(const key of ['x','y','time'])$(`initial-${key}`).value=r[`${key}_index`]??'';$('setup').open=true;this.message('The saved pair and gate are staged as a new draft. Creating a comparison publishes another immutable report.');}
  restoreInitial(){if(!this.report)return;this.selectionController?.abort();this.generation=(this.generation||0)+1;this.viewController?.abort();clearTimeout(this.viewTimer);this.view=this.report.initial_view;this.cursor={...this.view.cursor};this.snapshot=true;this.updateCursor();this.draw();this.message('Saved comparison ready · frozen initial snapshot restored. This snapshot remains readable without source datasets.');}
  loadViewSoon(){clearTimeout(this.viewTimer);this.viewController?.abort();this.viewTimer=setTimeout(()=>this.loadView(),100);}
  async loadView(){
    if(!this.report)return;const id=identity(this.report),cursor={...this.cursor},controller=this.viewController=new AbortController();this.message('Reading synchronized saved-source samples…');
    try{const v=await this.request(`${API}/${id}/view?${new URLSearchParams({x_index:cursor.x_index,y_index:cursor.y_index,time_index:cursor.time_index})}`,null,controller.signal);if(controller!==this.viewController||id!==identity(this.report))return;this.view=v;this.cursor={...v.cursor};this.snapshot=false;this.updateCursor();this.draw();this.message('Saved comparison ready · verified source selection. No model runs or report changes.');}
    catch(error){if(error.name!=='AbortError'&&controller===this.viewController){this.cursor={...this.view.cursor};this.updateCursor();this.message(`Source selection unavailable: ${error.message}. The last accepted ${this.snapshot?'frozen snapshot':'saved-source view'} is retained; Restore frozen initial view works without sources.`,true);}}
  }
  updateCursor(){const v=this.view;if(!v)return;for(const [key,size] of [['x',v.shape[1]],['y',v.shape[0]],['time',v.shape[2]]]){$(key).max=size-1;$(key).value=v.cursor[`${key}_index`];$(`${key}-value`).textContent=`${fmt(v.cursor[key==='time'?'time_us':`${key}_mm`])} ${key==='time'?'µs':'mm'} · ${v.cursor[`${key}_index`]+1}/${size}`;}}
  fit(){if(!this.view)return;const p=this.product,v=this.view,ab=rangeOf([v.xy.reference[p],v.xy.candidate[p],v.traces.reference[p],v.traces.candidate[p]]),d=rangeOf([v.xy.difference[p],v.traces.difference[p]]);$('shared-ceiling').value=Math.max(1e-12,Math.abs(ab[0]),Math.abs(ab[1]));$('difference-ceiling').value=Math.max(1e-12,Math.abs(d[0]),Math.abs(d[1]));this.draw();}
  draw(){
    if(!this.view||!$('dialog').open)return;const v=this.view,p=this.product,c=v.cursor,shared=Number($('shared-ceiling').value),diff=Number($('difference-ceiling').value);if(!(shared>0&&diff>0&&Number.isFinite(shared)&&Number.isFinite(diff)))return;
    const abRange=p==='envelope'?[0,shared]:[-shared,shared],diffRange=[-diff,diff];
    $('scale-note').textContent=`${label(p)} in relative incident-pressure units. A/B shared limits ${fmt(abRange[0])} to ${fmt(abRange[1])}; B − A limits ${fmt(-diff)} to ${fmt(diff)}. Display clipping only; no amplitude normalization. XY axes scale independently.${p==='envelope'?' Coherent complex magnitude, not a Hilbert envelope of the real trace.':''}`;
    for(const [i,side] of sides.entries()){drawComparisonMap($(`xy-${side}`),v.xy[side][p],v.extent_mm,i===2?diffRange:abRange,i===2||p!=='envelope',c);$(`xy-${side}-caption`).textContent=`${fmt(c.time_us)} µs`;}
    const gate={start_us:v.gate.actual_start_us??v.gate.start_us,end_us:v.gate.actual_end_us??v.gate.end_us};
    drawComparisonTrace($('trace-pair'),v.coordinates.time_us,[v.traces.reference[p],v.traces.candidate[p]],abRange,c.time_us,gate,colors);
    drawComparisonTrace($('trace-difference'),v.coordinates.time_us,[v.traces.difference[p]],diffRange,c.time_us,gate,[colors[2]]);
    $('trace-pair-caption').textContent=`${label(p)} · blue A / ochre B`;$('trace-difference-caption').textContent=`${label(p)} · purple B − A`;
    const t=c.time_index,values=Object.fromEntries(sides.map(side=>[side,Object.fromEntries(['rf','imaginary','envelope'].map(key=>[key,v.traces[side][key][t]]))]));
    $('readout').textContent=`x ${fmt(c.x_mm)} mm · y ${fmt(c.y_mm)} mm · t ${fmt(c.time_us)} µs. ${sides.map((side,i)=>`${['A','B','B − A'][i]}: real ${fmt(values[side].rf)}, imaginary ${fmt(values[side].imaginary)}, ${i===2?'difference of saved magnitudes':'magnitude'} ${fmt(values[side].envelope)}`).join(' | ')}. Difference of magnitudes is not the magnitude of the complex residual.`;
    Object.assign($('readout').dataset,{...Object.fromEntries(Object.entries(c).map(([k,value])=>[k.replace(/_([a-z])/g,(_,x)=>x.toUpperCase()),value])),differenceRf:values.difference.rf,differenceImaginary:values.difference.imaginary,differenceEnvelope:values.difference.envelope,product:p,snapshot:this.snapshot});
    this.renderBounds(v.selected_bounds);
    const maps=v.gate_maps[this.gateProduct],sourceRange=rangeOf([maps.reference,maps.candidate]),residualRange=symmetric(rangeOf([maps.difference]));
    $('gate-scale').textContent=`A/B shared limits ${fmt(sourceRange[0])} to ${fmt(sourceRange[1])}; gate-map difference ${fmt(residualRange[0])} to ${fmt(residualRange[1])}. Degenerate all-zero limits receive a display epsilon only.`;
    for(const [i,side] of sides.entries())drawComparisonMap($(`gate-${side}`),maps[side],v.extent_mm,visualRange(i===2?residualRange:sourceRange),i===2,c);
  }
  renderBounds(bounds){
    const rows=[['source_sum','Source A + B'],['complex_arithmetic','Complex subtraction arithmetic'],['complex_total','Total complex residual bound'],['envelope_arithmetic','Saved-magnitude subtraction arithmetic'],['envelope_total','Total difference-of-magnitudes bound']];
    $('bounds').replaceChildren();const table=document.createElement('table');table.setAttribute('aria-label','Selected residual numerical bounds');const body=document.createElement('tbody');for(const [key,title] of rows){const row=document.createElement('tr');row.dataset.bound=key;const name=document.createElement('th');name.scope='row';name.textContent=title;const cell=document.createElement('td');cell.textContent=fmt(bounds[key]);cell.title=Number(bounds[key]).toPrecision(17);cell.dataset.value=bounds[key];row.append(name,cell);body.append(row);}table.append(body);$('bounds').append(table);
  }
  renderMetrics(id,data){
    $(id).replaceChildren();if(!data)return;const table=document.createElement('table'),body=document.createElement('tbody');
    const add=(node,path=[])=>{for(const [key,value] of Object.entries(node||{})){if(value&&typeof value==='object'&&!Array.isArray(value)){add(value,[...path,label(key)]);continue;}const row=document.createElement('tr'),name=document.createElement('th'),cell=document.createElement('td');name.scope='row';name.textContent=[...path,label(key)].join(' / ');cell.textContent=Array.isArray(value)?value.map(fmt).join(', '):fmt(value);row.append(name,cell);body.append(row);}};
    add(data);table.append(body);$(id).append(table);
  }
  pick(id,event){if(!this.view)return;const canvas=$(id),p=canvas._comparisonPlot;if(!p)return;const rect=canvas.getBoundingClientRect(),u=(event.clientX-rect.left-p.left)/p.width,v=(event.clientY-rect.top-p.top)/p.height;if(u<0||u>1||v<0||v>1)return;const [ny,nx,nt]=this.view.shape;if(id.startsWith('trace'))this.cursor.time_index=Math.round(u*(nt-1));else{this.cursor.x_index=Math.min(nx-1,Math.floor(u*nx));this.cursor.y_index=Math.min(ny-1,Math.floor(v*ny));}this.loadViewSoon();}
  keyboard(id,event){if(!this.view||!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key))return;event.preventDefault();const key=id.startsWith('trace')?'time_index':['ArrowLeft','ArrowRight'].includes(event.key)?'x_index':'y_index',axis={x_index:1,y_index:0,time_index:2}[key],delta=['ArrowLeft','ArrowUp'].includes(event.key)?-1:1;this.cursor[key]=Math.max(0,Math.min(this.view.shape[axis]-1,this.cursor[key]+delta*(event.shiftKey?10:1)));this.loadViewSoon();}
}
