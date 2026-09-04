# Independent H100 specimen review

Reviewed 4 September 2026 against NVIDIA sources and the generated specimen in `tools/build_h100_example.py`.

## Public facts and modeling boundary

The [NVIDIA Hopper architecture article](https://developer.nvidia.com/blog/nvidia-hopper-architecture-in-depth/) documents a GH100 die area of 814 mm² and distinguishes the full six-stack GH100 architecture from the H100 SXM5 configuration with 80 GB HBM3 and five stacks. The specimen preserves those distinctions. Its five HBM bodies represent the functional memory configuration; they do not establish the real physical population of six possible sites.

The [official H100 whitepaper landing page](https://resources.nvidia.com/en-us-hopper-architecture/nvidia-h100-tensor-c) links to a [71-page PDF](https://dam-cdn.nvd.orangelogic.com/AssetLink/705n6ur546g0uk43w0117r17n8042d73.pdf). Printed pages 17–18 corroborate the die area and product/full-architecture distinction. Printed page 36 describes HBM stacks on the same package as the GPU. The [current product specifications](https://www.nvidia.com/en-us/data-center/h100/) identify H100 SXM with 80 GB memory and 3.35 TB/s bandwidth.

The retrieved sources do not supply a manufacturing stackup, material bill, measured layer thicknesses, die aspect ratio, complete package dimensions, or a bump/terminal map. The specimen labels those as assumptions. Its internal materials use the workbench's existing proxies. Seeded defects are synthetic. The model assumes an exposed package for top-entry acoustic inspection and omits the lid, cooler and complete SXM board.

## Executed independent checks

An independent Python check loaded the generated twin through the public Pydantic schema and validated its recommended acquisition settings. It then sampled the geometry at every supported resolution and ran the complete 128 × 128 acquisition with defects enabled and disabled.

- **Geometry:** 360 primitives fit inside the 60 × 60 × 2.65 mm assumed envelope. The modeled GH100 rectangle has area exactly 814 mm². Five HBM body primitives are present.
- **Material overwrite:** at 64, 128 and 192 pixels per side, both delamination centers replace epoxy with air; both void centers replace solder with air. All four defects survive the grid at their centers. GPU aggregate contacts begin below the seeded GPU delamination.
- **Default gate:** the 0.34–0.45 µs interval captures the chosen GH100/underfill response. At the recommended 22.5 mm, 24 mm probe, the sampled gated amplitude changes from approximately 0.17969 for intact geometry to 0.24758 with defects. The gated A-scan envelope peak occurs at 0.355 µs in both cases. These are synthetic relative amplitudes.
- **Numerical output:** both acquisitions returned finite X-ray and acoustic arrays. The default X-ray range was approximately 0.20084–0.97710 relative transmission.
- **Sampling:** lateral pitch is 937.5, 468.75 or 312.5 µm at 64, 128 or 192 pixels per side. The default run emits 54 sampling warnings. Small features and the modeled focal spot remain undersampled; even when a defect center survives, its shape and amplitude are not established by that fact.

These checks establish bounded geometry, intended material replacement and usable synthetic contrast. They do not establish experimental H100 fidelity, physical microscope resolution, a convergence result, or defect detection accuracy.
