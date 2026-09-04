"""Run the same workbench models without a browser and export numerical arrays."""
import argparse
import json
from pathlib import Path

import numpy as np

from virtual_microscopy.schemas import Settings, SimulationRequest, Twin
from virtual_microscopy.server import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("twin", type=Path)
    parser.add_argument("output", type=Path, help="New output directory; existing directories are refused")
    parser.add_argument("--resolution", type=int, choices=[64,128,192])
    parser.add_argument("--energy-kev", type=float)
    parser.add_argument("--frequency-mhz", type=float)
    parser.add_argument("--depth-samples", type=int, choices=[128,256,512,1024])
    region = parser.add_mutually_exclusive_group()
    region.add_argument("--roi", type=float, nargs=4, metavar=("X0", "Y0", "X1", "Y1"),
                        help="Normal-incidence full-depth ROI in global millimetres")
    region.add_argument("--hbm-roi", help="Scan an HBM footprint, e.g. hbm-6, with 1024 depth samples by default")
    noise = parser.add_mutually_exclusive_group()
    noise.add_argument("--noise", action="store_true", dest="noise", default=None)
    noise.add_argument("--no-noise", action="store_false", dest="noise")
    args = parser.parse_args()
    twin = Twin.model_validate_json(args.twin.read_text(encoding="utf-8")).model_dump(mode="json", exclude_none=True)
    sx,sy,sz=twin["size_mm"]
    settings = Settings(probe_x_mm=sx/2,probe_y_mm=sy/2,focus_mm=min(.5,sz)).model_dump(mode="json")
    settings.update(twin.get("recommended_settings", {}))
    for key in ("resolution", "energy_kev", "frequency_mhz", "noise", "depth_samples"):
        value = getattr(args, key)
        if value is not None:
            settings[key] = value
    roi = args.roi
    if args.hbm_roi:
        stack = next((s for s in twin.get("hbm_assemblies", []) if s["id"] == args.hbm_roi), None)
        if stack is None:
            parser.error(f"Unknown HBM assembly: {args.hbm_roi}")
        x,y = stack["center_xy_mm"]
        w,h = stack["footprint_mm"]
        roi = [x-w/2,y-h/2,x+w/2,y+h/2]
        if args.depth_samples is None:
            settings["depth_samples"] = 1024
    if roi is not None:
        settings.update(roi_mm=roi, angle_deg=0, probe_x_mm=(roi[0]+roi[2])/2, probe_y_mm=(roi[1]+roi[3])/2)
    request = SimulationRequest(twin=twin,settings=settings)
    if args.output.exists():
        raise FileExistsError(f"Refusing to overwrite an existing output directory: {args.output}")
    result = run(request)
    args.output.mkdir(parents=True,exist_ok=False)
    with (args.output/"simulation.json").open("x",encoding="utf-8") as f:
        json.dump(result,f,allow_nan=False,separators=(",",":"))
    with (args.output/"arrays.npz").open("xb") as f:
        np.savez_compressed(f,xray_transmission=result["xray"]["image"],sam_echo_amplitude=result["sam"]["image"],time_us=result["ascan"]["time_us"],rf_amplitude=result["ascan"]["amplitude"],rf_envelope=result["ascan"]["envelope"],bscan=result["bscan"]["image"])
    print(f"Saved {args.output.resolve()} / run {result['run_id']}")
    print(f"Synthetic data; runtime {result['metadata']['runtime_ms']:.0f} ms")


if __name__ == "__main__":
    main()
