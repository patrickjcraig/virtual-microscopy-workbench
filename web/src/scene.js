import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

export class TwinViewer {
  constructor(element) {
    this.element = element;
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color('#f3f7fc');
    this.camera = new THREE.PerspectiveCamera(33, 1, 0.01, 500);
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.0;
    this.renderer.domElement.setAttribute('aria-label', 'Interactive digital twin. Drag to orbit, scroll to zoom.');
    this.renderer.domElement.setAttribute('role', 'img');
    this.renderer.domElement.tabIndex = 0;
    element.prepend(this.renderer.domElement);
    this.labels = [];
    this.labelLayer = document.createElement('div');
    this.labelLayer.className = 'scene-part-labels';
    this.labelLayer.setAttribute('role', 'list');
    this.labelLayer.setAttribute('aria-label', 'Labeled specimen components');
    element.append(this.labelLayer);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.12;
    this.controls.maxPolarAngle = Math.PI * .84;
    this.controls.addEventListener('change', () => this.render());
    this.scene.add(new THREE.HemisphereLight(0xf7fbff, 0xa8b8cf, 1.8));
    const key = new THREE.DirectionalLight(0xffffff, 2.1);
    key.position.set(5, 10, 5); this.scene.add(key);
    const rim = new THREE.DirectionalLight(0xc7dcff, 1.0);
    rim.position.set(-6, 3, -3); this.scene.add(rim);
    this.model = new THREE.Group(); this.scene.add(this.model);
    this.floor = new THREE.Group(); this.scene.add(this.floor);
    this.probe = new THREE.Group(); this.scene.add(this.probe);
    this.roi = new THREE.Group(); this.scene.add(this.roi);
    this.resize = new ResizeObserver(() => {
      const { width, height } = element.getBoundingClientRect();
      if (!width || !height) return;
      this.camera.aspect = width / height; this.camera.updateProjectionMatrix();
      this.renderer.setSize(width, height, false); this.render();
    });
    this.resize.observe(element);
    this.renderer.domElement.addEventListener('keydown', (e) => {
      if (e.key === 'Home' || e.key.toLowerCase() === 'r') { e.preventDefault(); this.reset(); }
    });
    this.tick = () => { this.controls.update(); this.frame = requestAnimationFrame(this.tick); };
    this.tick();
  }
  clear(group) {
    while (group.children.length) {
      const child = group.children[0];
      child.traverse((node) => { node.geometry?.dispose(); if (Array.isArray(node.material)) node.material.forEach(m => m.dispose()); else node.material?.dispose(); });
      group.remove(child);
    }
  }
  setTwin(twin, materials, includeDefects, explode = false, microstructure = null) {
    const changed = this.twin !== twin;
    this.twin = twin; this.exploded = explode;this.includeDefects=includeDefects;this.microstructure=microstructure;
    const focus=this.microFocus&&!changed&&!explode;
    this.labels = []; this.labelLayer.replaceChildren();
    this.renderer.domElement.setAttribute('aria-label', `${twin.name}. Interactive digital twin. Drag to orbit, scroll to zoom.`);
    this.element.closest('.twin-panel').classList.toggle('reference-twin', Boolean(twin.reference));
    this.clear(this.model); this.clear(this.floor);
    const [sx, sy, sz] = twin.size_mm;
    this.span = Math.max(sx, sy, sz * 2);
    const materialColors = Object.fromEntries(materials.map(m => [m.id, m.color]));
    const defaults = { silicon:'#384c6c', copper:'#b97644', solder:'#aebaca', epoxy:'#90a9c6', fr4:'#52786d', air:'#ed935b' };
    const localDefects=new Map((microstructure?.defects || []).filter(d=>d.enabled).map(d=>[d.primitive_id,d]));
    const missing=new Set(includeDefects?(microstructure?.defects || []).filter(d=>d.enabled&&d.kind==='missing_bump').map(d=>d.target_id):[]);
    const structures = twin.objects.filter(o => (includeDefects || o.role !== 'defect')&&!missing.has(o.id));
    this.renderer.domElement.dataset.hiddenMissingBumps=String(missing.size);
    this.renderer.domElement.dataset.voidMarkers=String(includeDefects?[...localDefects.values()].filter(d=>d.kind!=='missing_bump').length:0);
    for (const item of structures) {
      let geometry;
      const [x, y, z] = item.size_mm;
      if (item.shape === 'sphere') geometry = new THREE.SphereGeometry(x / 2, 22, 14);
      else if (item.shape === 'cylinder') geometry = new THREE.CylinderGeometry(x / 2, x / 2, z, 24);
      else geometry = new THREE.BoxGeometry(x, z, y);
      const isDefect = item.role === 'defect',localDefect=localDefects.get(item.id),voidMarker=Boolean(localDefect&&localDefect.kind!=='missing_bump');
      const transparent = item.material === 'epoxy' || item.material === 'air';
      const surface = new THREE.MeshStandardMaterial({
        color: isDefect && localDefect?.kind!=='missing_bump' ? '#f59b52' : materialColors[item.material] || defaults[item.material] || '#879ab1',
        metalness: ['copper', 'solder'].includes(item.material) ? .55 : .1,
        roughness: .43,
        transparent,
        opacity: isDefect ? .78 : transparent ? .17 : 1,
        depthWrite: !transparent,
        wireframe:voidMarker,
        side: THREE.DoubleSide,
      });
      const mesh = new THREE.Mesh(geometry, surface);
      const offset = explode ? (sz / 2 - item.center_mm[2]) * 2.7 : 0;
      mesh.position.set(item.center_mm[0] - sx / 2, sz / 2 - item.center_mm[2] + offset, item.center_mm[1] - sy / 2);
      mesh.userData.object = item;mesh.userData.localDefect=localDefect;
      mesh.renderOrder = transparent ? 2 : 0;
      this.model.add(mesh);
      if (item.display_label && (explode || (!isDefect && item.id !== 'interposer'))) {
        const label = document.createElement('span'), leader = document.createElement('i');
        label.className = 'scene-part-label'; label.textContent = item.display_label;
        label.dataset.objectId = item.id; label.setAttribute('role', 'listitem');
        leader.className = 'scene-part-leader'; leader.setAttribute('aria-hidden', 'true');
        this.labelLayer.append(leader, label);
        this.labels.push({ mesh, label, leader, height:z });
      }
      if(localDefect){
        const label=document.createElement('span'),leader=document.createElement('i');label.className='scene-feature-label';label.textContent=voidMarker?`Air void marker · ${localDefect.id}`:`Epoxy replacement · ${localDefect.id}`;label.dataset.objectId=item.id;label.setAttribute('role','listitem');leader.className='scene-part-leader';leader.setAttribute('aria-hidden','true');this.labelLayer.append(leader,label);this.labels.push({mesh,label,leader,height:z,microOnly:true});
      }
      if (item.shape === 'box' || (isDefect&&!localDefect)) {
        const edge = new THREE.LineSegments(new THREE.EdgesGeometry(geometry), new THREE.LineBasicMaterial({ color:isDefect ? '#ad4b18' : transparent ? '#6d89ac' : '#273e55', transparent:true, opacity:transparent ? .28 : .19 }));
        mesh.add(edge);
      }
    }
    const grid = new THREE.GridHelper(this.span * 1.75, 14, 0xc5d4e6, 0xe0e8f2);
    grid.position.y = -sz / 2 - .025 - (explode ? sz * 1.35 : 0);
    this.floor.add(grid);
    this.controls.minDistance = this.span * .45; this.controls.maxDistance = this.span * 6;
    if(focus)this.focusMicrostructure(microstructure);else if(changed||this.microFocus)this.reset();
    this.render();
  }
  setProbe(x, y) {
    if (!this.twin) return;
    this.clear(this.probe);
    const [sx, sy, sz] = this.twin.size_mm;
    const h = sz / 2 + (this.exploded ? sz * 1.35 : 0) + .08;
    const ring = new THREE.Mesh(new THREE.TorusGeometry(this.span * .025, this.span * .0028, 6, 32), new THREE.MeshBasicMaterial({ color:0x2357d8, depthTest:false, transparent:true, opacity:.95 }));
    ring.rotation.x = Math.PI / 2; ring.position.set(x - sx / 2, h, y - sy / 2); ring.renderOrder = 8;
    this.probe.add(ring);
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(x - sx / 2, h, y - sy / 2), new THREE.Vector3(x - sx / 2, h + this.span * .13, y - sy / 2)]), new THREE.LineDashedMaterial({ color:0x2357d8,dashSize:this.span * .007,gapSize:this.span * .004,transparent:true,opacity:.7,depthTest:false }));
    line.computeLineDistances(); line.renderOrder = 8; this.probe.add(line); this.render();
  }
  setROI(bounds) {
    this.clear(this.roi);if(!this.twin || !bounds){this.render();return;}
    const [sx,sy,sz]=this.twin.size_mm,[x0,y0,x1,y1]=bounds,h=sz/2+(this.exploded?sz*1.35:0)+.09;
    const points=[[x0,y0],[x1,y0],[x1,y1],[x0,y1],[x0,y0]].map(([x,y])=>new THREE.Vector3(x-sx/2,h,y-sy/2));
    const outline=new THREE.Line(new THREE.BufferGeometry().setFromPoints(points),new THREE.LineBasicMaterial({color:0xb86b27,depthTest:false}));outline.renderOrder=9;this.roi.add(outline);this.render();
  }
  focusMicrostructure(summary=this.microstructure) {
    if(!this.twin || !summary?.feature_bounds_mm)return;
    this.microFocus=true;this.microstructure=summary;const ids=new Set(summary.features.map(f=>f.id));if(this.includeDefects)for(const defect of summary.defects || [])if(defect.enabled)ids.add(defect.primitive_id);
    for(const mesh of this.model.children){mesh.visible=ids.has(mesh.userData.object.id);if(mesh.userData.localDefect&&mesh.material.wireframe){mesh.material.depthTest=false;mesh.renderOrder=10;}}
    this.floor.visible=false;this.probe.visible=false;this.roi.visible=false;
    const [x0,y0,z0,x1,y1,z1]=summary.feature_bounds_mm,[sx,sy,sz]=this.twin.size_mm;
    const target=new THREE.Vector3((x0+x1-sx)/2,(sz-z0-z1)/2,(y0+y1-sy)/2);this.focusSpan=Math.max(x1-x0,y1-y0,z1-z0);
    this.camera.near=Math.max(.000001,this.focusSpan/10000);this.camera.updateProjectionMatrix();this.controls.minDistance=Math.max(.001,this.focusSpan*.035);this.controls.maxDistance=this.focusSpan*12;
    this.controls.target.copy(target);this.camera.position.copy(target).add(new THREE.Vector3(.9,.25,1.4).normalize().multiplyScalar(this.focusSpan*2.2));this.controls.update();
    this.renderer.domElement.dataset.viewMode='microstructure';this.renderer.domElement.dataset.visibleFeatureCount=String(this.model.children.filter(mesh=>mesh.visible&&!mesh.userData.localDefect).length);this.onViewMode?.(true);this.render();
  }
  reset() {
    this.microFocus=false;this.floor.visible=true;this.probe.visible=true;this.roi.visible=true;
    for(const mesh of this.model.children){mesh.visible=true;if(mesh.material)mesh.material.depthTest=true;}
    this.camera.near=.01;this.camera.updateProjectionMatrix();this.controls.minDistance=(this.span||6)*.45;this.controls.maxDistance=(this.span||6)*6;
    this.renderer.domElement.dataset.viewMode='package';this.onViewMode?.(false);
    const span = this.span || 6;
    if(this.labels.length) {
      const bounds=new THREE.Box3().setFromObject(this.model),corners=[];
      for(const x of [bounds.min.x,bounds.max.x])for(const y of [bounds.min.y,bounds.max.y])for(const z of [bounds.min.z,bounds.max.z])corners.push(new THREE.Vector3(x,y,z));
      this.camera.aspect=this.element.clientWidth/Math.max(1,this.element.clientHeight);this.camera.updateProjectionMatrix();
      const direction=new THREE.Vector3(.64,.83,.87).normalize();let distance=span*1.2;
      // Fit the actual package, leaving room for source labels and scene controls.
      for(let attempt=0;attempt<20;attempt++) {
        this.camera.position.copy(direction).multiplyScalar(distance);this.camera.lookAt(0,0,0);this.camera.updateMatrixWorld();
        if(corners.every(corner=>{const p=corner.clone().project(this.camera);return Math.abs(p.x)<.88 && Math.abs(p.y)<.79;}))break;
        distance*=1.1;
      }
    } else this.camera.position.set(span * 1.08, span * 1.07, span * 1.25);
    this.controls.target.set(0, 0, 0); this.controls.update(); this.render();
  }
  render() {
    this.renderer.render(this.scene, this.camera);
    const width=this.element.clientWidth,height=this.element.clientHeight,placed=[];
    for(const {mesh,label,leader,height:partHeight,microOnly} of this.labels) {
      const point=mesh.position.clone();point.y+=partHeight / 2 + (this.microFocus?this.focusSpan:this.span) * .012;point.project(this.camera);
      const visible=mesh.visible && (microOnly?this.microFocus:!this.microFocus) && point.z>=-1 && point.z<=1 && Math.abs(point.x)<1.1 && Math.abs(point.y)<1.1;
      label.hidden=!visible;leader.hidden=!visible;if(!visible)continue;
      const anchorX=(point.x + 1) * width / 2,anchorY=(1 - point.y) * height / 2;
      const labelWidth=label.offsetWidth,labelHeight=label.offsetHeight,candidates=[];
      for(const dx of [0,-36,36,-72,72,-108,108,-144,144])for(const dy of [0,-24,24,-48,48,-72,72,-96,96])candidates.push({dx,dy,cost:Math.abs(dx)+Math.abs(dy)*1.15});
      candidates.sort((a,b)=>a.cost-b.cost);
      let x,y,rectangle;
      for(const candidate of candidates) {
        x=Math.max(labelWidth/2+7,Math.min(width-labelWidth/2-7,anchorX+candidate.dx+(microOnly?120:0)));
        y=Math.max(43+labelHeight,Math.min(height-35,anchorY-12+candidate.dy));
        rectangle={left:x-labelWidth/2,top:y-labelHeight,right:x+labelWidth/2,bottom:y};
        if(!placed.some(box=>rectangle.left<box.right+5 && rectangle.right>box.left-5 && rectangle.top<box.bottom+5 && rectangle.bottom>box.top-5))break;
      }
      placed.push(rectangle);
      label.style.left=`${x}px`;label.style.top=`${y}px`;
      const dx=x-anchorX,dy=y-anchorY;
      leader.style.left=`${anchorX}px`;leader.style.top=`${anchorY}px`;
      leader.style.width=`${Math.hypot(dx,dy)}px`;leader.style.transform=`rotate(${Math.atan2(dy,dx)}rad)`;
    }
  }
}
