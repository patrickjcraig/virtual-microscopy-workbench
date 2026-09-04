"""Build the public-reference H100 teaching specimen, without replacing files.

NVIDIA architectural facts are kept separate from our assumed package geometry.
The model is neither proprietary CAD nor a transistor-resolved GPU simulation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

# Keep direct script execution (including no-overwrite generation) available.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from virtual_microscopy.hbm import compile_hbm_stack


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

    # Six physical positions are independent of the five enabled SXM stacks.
    # The sixth body's detailed construction remains an explicit assumption.
    assemblies = []
    hbm_sites = [(10.5, 20), (10.5, 30), (10.5, 40), (49.5, 20), (49.5, 30), (49.5, 40)]
    for index, (x, y) in enumerate(hbm_sites, 1):
        stack = {
            "id": f"hbm-{index}", "name": f"HBM3 {index}",
            "center_xy_mm": [x, y], "footprint_mm": [8, 9], "bottom_z_mm": 0.82,
            "die_count": 8, "die_thickness_um": 50, "gap_um": 15,
            "base_thickness_um": 70, "cap_thickness_um": 30,
            "functional_state": "enabled" if index <= 5 else "unknown",
            "physical_present": True,
            "evidence": "Assumed silicon/epoxy layer template; current layer count, dimensions and functional state are recorded in this assembly's parameters. Internal construction is unverified for this H100 specimen."
                        + (" References do not establish the sixth physical body's internal population or vendor-enabled state." if index == 6 else ""),
        }
        assemblies.append(stack)
        objects.extend(compile_hbm_stack(stack))
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
        "description": "Public-reference H100 SXM 80 GB teaching model with editable HBM assemblies. The initial fixture has six physical sites: five marked enabled and the sixth with unknown functional state and assumed construction. Current construction and state are recorded in hbm_assemblies. NVIDIA documents the 814 mm2 die area and five enabled HBM3 stacks. Package dimensions, layer parameters, coarse interconnects and seeded defects are illustrative, not NVIDIA CAD or measured hardware.",
        "size_mm": [60, 60, 2.65],
        "objects": objects,
        "hbm_assemblies": assemblies,
        "image_reference": {
            "sha256": "e2b1274b5593236fff9c9a3183cd6f73808f890ab2acca32e267c057ec8d12d9",
            "width_px": 693, "height_px": 502, "pixel_size_um": 4.6,
            "scale_status": "user_estimate",
            "title": "User-supplied HBM cross-section reference",
            "source_note": "User estimates approximately 4.6 um/pixel for the supplied raster; resizing history, instrument, H100 variant, source, orientation and feature identities remain unverified. Used for structural guidance, not fitted layer dimensions or validation. Original image is local and is not included in this public specimen.",
        },
        "recommended_settings": {
            "resolution": 128, "energy_kev": 80, "angle_deg": 0, "photons": 100000,
            "noise": False, "frequency_mhz": 50, "gate_start_us": 0.34, "gate_end_us": 0.45,
            "focus_mm": 0.85, "probe_x_mm": 22.5, "probe_y_mm": 24,
            "include_defects": True, "seed": 42,
        },
        "reference": {
            "product": "NVIDIA H100 SXM (80 GB HBM3)",
            "summary": "Reference-informed package surrogate, not vendor CAD. The initial fixture has six physical HBM sites independently of the five enabled stacks in the published SXM memory configuration. Current edited construction and state are recorded in hbm_assemblies. Internal layers and other package geometry are assumed. Synthetic defects demonstrate reduced-order imaging models.",
            "sources": [
                {"id": "h100-whitepaper", "title": "NVIDIA H100 Tensor Core GPU Architecture v1.04, pages 17–18 and 36",
                 "url": "https://resources.nvidia.com/en-us-hopper-architecture/nvidia-h100-tensor-c"},
                {"id": "hopper-architecture", "title": "NVIDIA Hopper Architecture In-Depth (22 March 2022)",
                 "url": "https://developer.nvidia.com/blog/nvidia-hopper-architecture-in-depth/"},
                {"id": "h100-product", "title": "NVIDIA H100 product specifications (accessed 4 September 2026)",
                 "url": "https://www.nvidia.com/en-us/data-center/h100/"},
                {"id": "hbm3-stacks", "title": "SK hynix: eight-layer 16 GB and twelve-layer 24 GB HBM3 constructions",
                 "url": "https://news.skhynix.com/en/meet-the-sk-hynix-team-behind-the-worlds-first-12-layer-hbm3/"},
            ],
            "published_facts": [
                {"label": "GH100 die area", "value": "814 mm²", "source_ids": ["h100-whitepaper", "hopper-architecture"]},
                {"label": "GH100 fabrication", "value": "80 billion transistors; TSMC 4N customized for NVIDIA", "source_ids": ["h100-whitepaper", "hopper-architecture"]},
                {"label": "H100 SXM memory", "value": "80 GB HBM3; five functional HBM3 stacks", "source_ids": ["h100-whitepaper", "hopper-architecture", "h100-product"]},
                {"label": "Full GH100 architecture", "value": "Six HBM stack interfaces; distinct from the five-stack H100 SXM configuration", "source_ids": ["h100-whitepaper", "hopper-architecture"]},
                {"label": "Package reference rendering", "value": "NVIDIA's Hopper package rendering depicts six peripheral bodies; their individual enabled state and complete internal construction are not established by the rendering.", "source_ids": ["hopper-architecture"]},
                {"label": "Available HBM3 stack templates", "value": "SK hynix describes eight-layer 16 GB and twelve-layer 24 GB HBM3; this does not identify the construction in the user-supplied image.", "source_ids": ["hbm3-stacks"]},
            ],
            "assumptions": [
                "60 × 60 × 2.65 mm is a chosen simulation envelope, not a published H100 package or SXM module dimension.",
                "The die is 26.7 × (814 / 26.7) mm with a chosen 0.67 mm thickness: area is published; rectangular aspect ratio and thickness are assumed.",
                "The initial fixture has six 8 × 9 mm HBM sites using assumed eight-high templates: 70 µm silicon base, eight 50 µm silicon DRAM dies, eight 15 µm epoxy interfaces and a 30 µm epoxy cap; total 620 µm. Current dimensions are recorded in hbm_assemblies. These dimensions and materials are not measured H100 construction. TSVs and microbumps remain unresolved; aggregate attachment contacts are illustrative.",
                "The initial fixture marks five sites functionally enabled and the sixth's state unknown. The sixth physical body's vendor internal population and enabled state are unverified; the fixture uses the same assumed template there. Current chosen state and physical presence are recorded in hbm_assemblies. Changing functional state never removes material; physical presence is a separate explicit parameter.",
                "The alternate twelve-high template uses 34 µm dies and 8 µm gaps (604 µm total with the same base and cap). Template availability is documented, but neither template's dimensions or assignment to this specimen are established by NVIDIA or the image.",
                "The user-supplied 693 × 502 pixel cross-section has an estimated 4.6 µm/pixel scale, corresponding to approximately 3.19 × 2.31 mm if it applies to this exact raster. Scale and interpretation are provisional; model parameters were not fitted to this image.",
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
