"""Create an editable, explicitly assumed HBM6 patch preset; refuse overwrites."""
import argparse
import json
from pathlib import Path

from tools.build_h100_example import h100
from virtual_microscopy.hbm import compose_hbm, microstructure_summary
from virtual_microscopy.schemas import Twin


def microstructure_example():
    twin = compose_hbm(h100(), "hbm-6", {"microstructure": {}})
    twin["name"] = "NVIDIA H100 SXM / HBM6 explicit patch"
    twin["description"] = "Six physical HBM sites with a selected 2×3 synthetic microstructure patch through HBM6. Inspect 48 inter-die solder-proxy bumps and 54 copper TSVs, author local defects, and acquire a fine ROI. Patch dimensions and composition are assumptions."
    summary = microstructure_summary(twin["hbm_assemblies"][-1])
    twin["recommended_settings"].update(resolution=64, depth_samples=1024,
        roi_mm=summary["roi_mm"], angle_deg=0, noise=False, frequency_mhz=100,
        gate_start_us=.26, gate_end_us=.7, focus_mm=.55, probe_x_mm=49.525, probe_y_mm=40)
    assumptions = twin["reference"]["assumptions"]
    assumptions[2] = assumptions[2].replace("TSVs and microbumps remain unresolved; aggregate attachment contacts are illustrative.",
        "HBM6 has a selected explicit patch; other connections remain unresolved and package attachment contacts remain illustrative.")
    assumptions.insert(3,"The selected HBM6 patch uses 2×3 sites at 50 µm pitch with 25 µm solder-proxy bump cylinders in eight gaps and 10 µm copper TSV cylinders through eight DRAM dies plus the base. These are editable simulation choices, not dimensions inferred from the image or verified H100 construction.")
    assumptions.append("The recommended 0.125×0.175 mm ROI retains full package depth. At 64×64×1024 samples its pitches are 1.953125×2.734375×2.587891 µm; these are numerical sampling intervals, not measured instrument resolution.")
    return Twin.model_validate(twin).model_dump(mode="json", exclude_none=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,default=Path(__file__).resolve().parents[1]/"examples"/"nvidia-h100-hbm6-microstructure.json")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open("x",encoding="utf-8",newline="\n") as handle:
        json.dump(microstructure_example(),handle,indent=2,ensure_ascii=False)
        handle.write("\n")
    print(args.output)


if __name__ == "__main__":
    main()
