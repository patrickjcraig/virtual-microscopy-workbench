const $=selector=>document.querySelector(selector);
const parameters=['die_count','die_thickness_um','gap_um','base_thickness_um','cap_thickness_um'];

// This panel edits the twin, while the instrument keeps its previous acquisition snapshot.
export class HBMEditor {
  constructor({request,getTwin,onApply,onSelectROI}) {
    Object.assign(this,{request,getTwin,onApply,onSelectROI});this.sequence=0;this.section=null;
    document.body.insertAdjacentHTML('beforeend',`
      <dialog id="hbm-dialog" aria-labelledby="hbm-title" aria-describedby="hbm-intro">
        <div class="dialog-header"><div><h2 id="hbm-title">HBM assembly laboratory</h2><p id="hbm-intro">Six physical sites. Editable layers. Electrical state stored separately.</p></div><button id="hbm-close" class="quiet" aria-label="Close HBM editor">✕</button></div>
        <div class="hbm-layout">
          <form id="hbm-form" class="hbm-form">
            <div class="field"><label for="hbm-site">Physical site</label><select id="hbm-site"></select></div>
            <p id="hbm-evidence" class="hbm-evidence"></p>
            <div class="field"><label for="hbm-functional-state">Electrical state</label><select id="hbm-functional-state"><option value="enabled">Enabled</option><option value="disabled">Disabled</option><option value="unknown">Unknown</option></select><p class="gate-hint">Changing this state retains all physical material.</p></div>
            <div class="hbm-presets" aria-label="Stack presets"><button type="button" id="hbm-preset-8">8-high template</button><button type="button" id="hbm-preset-12">12-high template</button></div>
            <div class="hbm-fields">
              <div class="field"><label for="hbm-die_count">DRAM dies</label><select id="hbm-die_count"><option value="8">8 dies</option><option value="12">12 dies</option></select></div>
              <div class="field"><label for="hbm-die_thickness_um">Die thickness (µm)</label><input id="hbm-die_thickness_um" type="number" min="5" max="200" step="any" required/></div>
              <div class="field"><label for="hbm-gap_um">Inter-die gap (µm)</label><input id="hbm-gap_um" type="number" min="1" max="100" step="any" required/></div>
              <div class="field"><label for="hbm-base_thickness_um">Base die (µm)</label><input id="hbm-base_thickness_um" type="number" min="5" max="300" step="any" required/></div>
              <div class="field"><label for="hbm-cap_thickness_um">Mold cap (µm)</label><input id="hbm-cap_thickness_um" type="number" min="1" max="300" step="any" required/></div>
            </div>
            <div class="hbm-thickness"><span>Total stack thickness</span><output id="hbm-total-thickness">—</output></div>
            <p class="gate-hint">Die and gap dimensions are assumptions. Connections are homogenized here; bumps and TSVs are not individually resolved.</p>
            <p id="hbm-edit-status" class="hbm-message" role="status" aria-live="polite">Edit parameters, then apply to the digital twin.</p>
            <button id="hbm-apply" type="submit" class="primary">Apply stack changes</button>
          </form>
          <div class="hbm-inspection">
            <div class="hbm-section-heading"><div><h3>Material cross-section</h3><p>Applied geometry · depth axis expanded</p></div><div class="hbm-axis" aria-label="Cross-section axis"><button id="hbm-xz" aria-pressed="true">XZ</button><button id="hbm-yz" aria-pressed="false">YZ</button></div></div>
            <div class="hbm-section-wrap"><canvas id="hbm-section" role="img" aria-label="Material cross-section through selected HBM stack"></canvas><p id="hbm-section-loading" role="status">Loading material section…</p></div>
            <p id="hbm-section-description" class="hbm-section-description"></p><div id="hbm-materials" class="material-key"></div>
            <p id="hbm-section-warnings" class="gate-hint"></p>
            <button type="button" id="hbm-scan-selected" class="small">Set this stack as scan ROI</button>
            <details id="hbm-reference"><summary>Supplied X-ray cross-section reference</summary><p id="hbm-reference-status">Loading local reference image…</p><img id="hbm-reference-image" hidden alt="User-supplied X-ray cross-section showing stacked interconnect rows and larger package joints"/><p id="hbm-reference-scale" class="gate-hint"></p><p id="hbm-reference-source" class="gate-hint"></p></details>
          </div>
        </div>
      </dialog>`);
    $('#hbm-close').addEventListener('click',()=>$('#hbm-dialog').close());
    $('#hbm-dialog').addEventListener('close',()=>{this.sequence++;this.controller?.abort();this.sectionController?.abort();this.setBusy(false);});
    $('#hbm-site').addEventListener('change',()=>this.select($('#hbm-site').value));
    $('#hbm-form').addEventListener('submit',event=>{event.preventDefault();this.apply();});
    for(const count of [8,12])$(`#hbm-preset-${count}`).addEventListener('click',()=>{ $('#hbm-die_count').value=String(count);$('#hbm-die_thickness_um').value=count===8?'50':'34';$('#hbm-gap_um').value=count===8?'15':'8';$('#hbm-base_thickness_um').value='70';$('#hbm-cap_thickness_um').value='30';this.draftChanged(); });
    for(const id of [...parameters,'functional-state'])$(`#hbm-${id}`).addEventListener('input',()=>this.draftChanged());
    for(const axis of ['xz','yz'])$(`#hbm-${axis}`).addEventListener('click',()=>{this.axis=axis;this.loadSection();});
    $('#hbm-scan-selected').addEventListener('click',()=>{const selected=this.assembly();if(selected){this.onSelectROI(selected);$('#hbm-dialog').close();}});
    new ResizeObserver(()=>this.drawSection()).observe($('#hbm-section-wrap') || $('.hbm-section-wrap'));
  }
  assembly(){return this.getTwin()?.hbm_assemblies?.find(item=>item.id===$('#hbm-site').value);}
  setTwin(twin) {
    this.referenceLoaded=false;if(this.referenceURL)URL.revokeObjectURL(this.referenceURL);this.referenceURL=null;$('#hbm-reference-image').hidden=true;$('#hbm-reference-image').removeAttribute('src');
    const previous=$('#hbm-site').value;this.sequence++;this.controller?.abort();this.sectionController?.abort();
    $('#hbm-site').replaceChildren();
    for(const item of twin.hbm_assemblies || []){const option=document.createElement('option');option.value=item.id;option.textContent=item.name || item.id;$('#hbm-site').append(option);}
    if((twin.hbm_assemblies || []).some(item=>item.id===previous))$('#hbm-site').value=previous;
    if(!twin.hbm_assemblies?.length)$('#hbm-dialog').close();
    else if($('#hbm-dialog').open){this.select($('#hbm-site').value);this.loadReference();}
  }
  open(id) {if(!this.getTwin()?.hbm_assemblies?.length)return;$('#hbm-dialog').showModal();this.select(id || $('#hbm-site').value);this.loadReference();}
  select(id) {
    this.sequence++;this.controller?.abort();this.setBusy(false);$('#hbm-site').value=id;const assembly=this.assembly();if(!assembly)return;
    for(const key of parameters)$(`#hbm-${key}`).value=assembly[key];
    $('#hbm-functional-state').value=assembly.functional_state;this.axis ||= 'xz';
    $('#hbm-evidence').textContent=assembly.id==='hbm-6'?'Sixth physical site included. Its internal construction and electrical state remain uncertain; this layered stack is an assumed template.':'Physical layer geometry is an editable assumption, separate from published enabled-memory counts.';
    $('#hbm-edit-status').textContent='Applied parameters shown. Edit, then apply to the digital twin.';$('#hbm-edit-status').classList.remove('error');this.updateThickness();this.loadSection();
  }
  values(){return {...Object.fromEntries(parameters.map(key=>[key,Number($(`#hbm-${key}`).value)])),functional_state:$('#hbm-functional-state').value};}
  updateThickness(){const p=this.values(),height=p.die_count*p.die_thickness_um+p.die_count*p.gap_um+p.base_thickness_um+p.cap_thickness_um;$('#hbm-total-thickness').textContent=Number.isFinite(height)?`${height.toLocaleString()} µm`:'—';}
  draftChanged(){this.updateThickness();$('#hbm-edit-status').textContent='Unapplied changes. The cross-section still shows the applied twin.';$('#hbm-edit-status').classList.remove('error');}
  setBusy(value){$('#hbm-form').querySelectorAll('input,select,button').forEach(el=>el.disabled=value);$('#hbm-scan-selected').disabled=value;$('#hbm-apply').textContent=value?'Applying…':'Apply stack changes';}
  async apply() {
    if(!$('#hbm-form').reportValidity())return;const twin=this.getTwin(),assembly=this.assembly();if(!assembly)return;
    const sequence=++this.sequence;this.controller?.abort();this.controller=new AbortController();const controller=this.controller;this.setBusy(true);
    $('#hbm-edit-status').textContent='Validating layer geometry…';$('#hbm-edit-status').classList.remove('error');
    try {
      const result=await this.request('/api/hbm/compose',{twin,assembly_id:assembly.id,parameters:this.values()},this.controller.signal);
      if(sequence!==this.sequence || twin!==this.getTwin() || !$('#hbm-dialog').open)return;
      this.onApply(result.twin);
      $('#hbm-edit-status').textContent='Stack applied. Run an acquisition to update microscope images.';
      if(result.warnings?.length)$('#hbm-edit-status').textContent+=` ${result.warnings.join(' ')}`;
    }catch(error){if(error.name==='AbortError'||sequence!==this.sequence)return;$('#hbm-edit-status').textContent=`Changes rejected; current twin retained. ${error.message}`;$('#hbm-edit-status').classList.add('error');}
    finally{if(controller===this.controller && $('#hbm-dialog').open)this.setBusy(false);}
  }
  async loadSection() {
    const twin=this.getTwin(),assembly=this.assembly();if(!assembly||!$('#hbm-dialog').open)return;
    this.sectionController?.abort();this.sectionController=new AbortController();const controller=this.sectionController,axis=this.axis;
    this.section=null;this.drawSection();$('#hbm-section-loading').hidden=false;$('#hbm-section-loading').textContent='Sampling material geometry…';
    for(const item of ['xz','yz'])$(`#hbm-${item}`).setAttribute('aria-pressed',String(item===axis));
    try {
      const section=await this.request('/api/hbm/section',{twin,assembly_id:assembly.id,axis,resolution:512},controller.signal);
      if(controller!==this.sectionController || twin!==this.getTwin() || assembly.id!==this.assembly()?.id)return;
      this.section=section;$('#hbm-section-loading').hidden=true;this.drawSection();
      $('#hbm-section-description').textContent=`${axis.toUpperCase()} through ${assembly.name || assembly.id}; ${axis==='xz'?'y':'x'} = ${Number(section.fixed_coordinate_mm).toFixed(3)} mm. This is material geometry, not an X-ray reconstruction.`;
      $('#hbm-section-warnings').textContent=(section.warnings || []).join(' ');
      $('#hbm-materials').replaceChildren();for(const material of section.materials){const span=document.createElement('span');span.className='material-item';const dot=document.createElement('i');if(/^#[\da-f]{6}$/i.test(material.color))dot.style.background=material.color;span.append(dot,document.createTextNode(material.name || material.id));$('#hbm-materials').append(span);}
    }catch(error){if(error.name==='AbortError'||controller!==this.sectionController)return;$('#hbm-section-loading').textContent=`Section unavailable: ${error.message}`;}
  }
  drawSection() {
    const canvas=$('#hbm-section'),width=canvas.clientWidth,height=canvas.clientHeight;if(!width||!height)return;
    const dpr=Math.min(devicePixelRatio || 1,2);canvas.width=width*dpr;canvas.height=height*dpr;const context=canvas.getContext('2d');context.scale(dpr,dpr);context.fillStyle='#f6f9fc';context.fillRect(0,0,width,height);
    const section=this.section;if(!section?.image?.length)return;const rows=section.image.length,cols=section.image[0].length,off=document.createElement('canvas');off.width=cols;off.height=rows;const ctx=off.getContext('2d'),pixels=ctx.createImageData(cols,rows);
    const colors=new Map(section.materials.map(material=>[Number(material.label),/^#[\da-f]{6}$/i.test(material.color)?material.color:'#eff3f8']));
    for(let y=0;y<rows;y++)for(let x=0;x<cols;x++){const color=colors.get(section.image[y][x]) || '#eff3f8',i=(y*cols+x)*4;pixels.data[i]=parseInt(color.slice(1,3),16);pixels.data[i+1]=parseInt(color.slice(3,5),16);pixels.data[i+2]=parseInt(color.slice(5,7),16);pixels.data[i+3]=255;}
    ctx.putImageData(pixels,0,0);const left=57,top=13,w=width-left-17,h=height-top-38;context.imageSmoothingEnabled=false;context.drawImage(off,left,top,w,h);context.strokeStyle='#b6c7da';context.strokeRect(left,top,w,h);context.font='10px "Segoe UI",sans-serif';context.fillStyle='#49627f';
    const [u0,u1,z0,z1]=section.extent_mm;
    for(let i=0;i<=4;i++){const f=i/4;context.textAlign='center';context.fillText((u0+(u1-u0)*f).toFixed(2),left+w*f,top+h+16);context.textAlign='right';context.fillText((z0+(z1-z0)*f).toFixed(3),left-7,top+h*f+3);}
    context.textAlign='center';context.fillText(`${section.axis==='xz'?'x':'y'} (mm)`,left+w/2,height-4);context.save();context.translate(12,top+h/2);context.rotate(-Math.PI/2);context.fillText('Depth z (mm)',0,0);context.restore();
    canvas.dataset.axis=section.axis;canvas.dataset.materialCount=String(new Set(section.image.flat()).size);
  }
  async loadReference(){
    if(this.referenceLoaded)return;this.referenceLoaded=true;
    const twin=this.getTwin(),reference=twin.image_reference;
    $('#hbm-reference-status').textContent='Loading local reference image…';$('#hbm-reference-source').textContent=reference?.source_note || 'No source details recorded for this twin.';
    $('#hbm-reference-scale').textContent=reference?.pixel_size_um?`Recorded scale: ${reference.pixel_size_um} µm/pixel (${String(reference.scale_status).replaceAll('_',' ')}). Scale applies only to the original raster.`:'No image scale recorded.';
    try{
      const response=await fetch('/api/reference-image');if(twin!==this.getTwin())return;
      if(!response.ok){$('#hbm-reference-status').textContent='The supplied cross-section is not installed on this machine. Geometry editing and simulation remain available.';return;}
      if(!reference?.sha256 || response.headers.get('X-Reference-SHA256')!==reference.sha256){$('#hbm-reference-status').textContent='This twin’s reference image does not match the local image. The image is withheld to keep its source unambiguous.';return;}
      const blob=await response.blob();if(twin!==this.getTwin())return;
      this.referenceURL=URL.createObjectURL(blob);const img=$('#hbm-reference-image');img.src=this.referenceURL;await img.decode();if(twin!==this.getTwin())return;img.hidden=false;
      $('#hbm-reference-status').textContent='Local reference image · source and H100 variant unconfirmed.';
      if(img.naturalWidth!==reference.width_px || img.naturalHeight!==reference.height_px){$('#hbm-reference-scale').textContent=`Image size ${img.naturalWidth} × ${img.naturalHeight} px differs from the recorded ${reference.width_px} × ${reference.height_px} px. Scale metadata is inconsistent; no field of view is inferred.`;return;}
      const estimated=reference.scale_status==='user_estimate';
      $('#hbm-reference-scale').textContent=`${estimated?'Approximate ':''}${reference.pixel_size_um} µm/pixel (${String(reference.scale_status).replaceAll('_',' ')}). ${reference.width_px} × ${reference.height_px} px corresponds to ${estimated?'approximately ':''}${(reference.width_px*reference.pixel_size_um/1000).toFixed(2)} × ${(reference.height_px*reference.pixel_size_um/1000).toFixed(2)} mm. ${estimated?'This user estimate is not a calibrated measurement. ':''}Image contrast does not identify every material.`;
    }
    catch{if(twin===this.getTwin())$('#hbm-reference-status').textContent='Local reference unavailable. Geometry editing and simulation remain available.';}
  }
}
