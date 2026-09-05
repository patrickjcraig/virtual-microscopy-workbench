"""Bounded recording-time views of saved finite coherent observations."""
import numpy as np

from .causal_processing import _section, PRODUCT_UNITS
from .observation_datasets import ObservationStore
from .observation_math import BOUNDS, SIGNALS
from .observation_plan import measure


def observation_view(root, identifier, *, x_index=None, y_index=None, time_index=None,
                     product="envelope", gate_start_us=None, gate_end_us=None, gate_mode="peak_envelope"):
    if product not in SIGNALS or gate_mode not in ("peak_envelope","rms_rf"):
        raise ValueError("Select a supported saved observation product and gate statistic.")
    store = ObservationStore(root)
    manifest = store.verify_complete(identifier)
    group = store._open_checked(identifier, manifest)
    plan = manifest["estimate"]
    x,y,time = (np.asarray(group[key][:],dtype=np.float64) for key in ("x_mm","y_mm","time_us"))
    ny,nx,nt = manifest["shape"]
    xi,yi,ti = nx//2 if x_index is None else x_index, ny//2 if y_index is None else y_index, nt//2 if time_index is None else time_index
    if any(type(v) is not int for v in (xi,yi,ti)) or not(0<=xi<nx and 0<=yi<ny and 0<=ti<nt):
        raise ValueError("Observation cursor is outside the saved interior.")
    t0,t1 = float(time[0]) if gate_start_us is None else gate_start_us, float(time[-1]) if gate_end_us is None else gate_end_us
    if (not np.isfinite([t0,t1]).all() or not time[0]<=t0<t1<=time[-1]):
        raise ValueError("The gate must be ordered and inside the actual saved recording centers.")
    lo,hi = int(np.searchsorted(time,t0,side="left")),int(np.searchsorted(time,t1,side="right"))
    if lo>=hi:
        raise ValueError("The gate contains no saved time centers.")
    xy,yt,gated = np.empty((ny,nx)),np.empty((ny,nt)),np.empty((ny,nx))
    maxima = {key:0. for key in BOUNDS}
    bound_maps = {key:np.empty((ny,nx)) for key in BOUNDS}
    for row in range(ny):
        values = store._read_row(identifier,manifest,group,row)
        xy[row],yt[row] = values[product][:,ti],values[product][xi]
        for key in BOUNDS:
            maxima[key] = max(maxima[key],float(values[key].max()))
            bound_maps[key][row] = values[key]
        if row==yi:
            xt = values[product].copy()
            ascan = {key:values[key][xi].tolist() for key in SIGNALS}
            bounds = {key:float(values[key][xi]) for key in BOUNDS}
        gate_values = values["envelope" if gate_mode=="peak_envelope" else "rf"][:,lo:hi]
        if gate_mode=="peak_envelope":
            gated[row] = gate_values.max(axis=1)
        else:
            scale = np.max(np.abs(gate_values),axis=1)
            normalized = np.divide(gate_values,scale[:,None],out=np.zeros_like(gate_values),where=scale[:,None]!=0)
            gated[row] = scale*np.sqrt(np.mean(normalized*normalized,axis=1))
    if measure(store.manifest(identifier))["sha256"] != measure(manifest)["sha256"]:
        raise ValueError("Observation manifest changed during inspection.")
    extent = plan["extent_mm"]
    unit = PRODUCT_UNITS[product]
    cursor = {"x_index":xi,"y_index":yi,"time_index":ti,"source_x_index":plan["source_indices"]["x"][xi],
              "source_y_index":plan["source_indices"]["y"][yi],"x_mm":float(x[xi]),"y_mm":float(y[yi]),
              "time_us":float(time[ti]),**{key:ascan[key][ti] for key in SIGNALS}}
    maps = {key:value.tolist() for key,value in bound_maps.items()}
    return {"coordinates":{"x_mm":x.tolist(),"y_mm":y.tolist(),"time_us":time.tolist()},
        "shape":manifest["shape"],"extent_mm":extent,"source_extent_mm":plan["source_extent_mm"],
        "source_indices":plan["source_indices"],"source_summary":plan["source_summary"],"operator":plan["operator"],
        "xy":{"image":xy.tolist(),"extent_mm":extent,"unit":unit},
        "xt":_section(xt,time,extent[:2],"x",unit),"yt":_section(yt,time,extent[2:],"y",unit),
        "ascan":{**ascan,"amplitude":ascan["rf"],"time_us":time.tolist(),"probe_mm":[float(x[xi]),float(y[yi])]},
        "cscan":{"image":gated.tolist(),"extent_mm":extent,"unit":"peak recomputed complex magnitude" if gate_mode=="peak_envelope" else "RMS real pressure"},
        "cursor":cursor,"bound_maps":maps,
        "gate":{"start_us":t0,"end_us":t1,"actual_start_us":float(time[lo]),"actual_end_us":float(time[hi-1]),
                "mode":gate_mode,"sample_count":hi-lo,"certificate_scope":"Ordinary saved-sample reduction; no additional numerical certificate."},
        "certificate":{"selected_bounds":bounds,"volume_max_bounds":maxima,"max_bounds":maxima,"maps":maps,
                       "requested_tolerance":manifest["request"]["absolute_tolerance"],"definition":plan["certificate_scope"]},
        "metadata":{"axes":["y","x","time"],"shape":manifest["shape"],"product":product,"dtype":"float64",
                    "observation_model":plan["operator"]["model"],"evidence_status":manifest["evidence_status"],
                    "time_axis":"Inherited gamma recording time; repeated returns have no unique physical depth.",
                    "display_reduction":"None; exact saved centers and full retained recording.",
                    "source_required":False,"magnitude_processing":"Magnitude after signed complex spatial mixing."}}
