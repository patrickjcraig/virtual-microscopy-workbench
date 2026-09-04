# Independent H100 specimen review

Reviewed 4 September 2026 against NVIDIA sources and the generated specimen in `tools/build_h100_example.py`. The current M1 review follows; the original five-body model's numerical record is retained below and marked historical.

## M1: six physical sites and editable layered assemblies

The current fixture has **six physical HBM sites**, three on either side of the GH100 die. Its initial state records five enabled sites and an unknown sixth-site functional state. This corrects the original model's conflation of enabled memory count with physical bodies. The [NVIDIA Hopper package rendering](https://developer.nvidia.com/blog/nvidia-hopper-architecture-in-depth/) depicts six peripheral bodies; its five-stack SXM specification establishes enabled memory configuration, not the sixth body's exact internal construction.

The 472-primitive default model has six assumed 8-high assemblies. Each contains a 70 µm silicon base, eight 50 µm silicon DRAM dies, eight 15 µm epoxy interfaces and a 30 µm epoxy cap, plus 180 µm coarse attachment regions. All six 12-high templates, using 34 µm dies and 8 µm gaps, produce 520 primitives. [SK hynix's HBM3 stack construction reference](https://news.skhynix.com/en/meet-the-sk-hynix-team-behind-the-worlds-first-12-layer-hbm3/) supports the available die-count templates; it does not establish these dimensions or the construction of the supplied image. TSVs, microbumps and detailed redistribution remain unresolved.

Current edits are governed by `hbm_assemblies`; descriptive provenance explicitly describes the initial/default fixture. Per-stack evidence is independent of selected die count and functional state. Editing a stack to 12-high or marking the sixth enabled therefore does not leave a false current 8-high/unknown description. Imported evidence text is preserved rather than rewritten.

The image metadata retains the supplied raster's 693 × 502 dimensions, SHA-256 and **user-estimated 4.6 µm/pixel scale**. Its approximate 3.19 × 2.31 mm field is conditional on that scale applying to the exact raster. The image is structural guidance; source, orientation, acquisition type, resizing history, variant and detailed feature identities are not established. The public JSON does not include image bytes or private local paths.

### Executed M1 numerical and API checks

The focused [H100 test file](../tests/test_h100.py) passed **20 tests**. An independent reviewer also ran the [ROI/API test file](../tests/test_roi.py), with **20 tests passed**. Checks cover:

- Six-site positions, explicit presence and enabled/unknown state, valid bounds, contiguous physical layers, 472/520 object limits and JSON/generator consistency.
- 8-high and 12-high editing, rejection of the too-tall 12-high/default-thickness combination, unchanged original input and preservation of unrelated geometry/defects.
- Electrical state changes preserving primitive arrays and sampled material labels. Physical removal is an explicit separate action; restoration preserves seeded-defect precedence.
- Actual HBM cross-section materials at approximately 0.25 mm (DRAM silicon), 0.2875 mm (epoxy gap) and 0.79 mm (silicon base), with correct global XZ/YZ orientation.
- Full-depth ROI sampling retaining underlying interposer/substrate material. Normal-incidence ROI transmission and acoustic travel time agree with independent layered calculations; a 1024-cell depth grid recovers a known thin layer that a coarse grid misses.
- ROI coordinate offsets, probe boundaries, invalid ROI imports and the contribution of material outside the displayed ROI to the acoustic point-spread response.
- Existing 128/192-pixel healthy/defective X-ray and SAM comparisons, with all four synthetic defect centers retained.

The reviewer found a material-precedence bug in an early draft: an otherwise valid untagged copper film interleaved between generated HBM layers could move behind all layers during recompilation, changing silicon to copper during an electrical-state-only edit. **Import validation now requires every assembly's exact compiled primitives to form one contiguous ordered block.** A regression reproduces the interleaved film, confirms the original sampled material is silicon, and verifies both import and metadata-only recomposition reject it before any reordering. Compiled layer/metadata disagreement, unknown assembly tags and out-of-bounds geometry are also rejected.

The current ROI is an x/y selection at **0° X-ray incidence with full specimen z extent**; it is not an isolated cropped-depth chiplet. Material sections show model truth and are not reconstructed volumes. These checks verify implementation semantics and synthetic numerical behavior, not experimental H100 fidelity, calibrated microscope resolution, convergence of all features or measured defect detectability. Multi-view projection datasets, CT/laminography reconstruction and saved SAM RF volumes remain subsequent milestones.

## Historical record: original five-body homogeneous fixture

The following source and numerical record describes the **earlier 360-primitive model**, not the current six-site layered fixture. Its measured numerical ranges, warning counts and five-body description are retained only as historical evidence.

### Original public facts and modeling boundary

The [NVIDIA Hopper architecture article](https://developer.nvidia.com/blog/nvidia-hopper-architecture-in-depth/) documents a GH100 die area of 814 mm² and distinguishes the full six-stack GH100 architecture from the H100 SXM5 configuration with 80 GB HBM3 and five stacks. The specimen preserves those distinctions. Its five HBM bodies represent the functional memory configuration; they do not establish the real physical population of six possible sites.

The [official H100 whitepaper landing page](https://resources.nvidia.com/en-us-hopper-architecture/nvidia-h100-tensor-c) links to a [71-page PDF](https://dam-cdn.nvd.orangelogic.com/AssetLink/705n6ur546g0uk43w0117r17n8042d73.pdf). Printed pages 17–18 corroborate the die area and product/full-architecture distinction. Printed page 36 describes HBM stacks on the same package as the GPU. The [current product specifications](https://www.nvidia.com/en-us/data-center/h100/) identify H100 SXM with 80 GB memory and 3.35 TB/s bandwidth.

The retrieved sources do not supply a manufacturing stackup, material bill, measured layer thicknesses, die aspect ratio, complete package dimensions, or a bump/terminal map. The specimen labels those as assumptions. Its internal materials use the workbench's existing proxies. Seeded defects are synthetic. The model assumes an exposed package for top-entry acoustic inspection and omits the lid, cooler and complete SXM board.

### Original executed independent checks

An independent Python check loaded the generated twin through the public Pydantic schema and validated its recommended acquisition settings. It then sampled the geometry at every supported resolution and ran the complete 128 × 128 acquisition with defects enabled and disabled.

- **Geometry:** 360 primitives fit inside the 60 × 60 × 2.65 mm assumed envelope. The modeled GH100 rectangle has area exactly 814 mm². Five HBM body primitives are present.
- **Material overwrite:** at 64, 128 and 192 pixels per side, both delamination centers replace epoxy with air; both void centers replace solder with air. All four defects survive the grid at their centers. GPU aggregate contacts begin below the seeded GPU delamination.
- **Default gate:** the 0.34–0.45 µs interval captures the chosen GH100/underfill response. At the recommended 22.5 mm, 24 mm probe, the sampled gated amplitude changes from approximately 0.17969 for intact geometry to 0.24758 with defects. The gated A-scan envelope peak occurs at 0.355 µs in both cases. These are synthetic relative amplitudes.
- **Numerical output:** both acquisitions returned finite X-ray and acoustic arrays. The default X-ray range was approximately 0.20084–0.97710 relative transmission.
- **Sampling:** lateral pitch is 937.5, 468.75 or 312.5 µm at 64, 128 or 192 pixels per side. The default run emits 54 sampling warnings. Small features and the modeled focal spot remain undersampled; even when a defect center survives, its shape and amplitude are not established by that fact.

These checks establish bounded geometry, intended material replacement and usable synthetic contrast. They do not establish experimental H100 fidelity, physical microscope resolution, a convergence result, or defect detection accuracy.
