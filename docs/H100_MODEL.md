# NVIDIA H100 SXM specimen

The workbench includes **NVIDIA H100 SXM / reference model**: an exposed GPU-package teaching specimen with a GH100 die, six physical HBM sites containing editable layered assemblies, an illustrative silicon interposer and substrate, and coarse material-labeled interconnects. It runs through the same X-ray and scanning acoustic microscopy forward models as the other examples. Physical construction and functional memory state are separate.

This is a public-reference geometric surrogate. It is not NVIDIA CAD, a measured digital twin, a transistor simulation, or a validated inspection of a real H100. NVIDIA and H100 are NVIDIA trademarks; this project is independent and is not endorsed by NVIDIA.

## Reference evidence

NVIDIA reports an **814 mm² GH100 die**, 80 billion transistors and a customized TSMC 4N process. Its H100 SXM configuration has **80 GB HBM3 across five stacks**; the full GH100 architecture supports six stacks. The memory stacks share the GPU package. These distinctions are documented in the [NVIDIA H100 architecture whitepaper v1.04, printed pages 17–18 and 36](https://resources.nvidia.com/en-us-hopper-architecture/nvidia-h100-tensor-c), and the [NVIDIA Hopper architecture article](https://developer.nvidia.com/blog/nvidia-hopper-architecture-in-depth/). The [current H100 product page](https://www.nvidia.com/en-us/data-center/h100/) also confirms SXM 80 GB. References were checked on 4 September 2026.

NVIDIA's package rendering depicts six peripheral bodies. The initial fixture therefore includes six physical sites, three on each side of the GPU, with five marked enabled and the sixth's functional state marked unknown. The rendering and enabled memory count do **not** establish the sixth body's internal construction. It uses the same explicitly assumed layered template until specimen-specific evidence becomes available. Disabling a stack electrically never removes material from imaging; `physical_present` is a separate explicit parameter.

[SK hynix describes eight-layer 16 GB and twelve-layer 24 GB HBM3 constructions](https://news.skhynix.com/en/meet-the-sk-hynix-team-behind-the-worlds-first-12-layer-hbm3/). This supports offering 8-high and 12-high templates, but does not identify the construction in this H100 or the supplied cross-section. Neither template's chosen dimensions is a measured product specification.

Published reference facts and modeling assumptions are separate fields in the twin JSON. Every published fact identifies its supporting source IDs. These records travel with the imported specimen and acquisition export. Reference descriptions document the initial fixture; **`hbm_assemblies` records the current editable construction and functional state**.

## Initial fixture geometry

No exact package drawing, HBM dimensions, layer stack, bump map, or material bill of materials was recovered from the cited NVIDIA documents. All dimensions below are **modeling choices**, except the die's footprint area.

| Component | Representation | Evidence status |
| --- | --- | --- |
| Specimen envelope | 60 × 60 × 2.65 mm | Assumed simulation extent; not SXM module dimensions |
| GH100 die | 26.7 × (814 / 26.7) × 0.67 mm | Published area; assumed aspect ratio and thickness |
| Six physical HBM sites | Each 8 × 9 mm; initial height 0.62 mm | Assumed dimensions and placement |
| HBM base die | One 70 µm silicon layer per stack | Assumed thickness/material |
| HBM DRAM dies | Eight 50 µm silicon layers per initial stack | Assumed dimensions; no circuit/TSV topology |
| HBM interfaces | Eight 15 µm epoxy layers | Base-to-first-die plus seven inter-die interfaces; no actual microbump array |
| HBM cap | One 30 µm epoxy layer per stack | Assumed cap/molding proxy |
| Silicon interposer | 50 × 44 × 0.22 mm | Assumed package topology and dimensions |
| Die / HBM underfill | 0.18 mm thick | Assumed epoxy surrogate |
| Interposer attach | 0.16 mm thick | Assumed epoxy surrogate |
| Organic substrate | 60 × 60 × 0.67 mm | Assumed; existing FR-4 material proxy |
| Copper routing and planes | Coarse lanes and planar features | Illustrative, not an H100 routing map |
| Die/HBM contacts and terminal field | Coarse tin cylinders, including 14 × 14 package terminal aggregates | Illustrative sampling features; not an SXM connector or pinout |
| Peripheral passive blocks | Simple epoxy/solder aggregates | Assumed placement/materials; not an actual bill of materials |

The default file contains **472 ordered primitives**, including four synthetic defects. Later primitives overwrite earlier material, following the existing CSG convention. The top plane is `z = 0`, and increasing z travels into the specimen. The GH100 top lies at 0.15 mm; its underside is at 0.82 mm. Default HBM stacks begin at 0.20 mm and end at the same underside plane. Water fills unoccupied space for SAM. The model omits a lid, cooling hardware, the full SXM board and functional electronic circuitry.

The material library remains unchanged: silicon, copper, the existing pure-tin solder proxy, epoxy proxy and FR-4 composite proxy. Neither the polymer formulation nor substrate composition is verified for H100. HBM silicon/epoxy layers are explicit, but **TSVs, true microbumps, pads and redistribution microstructure remain unresolved**. The three coarse attachment contacts per stack are not predictions of real microscopic texture.

## Editable HBM assemblies and safe imports

`hbm_assemblies` stores each stack's ID, name, x/y center, footprint, bottom depth, die count, die/gap/base/cap thicknesses, physical presence, functional state and evidence note. Thickness fields use micrometres; positions and footprints use millimetres. Stack height is:

`base_thickness_um + die_count × (die_thickness_um + gap_um) + cap_thickness_um`.

The default 8-high template totals **620 µm**. The alternate 12-high template uses **34 µm dies and 8 µm gaps**, retaining the 70 µm base and 30 µm cap, for **604 µm** total. All six 12-high stacks produce **520 primitives**, below the 600-object v1 limit. Choosing 12 dies without reducing the default die/gap dimensions would exceed the available height and is rejected.

`virtual_microscopy.hbm.compose_hbm` regenerates the selected assembly as a pure operation. Its underfill and coarse contacts follow the stack position/footprint; unrelated package routing and all seeded defects retain their original coordinates. Moving a stack does not silently move a fixed defect. The compiler places one display label on each cap while recording each internal primitive's `assembly_id` and `layer_role`.

Import validation requires the generated primitives to match the assembly parameters **exactly and in one contiguous ordered block**. Unknown assembly tags, missing/extra/modified layers, out-of-bounds geometry and interleaved foreign objects are rejected. Contiguity protects material precedence: regrouping an interleaved copper feature could otherwise change the physical model during an electrical-state-only update. An absent stack has no compiled primitives; restoring it inserts its block before the first defect so seeded defects retain their precedence.

## Cross-section reference and local inspection

The supplied **693 × 502 pixel** cross-section has a user-estimated scale of **approximately 4.6 µm/pixel**, corresponding to approximately **3.19 × 2.31 mm** if that scale applies to the exact raster. The twin records `scale_status: "user_estimate"`, dimensions, source limitations and SHA-256 `e2b1274b5593236fff9c9a3183cd6f73808f890ab2acca32e267c057ec8d12d9`. It does not publish image bytes or a private local path.

This is structural guidance, not a calibration or measured-device validation set. The source, acquisition type, orientation, H100 variant, resizing history and exact material/interface identities remain unverified. Model dimensions were not fitted to the image.

The M1 ROI is an x/y selection with **the complete specimen depth retained**, so material beneath the selected HBM remains in the X-ray path and acoustic calculation. ROI X-rays currently require **normal incidence (0°)**; full-specimen tilted projections remain available. Lateral raster size and depth samples can be chosen separately, with depth sampling up to 1024 cells. At 1024 samples over 2.65 mm, depth pitch is approximately 2.59 µm. A material cross-section shows the model's material truth, not a reconstructed X-ray or SAM volume. Smaller grid pitch does not establish instrument resolution, and unmodeled microbumps cannot appear merely by increasing sampling.

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

The gate includes the GPU underside echo: an idealized vertical path gives approximately `2 × 0.15 / 1.48 + 2 × 0.67 / 8.43 = 0.362 µs`, including water above the exposed die. This is measured from the specimen top plane; transducer water standoff is excluded. Voxel sampling shifts interfaces slightly. Layered HBM introduces additional silicon/epoxy echoes and material-dependent travel times; its response depends on the selected parameters and sampling.

## Verification and limitations

The focused H100 tests check reproducible generation, safe refusal to overwrite an existing file, valid presets, six-site geometry, layer contiguity, 8/12-high templates, functional-state material invariance, preservation of unrelated objects and defects, physical presence/restoration, reference serialization and strict rejection of divergent or interleaved assembly imports. They retain healthy/defective X-ray and SAM comparisons at 128 and 192 pixels and verify all four defect centers. The M1 focused file passed **20 tests**; this is numerical verification, not measured-device calibration.

Historical deterministic results from the earlier five-body homogeneous HBM fixture are retained below as a baseline. They are **not a measurement of the current six-site layered model**:

| Raster | Lateral sampling pitch | GH100 probe C-scan, healthy | GH100 probe C-scan, defect | Largest X-ray transmission change |
| --- | --- | --- | --- | --- |
| 128 × 128 | 468.75 µm | 0.17969 | 0.24758 | 0.30793 I/I₀ |
| 192 × 192 | 312.50 µm | 0.17967 | 0.24755 | 0.29647 I/I₀ |

These values verify that the synthetic preset exposes the seeded changes. They are **not measured H100 amplitudes, defect detectability, resolution, probability of detection, or calibrated instrument predictions**. The variation between raster sizes illustrates geometry sampling effects. Pixel pitch greatly exceeds the modeled acoustic focal spot and detector blur, so the software reports undersampling. Small contacts and void edges are only a few pixels wide; real microbump and TSV inspection needs a local region of interest with much finer geometry and sampling.

The existing [physics model](PHYSICS.md) remains the limiting model: monochromatic Beer–Lambert X-ray projection and primary normal-incidence longitudinal acoustic echoes with illustrative focus, bandwidth and losses. It does not calculate scatter, polychromatic beam hardening, acoustic reverberation, mode conversion or full elastic wave propagation. M1 does not yet create a multi-view projection dataset, CT reconstruction or saved SAM RF volume; those remain later expansion milestones. It also does not add thermal/electrical coupling or GPU execution.

To reproduce the twin in a **new** location:

```powershell
.\.venv\Scripts\python.exe tools\build_h100_example.py --output artifacts\h100-copy.json
```

The builder refuses to replace an existing file. The distributed twin is at [examples/nvidia-h100-sxm.json](../examples/nvidia-h100-sxm.json); focused verification is at [tests/test_h100.py](../tests/test_h100.py).
