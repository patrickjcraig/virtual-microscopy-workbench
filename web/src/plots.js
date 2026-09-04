const INK = '#627991';
const FONT = '10px "Segoe UI", Arial, sans-serif';
const PALETTE = [[0,18,40,72],[.24,25,76,120],[.5,51,144,173],[.74,118,201,188],[.91,231,229,158],[1,255,243,225]];
function heat(value) {
  const v = Math.max(0, Math.min(1, value));
  let b = 1; while (b < PALETTE.length - 1 && v > PALETTE[b][0]) b++;
  const lo = PALETTE[b - 1], hi = PALETTE[b], t = (v - lo[0]) / (hi[0] - lo[0]);
  return [1,2,3].map(i => Math.round(lo[i] + (hi[i] - lo[i]) * t));
}
function setup(canvas) {
  const width = canvas.clientWidth, height = canvas.clientHeight, dpr = Math.min(window.devicePixelRatio || 1, 2);
  if (!width || !height) return null;
  canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr);
  const ctx = canvas.getContext('2d'); ctx.scale(dpr, dpr); ctx.clearRect(0,0,width,height);
  ctx.font = FONT; ctx.fillStyle = INK; return {ctx,width,height};
}
function bitmap(data, mode, ceiling = 1) {
  const ny = data.length, nx = data[0]?.length || 0;
  const off = document.createElement('canvas'); off.width = nx; off.height = ny;
  const ctx = off.getContext('2d'), pixels = ctx.createImageData(nx, ny);
  for (let y = 0; y < ny; y++) for (let x = 0; x < nx; x++) {
    const val = Number.isFinite(data[y][x]) ? data[y][x] : 0;
    const color = mode === 'xray' ? Array(3).fill(Math.round(Math.max(0, Math.min(1,val)) * 255)) : heat(Math.abs(val)/ceiling);
    const j = (y * nx + x) * 4; pixels.data[j] = color[0]; pixels.data[j+1] = color[1]; pixels.data[j+2] = color[2]; pixels.data[j+3] = 255;
  }
  ctx.putImageData(pixels,0,0); return off;
}
export function mapPlot(canvas, result, mode, probe, {showProbe = true,ceiling = 1} = {}) {
  const setupResult = setup(canvas); if (!setupResult || !result?.image?.length) return;
  const {ctx,width,height} = setupResult;
  const [x0,x1,y0,y1] = result.extent_mm;
  const spanX = x1-x0, spanY=y1-y0;
  const availW = width - 88, availH = height - 52;
  const scale = Math.min(availW/spanX, availH/spanY);
  const w=spanX*scale,h=spanY*scale,left=(width-w)/2+9,top=(height-h)/2-9;
  canvas._plot = {left,top,w,h,extent:[x0,x1,y0,y1]};
  ctx.fillStyle='#f9fbfd';ctx.fillRect(0,0,width,height);
  ctx.imageSmoothingEnabled=true;ctx.drawImage(bitmap(result.image,mode,ceiling),left,top,w,h);
  ctx.strokeStyle='#c7d4e3';ctx.lineWidth=1;ctx.strokeRect(left-.5,top-.5,w+1,h+1);
  ctx.fillStyle=INK;ctx.font='9px "Segoe UI", Arial, sans-serif';
  for(let i=0;i<=4;i++) {
    const f=i/4, xx=left+w*f, yy=top+h*f;
    ctx.strokeStyle='#a0b0c4';ctx.beginPath();ctx.moveTo(xx,top+h+1);ctx.lineTo(xx,top+h+4);ctx.moveTo(left-4,yy);ctx.lineTo(left-1,yy);ctx.stroke();
    ctx.textAlign='center';ctx.fillText((x0+spanX*f).toFixed(spanX<2?2:1),xx,top+h+15);
    ctx.textAlign='right';ctx.fillText((y0+spanY*f).toFixed(spanY<2?2:1),left-7,yy+3);
  }
  ctx.textAlign='center';ctx.fillStyle='#5c708b';ctx.fillText('x (mm)',left+w/2,height-4);
  ctx.save();ctx.translate(left-29,top+h/2);ctx.rotate(-Math.PI/2);ctx.textAlign='center';ctx.fillText('y (mm)',0,0);ctx.restore();
  if (probe && showProbe) {
    const px=left+(probe[0]-x0)/spanX*w,py=top+(probe[1]-y0)/spanY*h;
    ctx.save();ctx.beginPath();ctx.rect(left,top,w,h);ctx.clip();
    ctx.strokeStyle='#ffffffdd';ctx.lineWidth=2.5;ctx.beginPath();ctx.arc(px,py,5,0,Math.PI*2);ctx.stroke();
    ctx.strokeStyle='#3b79ff';ctx.lineWidth=1.1;ctx.setLineDash([4,3]);ctx.beginPath();ctx.moveTo(left,py);ctx.lineTo(left+w,py);ctx.moveTo(px,top);ctx.lineTo(px,top+h);ctx.stroke();
    ctx.setLineDash([]);ctx.beginPath();ctx.arc(px,py,5,0,Math.PI*2);ctx.stroke();ctx.restore();
  }
  const targetLength=spanX*.15,magnitude=10**Math.floor(Math.log10(targetLength));
  const length=[1,2,5,10].map(value=>value*magnitude).reduce((best,value)=>Math.abs(value-targetLength)<Math.abs(best-targetLength)?value:best);
  const barW=length*scale,bx=left+w-barW-9,by=top+h-11;
  const scaleLabel=`${Number(length.toPrecision(2))} mm`;ctx.font='8px "Segoe UI", Arial, sans-serif';
  const labelWidth=Math.max(barW,ctx.measureText(scaleLabel).width);
  ctx.fillStyle=mode==='xray'?'#12243be6':'#ffffffe6';ctx.fillRect(bx+barW/2-labelWidth/2-4,by-15,labelWidth+8,20);
  ctx.strokeStyle=mode==='xray'?'#fff':'#233a59';ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(bx,by);ctx.lineTo(bx+barW,by);ctx.stroke();
  ctx.fillStyle=mode==='xray'?'#fff':'#233a59';ctx.textAlign='center';ctx.fillText(scaleLabel,bx+barW/2,by-5);
}
export function ascanPlot(canvas, data, settings) {
  const s=setup(canvas);if(!s||!data?.time_us?.length)return;
  const {ctx,width,height}=s;
  const left=44,right=width-14,top=22,bottom=height-30,w=right-left,h=bottom-top;
  const times=data.time_us, maxT=times[times.length-1], minT=times[0];
  const maxAmp=Math.max(1,...data.amplitude.map(Math.abs),...data.envelope.map(Math.abs));
  const xx=t=>left+(t-minT)/(maxT-minT)*w, yy=v=>top+h/2-v/maxAmp*h/2;
  ctx.fillStyle='#f3f7ff';ctx.fillRect(Math.max(left,xx(settings.gate_start_us)),top,Math.max(0,Math.min(right,xx(settings.gate_end_us))-Math.max(left,xx(settings.gate_start_us))),h);
  ctx.strokeStyle='#e5ecf4';ctx.lineWidth=1;ctx.font='9px "Segoe UI", Arial, sans-serif';
  for(let i=0;i<=4;i++) {
    const y=top+h*i/4,val=maxAmp*(1-i/2);ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(right,y);ctx.stroke();ctx.fillStyle=INK;ctx.textAlign='right';ctx.fillText(val.toFixed(1),left-7,y+3);
    const t=minT+(maxT-minT)*i/4,x=xx(t);ctx.beginPath();ctx.moveTo(x,top);ctx.lineTo(x,bottom);ctx.stroke();ctx.textAlign='center';ctx.fillText(t.toFixed(maxT>5?1:2),x,bottom+15);
  }
  const trace=(values,color,lineWidth)=>{ctx.save();ctx.beginPath();ctx.rect(left,top,w,h);ctx.clip();ctx.beginPath();values.forEach((v,i)=>i?ctx.lineTo(xx(times[i]),yy(v)):ctx.moveTo(xx(times[i]),yy(v)));ctx.strokeStyle=color;ctx.lineWidth=lineWidth;ctx.stroke();ctx.restore();};
  trace(data.amplitude,'#2357d8',1.2);trace(data.envelope,'#c48a40',1.4);
  ctx.font='8px "Segoe UI", Arial, sans-serif';ctx.textAlign='left';ctx.fillStyle='#2357d8';ctx.fillRect(left,7,11,2);ctx.fillText('RF waveform',left+15,11);ctx.fillStyle='#c48a40';ctx.fillRect(left+85,7,11,2);ctx.fillText('Envelope',left+100,11);
  ctx.textAlign='right';ctx.fillStyle=INK;ctx.fillText('Time (µs)',right,height-3);
  ctx.save();ctx.translate(11,top+h/2);ctx.rotate(-Math.PI/2);ctx.textAlign='center';ctx.font='8px "Segoe UI", Arial, sans-serif';ctx.fillText('Echo amplitude',0,0);ctx.restore();
  const gateX=Math.max(left,xx(settings.gate_start_us));ctx.fillStyle='#506c95';ctx.font='8px "Segoe UI", Arial, sans-serif';ctx.textAlign='left';if(gateX<right-30)ctx.fillText('Gate',gateX+4,top+10);
}
export function bscanPlot(canvas,data,probe) {
  const s=setup(canvas);if(!s||!data?.image?.length)return;
  const {ctx,width,height}=s;
  const [x0,x1,t0,t1]=data.extent,left=46,top=13,right=width-16,bottom=height-30,w=right-left,h=bottom-top;
  ctx.imageSmoothingEnabled=true;ctx.drawImage(bitmap(data.image,'sam'),left,top,w,h);
  ctx.font='9px "Segoe UI", Arial, sans-serif';ctx.fillStyle=INK;
  for(let i=0;i<=4;i++) {
    const f=i/4;ctx.textAlign='center';ctx.fillText((x0+(x1-x0)*f).toFixed(1),left+w*f,bottom+15);
    ctx.textAlign='right';ctx.fillText((t0+(t1-t0)*f).toFixed(t1>5?1:2),left-7,top+h*f+3);
  }
  if(probe){ctx.strokeStyle='#f8c874';ctx.lineWidth=1;ctx.setLineDash([3,3]);const x=left+(probe[0]-x0)/(x1-x0)*w;ctx.beginPath();ctx.moveTo(x,top);ctx.lineTo(x,bottom);ctx.stroke();ctx.setLineDash([]);}
  ctx.textAlign='right';ctx.fillText('x (mm)',right,height-3);
  ctx.save();ctx.translate(12,top+h/2);ctx.rotate(-Math.PI/2);ctx.textAlign='center';ctx.font='8px "Segoe UI", Arial, sans-serif';ctx.fillText('Time (µs)',0,0);ctx.restore();
}
export function pointFromEvent(canvas,event) {
  const plot=canvas._plot;if(!plot)return null;
  const box=canvas.getBoundingClientRect(),x=event.clientX-box.left,y=event.clientY-box.top;
  if(x<plot.left||x>plot.left+plot.w||y<plot.top||y>plot.top+plot.h)return null;
  const [x0,x1,y0,y1]=plot.extent;
  return [x0+(x-plot.left)/plot.w*(x1-x0),y0+(y-plot.top)/plot.h*(y1-y0)];
}
