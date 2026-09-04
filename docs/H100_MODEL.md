# NVIDIA H100 SXM specimen

The workbench includes **NVIDIA H100 SXM / reference model**: an exposed GPU-package teaching specimen with a GH100 die, five functional HBM3 blocks, an illustrative silicon interposer and substrate, and coarse material-labeled interconnects. It runs through the same X-ray and scanning acoustic microscopy forward models as the other examples.

This is a public-reference geometric surrogate. It is not NVIDIA CAD, a measured digital twin, a transistor simulation, or a validated inspection of a real H100. NVIDIA and H100 are NVIDIA trademarks; this project is independent and is not endorsed by NVIDIA.

## Reference evidence

NVIDIA reports an **814 mm² GH100 die**, 80 billion transistors and a customized TSMC 4N process. Its H100 SXM configuration has **80 GB HBM3 across five stacks**; the full GH100 architecture supports six stacks. The memory stacks share the GPU package. These distinctions are documented in the [NVIDIA H100 architecture whitepaper v1.04, printed pages 17–18 and 36](https://resources.nvidia.com/en-us-hopper-architecture/nvidia-h100-tensor-c), and the [NVIDIA Hopper architecture article](https://developer.nvidia.com/blog/nvidia-hopper-architecture-in-depth/). The [current H100 product page](https://www.nvidia.com/en-us/data-center/h100/) also confirms SXM 80 GB. References were checked on 4 September 2026.

The model draws five functional HBM blocks to communicate that configuration. The enabled stack count does **not** determine the physical population of all possible package sites. This model makes no claim about whether a sixth physical site on a real unit is populated or disabled; that site is omitted.

Published reference facts and modeling assumptions are separate fields in the twin JSON. Every published fact identifies its supporting source IDs. These records travel with the imported specimen and acquisition export.

## Chosen geometry

No exact package drawing, HBM dimensions, layer stack, bump map, or material bill of materials was recovered from the cited NVIDIA documents. All dimensions below are **modeling choices**, except the die's footprint area.

| Component | Representation | Evidence status |
| --- | --- | --- |
| Specimen envelope | 60 × 60 × 2.65 mm | Assumed simulation extent; not SXM module dimensions |
| GH100 die | 26.7 × (814 / 26.7) × 0.67 mm | Published area; assumed aspect ratio and thickness |
| Five HBM3 functional blocks | Each 8 × 9 × 0.62 mm, homogeneous silicon | Assumed dimensions and placement; internal memory layers unresolved |
| Silicon interposer | 50 × 44 × 0.22 mm | Assumed package topology and dimensions |
| Die / HBM underfill | 0.18 mm thick | Assumed epoxy surrogate |
| Interposer attach | 0.16 mm thick | Assumed epoxy surrogate |
| Organic substrate | 60 × 60 × 0.67 mm | Assumed; existing FR-4 material proxy |
| Copper routing and planes | Coarse lanes and planar features | Illustrative, not an H100 routing map |
| Die/HBM contacts and terminal field | Coarse tin cylinders, including 14 × 14 package terminal aggregates | Illustrative sampling features; not an SXM connector or pinout |
| Peripheral passive blocks | Simple epoxy/solder aggregates | Assumed placement/materials; not an actual bill of materials |

The file contains **360 ordered primitives**, including four synthetic defects. Later primitives overwrite earlier material, following the existing CSG convention. The top plane is `z = 0`, and increasing z travels into the specimen. The GH100 top lies at 0.15 mm; its underside is at 0.82 mm. HBM blocks begin at 0.20 mm and end at the same underside plane. Water fills unoccupied space for SAM. The model omits a lid, cooling hardware, the full SXM board and functional electronic circuitry.

The material library remains unchanged: silicon, copper, the existing pure-tin solder proxy, epoxy proxy and FR-4 composite proxy. Neither the polymer formulation nor substrate composition is verified for H100. The simplified homogeneous HBM blocks omit DRAM layers, TSVs and true microbumps. The coarse contacts are not predictions of real microscopic texture.

## Seeded inspection examples

All four defects are artificial demonstrations; they are not evidence of defects in NVIDIA hardware.

| Synthetic feature | Center x/y/z (mm) | Extent (mm) | Intended inspection |
| --- | --- | --- | --- |
| GH100 / underfill delamination | 22.5 / 24 / 0.85 | 5 × 4 × 0.06 box | Increased gated echo at the die underside |
| HBM block 2 / underfill disbond | 8 / 30 / 0.85 | 1.8 × 4 × 0.06 box | Local acoustic interface change |
| HBM block 1 aggregate-contact void | 10.5 / 20 / 0.92 | 1.3 diameter × 0.14 high cylinder | Reduced solder attenuation and changed echo |
| Package terminal void | 46.2 / 42.6 / 2.28 | 0.95 diameter × 0.30 high cylinder | Higher X-ray transmission through the terminal |

The GH100 air gap lies between z = 0.82 and 0.88 mm; the coarse GPU contacts start at z = 0.88 mm. The delamination therefore changes the upper interface without erasing those contacts. Explicit cavities remain sealed air in the immersion model.

Select the H100 specimen and run its preset: **128 × 128 pixels, 80 keV, no counting noise, 50 MHz acoustics, focus 0.85 mm, gate 0.34–0.45 µs, probe (22.5, 24) mm**. Compare acquisitions with **Include embedded defects** enabled and disabled. Disabling noise makes this synthetic comparison deterministic. A separate 192-pixel acquisition gives a finer raster without changing the assumed geometry.

The gate includes the GPU underside echo: an idealized vertical path gives approximately `2 × 0.15 / 1.48 + 2 × 0.67 / 8.43 = 0.362 µs`, including water above the exposed die. This is measured from the specimen top plane; transducer water standoff is excluded. Voxel sampling shifts interfaces slightly. The HBM underside arrives later because its top is recessed by another 0.05 mm and water carries sound more slowly than silicon.

## Verification and limitations

The focused tests validate the file and recommended settings, check generator reproducibility and safe refusal to overwrite existing files, preserve provenance through JSON serialization, and compare healthy/defective numerical outputs at both 128 and 192 pixels. All four defect centers are represented as air at both resolutions.

Executed deterministic results at the recommended settings:

| Raster | Lateral sampling pitch | GH100 probe C-scan, healthy | GH100 probe C-scan, defect | Largest X-ray transmission change |
| --- | --- | --- | --- | --- |
| 128 × 128 | 468.75 µm | 0.17969 | 0.24758 | 0.30793 I/I₀ |
| 192 × 192 | 312.50 µm | 0.17967 | 0.24755 | 0.29647 I/I₀ |

These values verify that the synthetic preset exposes the seeded changes. They are **not measured H100 amplitudes, defect detectability, resolution, probability of detection, or calibrated instrument predictions**. The variation between raster sizes illustrates geometry sampling effects. Pixel pitch greatly exceeds the modeled acoustic focal spot and detector blur, so the software reports undersampling. Small contacts and void edges are only a few pixels wide; real microbump and TSV inspection needs a local region of interest with much finer geometry and sampling.

The existing [physics model](PHYSICS.md) remains the limiting model: monochromatic Beer–Lambert X-ray projection and primary normal-incidence longitudinal acoustic echoes with illustrative focus, bandwidth and losses. It does not calculate scatter, polychromatic beam hardening, acoustic reverberation, mode conversion, or full elastic wave propagation. Adding the H100 specimen does not add thermal/electrical coupling or GPU execution.

To reproduce the twin in a **new** location:

```powershell
.\.venv\Scripts\python.exe tools\build_h100_example.py --output artifacts\h100-copy.json
```

The builder refuses to replace an existing file. The distributed twin is at [examples/nvidia-h100-sxm.json](../examples/nvidia-h100-sxm.json); focused verification is at [tests/test_h100.py](../tests/test_h100.py).
