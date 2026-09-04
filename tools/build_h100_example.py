"""Build the public-reference H100 teaching specimen, without replacing files.

NVIDIA architectural facts are kept separate from our assumed package geometry.
The model is neither proprietary CAD nor a transistor-resolved GPU simulation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _part(identifier, name, material, center, size, shape="box", role="structure", label=None):
    part = dict(id=identifier, name=name, material=material, center_mm=center,
                size_mm=size, shape=shape, role=role)
    if label is not None:
        part["display_label"] = label
    return part


def h100():
    """Return a deterministic, deliberately coarse H100 SXM package surrogate."""
    die_width = 26.7
    die_height = 814.0 / die_width
    objects = [
        _part("substrate", "Assumed organic package substrate (FR-4 proxy)", "fr4",
              [30, 30, 1.715], [60, 60, 0.67]),
        _part("interposer-bond", "Assumed interposer attach / underfill", "epoxy",
              [30, 30, 1.30], [50, 44, 0.16]),
        _part("interposer", "Assumed silicon interposer", "silicon",
              [30, 30, 1.11], [50, 44, 0.22], label="Silicon interposer"),
        _part("gpu-underfill", "Assumed GH100 underfill", "epoxy",
              [30, 30, 0.91], [die_width, die_height, 0.18]),
        _part("gh100", "GH100 die / 814 mm2; aspect and thickness assumed", "silicon",
              [30, 30, 0.485], [die_width, die_height, 0.67], label="GH100 · 814 mm²"),
        _part("substrate-plane", "Assumed coarse copper reference plane", "copper",
              [30, 30, 1.92], [57, 57, 0.06]),
    ]

    # Five functional blocks communicate the documented SXM 80 GB configuration.
    # Placement and dimensions are assumptions; the physical sixth-site occupancy
    # cannot be inferred from the enabled memory count and is not drawn here.
    hbm_sites = [(10.5, 20), (10.5, 30), (10.5, 40), (49.5, 20), (49.5, 30)]
    for index, (x, y) in enumerate(hbm_sites, 1):
        objects.extend([
            _part(f"hbm-underfill-{index}", f"HBM3 block {index} assumed underfill", "epoxy",
                  [x, y, 0.91], [8, 9, 0.18]),
            _part(f"hbm-{index}", f"HBM3 functional stack {index} / homogeneous silicon aggregate",
                  "silicon", [x, y, 0.51], [8, 9, 0.62], label=f"HBM3 {index}"),
        ])
        # Large aggregate contacts stand in for unresolved real bump arrays.
        # They are deliberately not claimed to be actual H100 bump geometry.
        for contact, delta in enumerate([-2.5, 0, 2.5], 1):
            objects.append(_part(
                f"hbm-contact-{index}-{contact}",
                f"HBM3 {index} aggregate solder contact {contact} (assumed)",
                "solder", [x, y + delta, 0.91], [2.2, 2.2, 0.18], "cylinder"))
        direction = 1 if x < 30 else -1
        for lane, delta in enumerate([-3, -1.5, 0, 1.5, 3], 1):
            objects.append(_part(
                f"hbm-route-{index}-{lane}", f"HBM3 {index} illustrative copper routing lane {lane}",
                "copper", [x + direction * 4.7, y + delta, 1.045], [7.5, 0.7, 0.05]))

    # A sparse, visibly coarse GPU contact field; no nanometre circuit details.
    for row in range(6):
        for col in range(6):
            objects.append(_part(
                f"gpu-contact-{row}-{col}", f"GH100 aggregate solder contact {row + 1}:{col + 1}",
                "solder", [20.5 + 3.8 * col, 20.5 + 3.8 * row, 0.94],
                [1.3, 1.3, 0.12], "cylinder"))

    # Package-level routing and terminal aggregates intentionally resolve at a
    # modest whole-package raster; they are not the SXM connector/pinout.
    for lane in range(14):
        coordinate = 6.6 + lane * 3.6
        objects.extend([
            _part(f"route-x-{lane}", f"Assumed substrate copper lane X {lane + 1}", "copper",
                  [30, coordinate, 1.50], [53, 0.55, 0.06]),
            _part(f"route-y-{lane}", f"Assumed substrate copper lane Y {lane + 1}", "copper",
                  [coordinate, 30, 1.68], [0.55, 53, 0.06]),
        ])
        for row in range(14):
            y = 6.6 + row * 3.6
            objects.append(_part(
                f"terminal-{lane}-{row}", f"Assumed package terminal aggregate {lane + 1}:{row + 1}",
                "solder", [coordinate, y, 2.28], [1.5, 1.5, 0.46], "cylinder"))

    # Peripheral decoupling-like blocks are material aggregates, not an actual
    # board bill of materials or measured locations.
    for edge in [0, 1]:
        y = 4 if edge == 0 else 56
        for col in range(10):
            x = 8 + col * 4.9
            objects.extend([
                _part(f"passive-body-{edge}-{col}", "Assumed peripheral passive / epoxy proxy", "epoxy",
                      [x, y, 1.20], [2.3, 1.5, 0.36]),
                _part(f"passive-contact-{edge}-{col}", "Assumed passive solder aggregate", "solder",
                      [x, y, 1.34], [2.7, 1.5, 0.08]),
            ])

    # These are synthetic test defects, never observations of an NVIDIA device.
    # Delamination lies above the sparse GPU contacts, preserving metal geometry.
    objects.extend([
        _part("gpu-delamination", "Synthetic GH100 / underfill interfacial delamination", "air",
              [22.5, 24, 0.85], [5.0, 4.0, 0.06], role="defect", label="Synthetic delamination"),
        _part("hbm-delamination", "Synthetic HBM3 2 / underfill disbond", "air",
              [8, 30, 0.85], [1.8, 4.0, 0.06], role="defect"),
        _part("hbm-contact-void", "Synthetic void in HBM3 1 aggregate solder contact", "air",
              [10.5, 20, 0.92], [1.3, 1.3, 0.14], "cylinder", "defect"),
        _part("terminal-void", "Synthetic void in an assumed package terminal", "air",
              [46.2, 42.6, 2.28], [0.95, 0.95, 0.30], "cylinder", "defect"),
    ])

    return {
        "schema_version": 1,
        "name": "NVIDIA H100 SXM / reference model",
        "description": "Public-reference H100 SXM 80 GB teaching model: GH100 die and five functional HBM3 blocks, with an assumed interposer, package substrate and coarse interconnects. NVIDIA documents the 814 mm2 die area and five-stack HBM3 configuration; package dimensions, internal geometry and seeded defects are illustrative, not NVIDIA CAD or measured hardware.",
        "size_mm": [60, 60, 2.65],
        "objects": objects,
        "recommended_settings": {
            "resolution": 128, "energy_kev": 80, "angle_deg": 0, "photons": 100000,
            "noise": False, "frequency_mhz": 50, "gate_start_us": 0.34, "gate_end_us": 0.45,
            "focus_mm": 0.85, "probe_x_mm": 22.5, "probe_y_mm": 24,
            "include_defects": True, "seed": 42,
        },
        "reference": {
            "product": "NVIDIA H100 SXM (80 GB HBM3)",
            "summary": "Reference-informed package surrogate, not vendor CAD. Published die area and functional memory configuration are retained; all other geometry is assumed. Synthetic defects demonstrate the existing reduced-order imaging models.",
            "sources": [
                {"id": "h100-whitepaper", "title": "NVIDIA H100 Tensor Core GPU Architecture v1.04, pages 17–18 and 36",
                 "url": "https://resources.nvidia.com/en-us-hopper-architecture/nvidia-h100-tensor-c"},
                {"id": "hopper-architecture", "title": "NVIDIA Hopper Architecture In-Depth (22 March 2022)",
                 "url": "https://developer.nvidia.com/blog/nvidia-hopper-architecture-in-depth/"},
                {"id": "h100-product", "title": "NVIDIA H100 product specifications (accessed 4 September 2026)",
                 "url": "https://www.nvidia.com/en-us/data-center/h100/"},
            ],
            "published_facts": [
                {"label": "GH100 die area", "value": "814 mm²", "source_ids": ["h100-whitepaper", "hopper-architecture"]},
                {"label": "GH100 fabrication", "value": "80 billion transistors; TSMC 4N customized for NVIDIA", "source_ids": ["h100-whitepaper", "hopper-architecture"]},
                {"label": "H100 SXM memory", "value": "80 GB HBM3; five functional HBM3 stacks", "source_ids": ["h100-whitepaper", "hopper-architecture", "h100-product"]},
                {"label": "Full GH100 architecture", "value": "Six HBM stack interfaces; distinct from the five-stack H100 SXM configuration", "source_ids": ["h100-whitepaper", "hopper-architecture"]},
            ],
            "assumptions": [
                "60 × 60 × 2.65 mm is a chosen simulation envelope, not a published H100 package or SXM module dimension.",
                "The die is 26.7 × (814 / 26.7) mm with a chosen 0.67 mm thickness: area is published; rectangular aspect ratio and thickness are assumed.",
                "Five homogeneous silicon HBM blocks represent the functional memory configuration. Their 8 × 9 × 0.62 mm dimensions, placement and material simplification are assumed; internal DRAM dies, TSVs and microbumps are unresolved.",
                "Five functional blocks do not establish the real physical population of six possible sites. The sixth site's occupancy is unknown here and is omitted from this simplified geometry.",
                "Interposer, underfill, substrate, copper lanes, coarse contacts, terminals and passive blocks are illustrative. The terminal field is not an SXM connector or actual H100 pinout.",
                "The organic substrate uses the existing FR-4 composite proxy, underfill uses the epoxy proxy, and solder uses pure tin; none is a verified H100 material composition.",
                "Air-filled delaminations and voids are intentionally seeded synthetic defects, not claims of NVIDIA product defects. Air cavities remain sealed in the model.",
                "The heat spreader, cooling assembly, complete SXM board and transistor circuitry are omitted. Top-entry immersion SAM assumes an exposed package with no overlying lid.",
                "Whole-package sampling is 468.75 µm laterally at 128 pixels or 312.5 µm at 192 pixels; coarse contacts and small voids are not metrology-resolved. Sampling is not physical instrument resolution.",
                "X-ray and SAM use the workbench's uncalibrated reduced-order forward models. No experimental H100 validation, electromagnetic simulation, thermal field or CUDA execution is provided by this specimen.",
            ],
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parents[1] / "examples" / "nvidia-h100-sxm.json")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(h100(), handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print(args.output)


if __name__ == "__main__":
    main()
