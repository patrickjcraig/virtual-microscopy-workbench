# Proposed next loop: bounded layered acoustic reverberation

**Status: planned and unimplemented.** Prepared 5 September 2026 against v0.10. This is a proposed normal-incidence longitudinal layered approximation. It is not a full three-dimensional elastic solver or an experimentally calibrated model.

## Scope

The current SAM pipeline sums primary interface echoes with round-trip transmission and attenuation. Its continuous-column option improves geometry boundaries but does not add repeated reflections. The next increment should add a separately named, opt-in response model that captures coherent internal layer reverberation while retaining the current response as the default. Path construction and acoustic response must remain distinct settings.

Begin with a bounded standalone layered-response kernel and independent analytical fixtures. Define pressure, velocity, impedance, Fourier sign, one-way propagation phase, round-trip delay, attenuation units and terminal boundary conditions in one inspectable contract before implementing it. Establish the lossless solution first. Do not add a frequency-dependent loss law without an explicit frequency/phase convention and checks for passivity and finite behavior.

Then integrate that kernel with continuous material columns for a small saved SAM ROI. Preserve complete specimen contributions, ambient water, explicitly authored air voids, actual object precedence and all six HBM sites. Record the chosen response model, bounds and assumptions in the solver identity and manifest. Historical recipes lacking the new setting retain the primary-echo meaning and their frozen bytes; loading them must never trigger normalization or reacquisition. Resume only under the exact original solver identity.

## Required pressure and boundary contract

