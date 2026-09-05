"""Compare fine HBM ROI projections with independent continuous vertical rays.

The ray oracle intersects ordered primitives analytically. It does not read the
production voxel grid. These are synthetic geometry/discretization checks, not
an instrument resolution or experimental H100 validation.
"""
import argparse
from copy import deepcopy
import json
from math import exp, log, sqrt
from pathlib import Path

import numpy as np

from tools.build_h100_example import h100
from virtual_microscopy.hbm import compose_hbm, microstructure_summary
from virtual_microscopy.materials import linear_attenuation_mm
from virtual_microscopy.physics import project_xray, voxelize


def continuous_ray(twin, x, y, energy_kev=80):
    intervals = []
    for obj in twin["objects"]:
        cx, cy, cz = obj["center_mm"]
        sx, sy, sz = obj["size_mm"]
        if abs(x-cx) > sx/2 or abs(y-cy) > sy/2:
            continue
        if obj["shape"] == "box":
            half_z = sz/2
        else:
            radial = ((x-cx)/(sx/2))**2 + ((y-cy)/(sy/2))**2
            if radial > 1:
                continue
            half_z = sz/2 if obj["shape"] == "cylinder" else sz/2*sqrt(1-radial)
        intervals.append((cz-half_z, cz+half_z, obj["material"]))
    edges = sorted({0., twin["size_mm"][2], *(edge for low, high, _ in intervals for edge in (low,high))})
    lengths = {}
    for low, high in zip(edges[:-1], edges[1:]):
        middle = (low+high)/2
        material = next((material for start,end,material in reversed(intervals) if start <= middle <= end), None)
        if material is not None:
            lengths[material] = lengths.get(material,0.) + high-low
    tau = sum(linear_attenuation_mm(material,energy_kev)*length for material,length in lengths.items())
    return {"transmission": exp(-tau), "optical_depth": tau, "material_lengths_mm": lengths}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output",type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    # An independent two-slab check establishes the oracle's length convention.
    slabs = {"size_mm":[1,1,1],"objects":[
        {"shape":"box","material":"silicon","center_mm":[.5,.5,.2],"size_mm":[1,1,.4]},
        {"shape":"box","material":"copper","center_mm":[.5,.5,.6],"size_mm":[1,1,.2]}]}
    assert abs(continuous_ray(slabs,.5,.5)["optical_depth"]-(.4*linear_attenuation_mm("silicon",80)+.2*linear_attenuation_mm("copper",80))) < 1e-12
    intact = compose_hbm(h100(),"hbm-6",{"microstructure":{}})
    stack = intact["hbm_assemblies"][-1]
    summary = microstructure_summary(stack)
    micro = deepcopy(stack["microstructure"])
    micro["defects"] = [{"id":"missing-r2-c2-gap4","kind":"missing_bump","row":2,"column":2,"layer_index":4}]
    defective = compose_hbm(intact,"hbm-6",{"microstructure":micro})
    roi = summary["roi_mm"]
    target = next(item for item in summary["features"] if item["id"] == "hbm-6-mb-04-r02-c02")
    count = 64
    x = roi[0]+(np.arange(count)+.5)*(roi[2]-roi[0])/count
    y = roi[1]+(np.arange(count)+.5)*(roi[3]-roi[1])/count
    i,j = int(np.argmin(abs(x-target["center_mm"][0]))),int(np.argmin(abs(y-target["center_mm"][1])))
    references = {name:continuous_ray(twin,float(x[i]),float(y[j])) for name,twin in (("intact",intact),("defective",defective))}
    expected_log_ratio = (linear_attenuation_mm("solder",80)-linear_attenuation_mm("epoxy",80))*.015
    assert abs(log(references["defective"]["transmission"]/references["intact"]["transmission"])-expected_log_ratio) < 1e-10
    assembly_only = {**intact,"objects":[obj for obj in intact["objects"] if obj.get("assembly_id") == "hbm-6"]}
    surrounding_tau = references["intact"]["optical_depth"]-continuous_ray(assembly_only,float(x[i]),float(y[j]))["optical_depth"]
    assert surrounding_tau > 0
    arrays, checks = {}, []
    for depth in (256,512,1024):
        pair = {}
        for name,twin in (("intact",intact),("defective",defective)):
            grid = voxelize(twin,count,True,roi_mm=roi,depth_samples=depth)
            image = project_xray(grid,80,noise=False,detector_fwhm_mm=0)
            arrays[f"{name}_{depth}"] = image
            pair[name] = float(image[j,i])
            assert grid.origin_mm[2] == 0
            assert grid.size_mm[2] == intact["size_mm"][2]
        measured_log_ratio = log(pair["defective"]/pair["intact"])
        checks.append({"depth_samples":depth,"pitch_xyz_um":[(roi[2]-roi[0])/count*1000,(roi[3]-roi[1])/count*1000,2.65/depth*1000],
            "sampled_transmission":pair,"continuous_transmission":{key:value["transmission"] for key,value in references.items()},
            "sampled_log_ratio":measured_log_ratio,"expected_continuous_log_ratio":expected_log_ratio,
            "log_ratio_error":measured_log_ratio-expected_log_ratio})
    assert checks[-1]["sampled_log_ratio"] > 0
    assert abs(checks[-1]["log_ratio_error"]) < .02
    args.output.mkdir(parents=True)
    np.savez_compressed(args.output/"projections.npz",x_mm=x,y_mm=y,**arrays)
    report = {"evidence":"Synthetic numerical comparison with an independent continuous ordered-primitive ray oracle; no experimental validation.",
        "model":"Normal-incidence monochromatic 80 keV, no noise or detector blur; default material proxies.",
        "roi_mm":roi,"ray_xy_mm":[float(x[i]),float(y[j])],"target_id":target["id"],
        "intact_primitive_count":len(intact["objects"]),"defective_primitive_count":len(defective["objects"]),
        "continuous_references":references,"surrounding_package_optical_depth":surrounding_tau,"checks":checks,
        "interpretation":"Voxel-center geometry can alias nonmonotonically as depth sampling changes. Positive missing-bump transmission contrast here does not establish observability after instrument response and noise."}
    (args.output/"report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report,indent=2))


if __name__ == "__main__":
    main()
