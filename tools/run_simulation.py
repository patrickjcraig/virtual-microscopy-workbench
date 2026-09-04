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
    noise = parser.add_mutually_exclusive_group()
    noise.add_argument("--noise", action="store_true", dest="noise", default=None)
    noise.add_argument("--no-noise", action="store_false", dest="noise")
    args = parser.parse_args()
    twin = Twin.model_validate_json(args.twin.read_text(encoding="utf-8")).model_dump(mode="json", exclude_none=True)
    sx,sy,sz=twin["size_mm"]
    settings = Settings(probe_x_mm=sx/2,probe_y_mm=sy/2,focus_mm=min(.5,sz)).model_dump(mode="json")
    settings.update(twin.get("recommended_settings", {}))
    for key in ("resolution", "energy_kev", "frequency_mhz", "noise"):
        value = getattr(args, key)
        if value is not None:
            settings[key] = value
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