Use pressure amplitudes referred to the incident water wave, not intensity coefficients. At an interface from medium `i` to `j`, require `r_ij=(Z_j-Z_i)/(Z_j+Z_i)`, `t_ij=2 Z_j/(Z_i+Z_j)`, `r_ji=-r_ij`, and `t_ij*t_ji=1-r_ij²`. A pressure transmission coefficient can exceed one; energy uses the impedance weighting. These follow the acoustic boundary equations in [Staelin, MIT, section 13.2.1, equations 13.2.19–13.2.21](https://live.ocw.mit.edu/courses/6-013-electromagnetics-and-applications-spring-2009/d3be4ea78b036a6362230fb41780cf54_MIT6_013S09_notes.pdf#page=406).

Proposed convention: Fourier analysis uses `exp(-i*omega*t)`; a positive delay contributes `exp(-i*omega*tau)`. With speeds in mm/us, a layer's one-way factor is `P=exp(-alpha*d)*exp(-i*omega*d/c)`, where `alpha=ln(10)*loss_db_per_mm/20` for pressure. As an independently derived slab oracle, with incident medium 0, slab 1 and terminal medium 2:

```text
R = r01 + t01*t10*r12*P² / (1-r10*r12*P²)
front echo: amplitude r01, time 0
nth internal return, n>=1:
  amplitude t01*t10*r12*(r10*r12)^(n-1)*exp(-2*n*alpha*d)
  time 2*n*d/c
```

Freeze the exterior as semi-infinite water on both sides of the authored specimen, matching the current primary model. The receiving transducer is nonreflecting in this increment: external standoff contributes one outbound and one inbound water path to every received specimen response, with no transducer–specimen cavity. Do not add the top reflection twice or apply the primary model's upstream round-trip factor again to an already complete layered response. Interior ambient water and explicit air remain distinct, including at the bottom termination.

Use `ColumnPaths` before its current `column_acoustic_echoes` conversion: that conversion already removes small primary echoes and lacks the complete scattering history. Merge adjacent equal materials, retain the existing endpoint tolerance/overlap contract, and do not use inactive Z samples to change continuous layer thicknesses. The new response must reject unsupported path/response combinations explicitly.

## Admission and numerical boundaries

Choose and document a stable layer recursion or an explicitly bounded echo-path expansion based on the analytical checks. A proposed control must change an actual model term and have a tested numerical range. Account for layer count, frequency samples or echo paths, padded time record, lateral tiles, retained complex fields and output. Reject a configuration exceeding work/memory limits; never silently drop later echoes, shorten records or replace the response model.

The time response must not wrap an out-of-window echo into the recording. Demonstrate sufficient zero padding or bounded causal construction. Preserve phase, signed RF, absolute-time and water-standoff semantics. Keep the existing lateral beam approximation explicit; do not present independent normal columns as oblique propagation, shear conversion or a focused elastic wave calculation. Any approximated focus weighting must have a stated placement in the response and tests for its effect.

The current `1e-8` per-primary-echo floor is not a bound on the sum of omitted reverberations. Declare an aggregate absolute-error target and bound omitted paths or the circularly wrapped tail; a single slab supplies a geometric-series bound when `abs(r10*r12)*exp(-2*alpha*d)<1`. Highly reflecting air interfaces can require long records or many paths. Reject when the bound cannot be met within the resource budget; never clip a small denominator or rely solely on a visually quiet record edge. A frequency recursion must resolve narrow resonances, test DC/Nyquist and real-signal conjugate symmetry, and pass record-origin/padding invariance against a causal oracle. Naive transfer matrices need particular stability scrutiny in thick absorbing layers; [Lévesque and Piché's NRC record](https://publications-cnrc.canada.ca/eng/view/object/?id=056a54bb-ab25-4f71-8161-43b193d53a23) explicitly identifies this issue.

Keep the lossless kernel and its passivity checks free of focus gains, receive filtering and lateral blur. Any saved ROI integration must declare those observation operations separately and sum complex pressure coherently before computing envelope or gates. Do not sum individual echo envelopes or reuse travel-time-derived depth to focus each multiple: a multiple has no unique reflection depth. Convolve the declared excitation/receive pulse exactly once, retain its existing four-sigma support on both record edges, and preserve the full lateral halo before cropping. Distinguish causal impulse response from the symmetric Gaussian pulse's pre-peak support. Freeze center-frequency loss as a named narrowband approximation if retained; a broadband loss/dispersion model requires its own validated law.

## Acceptance evidence

- One interface: independently derive signed reflection/transmission and verify polarity and round-trip timing in both impedance directions.
- Matched finite layer: no spurious internal reflection; propagation delay follows its thickness and speed.
- One lossless slab: independently enumerate primary and several successive echoes, checking each signed amplitude, spacing and phase. Compare a frequency-domain formulation with the analytical echo series, not a second call to the implementation.
- Lossless conservation/passivity: for real positive exterior impedances, verify `abs(R)²+(Z_incident/Z_terminal)*abs(T)²=1` before focus/receive weighting; passive losses require a result no greater than one within the declared numerical tolerance. Verify attenuation along individual paths. Do not demand monotonic decrease of total reflected amplitude with loss: coherent cancellation and resonances can change it in either direction.
- Thin-layer and sampling sensitivity: vary thickness, RF rate, temporal padding and any truncation bound independently. Report convergence or explicit failure limits rather than treating a plausible plot as proof.
- Degenerate geometry and cancellation: subdividing a homogeneous layer into identical-material sublayers must preserve the response; a zero-thickness slab must reduce to the direct exterior interface. Check a high-contrast thin air layer, a matched backing, and coincident returns whose signed contributions cancel. Proposed lossless float64 kernel tolerances are `1e-10` absolute for normalized coefficients and energy residuals; waveform tolerances must additionally include declared tail and fractional-delay errors, with tightening demonstrated. These are numerical acceptance targets, not experimental accuracy claims.
- Recording and interpretation: changing record start/duration must preserve common RF samples within the declared synthesis tolerance, including echoes centered just outside either window edge. A longer padding calculation must not create early arrivals. Initially reject direct time-to-depth mapping of reverberant sources, or require a separately explicit apparent-depth interpretation with persistent multiple-path disclosure; never label later returns as newly localized physical interfaces.
- Small HBM ROI: demonstrate how layer echoes change recorded RF and gated signals with a controlled pair while preserving source arrays and all other geometry. Use the v0.9 saved SAM comparison workflow.
- Lifecycle: bounded estimate, cancel/resume exactness, historical recordings/recipes, export/reopen, and a native browser workflow with real saved data.

Publish an equation/limitations note, benchmark table and generated application screenshot before marking this increment delivered. If a full ROI integration cannot meet the numerical contract, deliver the validated bounded kernel and its usable comparison instrument as an explicitly narrower increment, then continue integration in the following loop.

## References and later work

The [k-Wave documentation](https://www.k-wave.org/documentation.php) provides a primary reference for the broader heterogeneous acoustic and absorption model landscape. Its [elastic Snell-law example](https://www.k-wave.org/documentation/example_ewp_shear_wave_snells_law.php) distinguishes fluid and elastic simulations and illustrates shear-related behavior. Those are context for the later full-wave stage; they do not validate this proposed layered implementation. The MIT equations above establish the proposed scalar interface convention; cite and reconcile the actual selected multilayer recurrence during implementation rather than treating this planning oracle as completed validation.

Measured transmit/receive responses, elastic mode conversion, oblique beams and experimental calibration remain later work. X-ray spectrum controls still require expanded material/filter attenuation data; cone geometry, GPU backends and refined local all-angle material integration remain separate roadmap increments.
