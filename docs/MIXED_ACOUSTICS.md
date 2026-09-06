# Mixed finite-media acoustics

Version 0.20 adds a standalone instrument for explicitly authored stacks containing
both lossless real media and dispersive standard-linear-solid (SLS) media. A finite
water spacer can use impedance and sound speed directly. Each SLS layer retains
its own density, relaxed and unrelaxed longitudinal moduli, and relaxation time.
These are assumed scalar material laws, not measured HBM calibration.

## Use the instrument

Open **Mixed finite media** from the workbench toolbar. Stage an assumed example,
or author the incident medium, terminal medium and ordered finite layers.

1. Choose **Add real layer** or **Add SLS layer**. Give each layer its own name,
   thickness and explicit material values. Reorder or remove rows as needed.
2. Choose a frequency range and sample count. Frequency-only mode computes
   complex pressure reflection and transmission, material curves and energy
   diagnostics at those actual frequencies.
3. Enable reflected causal gamma RF to also set carrier frequency, bandwidth,
   sample rate, recording start and duration, exterior standoff, gamma order,
   numerical tolerance and arithmetic precision.
4. Review the estimate before saving. Resource or numerical rejection preserves
   the authored settings; it does not reduce the stack or relax the tolerance.
5. Inspect the saved curves and exact cursor values. JSON and lossless CSV exports
   retain the full typed request, arrays, numerical bounds and provenance.

Switching a finite layer's type retains its name, position and thickness while
clearing incompatible material fields. It does not convert impedance and speed
into a rounded SLS surrogate. Previously accepted results remain attached to their
saved request when the editor changes.

## Explicit finite-layer types

| Type | Required material inputs | Interpretation |
| --- | --- | --- |
| `lossless_real` | Impedance, MRayl; sound speed, m/s | Constant positive real impedance and speed, with no independent attenuation or relaxation input. |
| `sls` | Density, kg/m³; relaxed and unrelaxed longitudinal moduli, GPa; relaxation time, µs | Passive single-relaxation scalar longitudinal law, with unrelaxed modulus at least the relaxed modulus. |

Both types also require a name and thickness in millimetres. At most **eight
authored finite layers** are admitted. Zero-thickness rows count toward this
limit and retain their authored material diagnostics, while contributing no
physical propagation or interfaces. The exact represented thickness sum must
not exceed **6 mm**. Positive layers are never cropped, averaged, or merged to
meet these limits.

Real impedance is supported from 0.0001 to 100 MRayl and speed from 100 to
20,000 m/s. SLS density is supported from 1 to 30,000 kg/m³, each modulus from
0.000001 to 1,000 GPa, and relaxation time from 0.000001 to 100 µs. Combinations
inside these individual ranges can still exceed numerical conditioning or
resource limits. The longitudinal modulus is not automatically Young's modulus
or the bulk modulus.

For real media, the numerical kernel uses the authored impedance and speed
directly. Its consistent scalar identities are `rho = Z_SI / c` and
`M = Z_SI * c`. Original represented inputs are enclosed before exact SI
conversion. For example, binary64 1.48 MRayl and 1480 m/s do not silently become
a separately authored exact density of 1000 kg/m³. No relaxation time is invented
for water.

## Reference planes and spacers

Frequency reflection is referenced to the incident face of the finite stack;
transmission is referenced to its terminal face. Exterior standoff is excluded
from those spectra. Incident speed enters the reflected RF exterior delay;
terminal speed remains explicit metadata for this response model.

A finite front layer is part of the stack. If matched in impedance to the
incident medium, it adds a round-trip delay to reflection and a single-pass delay
to transmission. A terminal-matched spacer changes transmission phase while
leaving reflection unchanged. Exterior standoff is a separate explicit reflected
round-trip delay. The instrument does not automatically add, remove or double
count a finite spacer as standoff.

Gamma excitation has its own peak latency. Its recording time and the multiple
reflections in a layered stack do not define a unique depth coordinate.

## What the outputs establish

The complex spectra, phase, material dispersion/attenuation and energy fractions
are ordinary rounded diagnostics at the saved frequencies. Wrapped phase is
undefined below the declared pressure-magnitude threshold. Negative rounded
energy residuals are retained. Pressure transmission can exceed one; energy
transmission is separately weighted by the real exterior impedances.

The optional reflected gamma response saves real pressure, quadrature and
separately computed complex magnitude at the actual recorded centers. Its
numerical certificate publishes outward alias, frequency-cutoff, complex-output
conversion and magnitude-output conversion bounds, and a total enclosing the
exact sum of those published components. The total must meet the requested
tolerance and bounds both reflected complex-pressure error and the separately
returned magnitude error over those centers.

The [mixed material proof](MIXED_MATERIAL_PROOF.md) states the scalar equations,
mixed-layer flux balance, outgoing uniqueness, analytic reflected-transfer bound,
gamma periodization and finite-sum arithmetic obligations. A sampled passivity
curve does not replace that global argument. Unresolved branches, denominators,
enclosures or final tolerance reject at the requested settings.

This certificate does not establish measured material accuracy, geometry accuracy,
transmitted RF error, frequency-curve error, gate-statistic error, focusing, shear
conversion, lateral coupling or full elastic-wave behavior.

## Saved reports and resource boundaries

The distinct request/report kind is `mixed_layered_analysis`, with endpoints under
`/api/v2/mixed-acoustics`:

- `POST /estimate`
- `POST /reports` and paged `GET /reports`
- `GET /reports/{id}`
- `GET /reports/{id}/export?format=json|csv`

Reports are published exclusively and atomically in `mixed-reports`. Complete
requests, typed stacks, actual arrays, proof and implementation identities, runtime
metadata and hashes are retained. Historical reads and exports validate frozen
contracts without running the current material kernels or reading current proof
files. CSV preserves each top-level field as JSON in `section,field,value_json`
rows, including nested arrays and exact represented numbers.

Admission retains the bounded spectrum/inverse workloads, 128 MiB numerical
workspace, 16 MiB encoded and 64 MiB expanded report limits, and 256 MiB owned
publication workspace. Retained spectra, report objects, parsing and export copies
enter the forecast. These are conservative owned-workspace estimates, not total
process RSS or performance guarantees. Listing processes one complete report at
a time before retaining its compact summary.

This instrument creates no acquisition, volume, observation or background-worker
job. It accepts manually authored finite stacks. Frozen material assignments and
HBM columns are not inputs to this release: 26/28-segment HBM paths require a
separate extension of layer admission, assignment/ambient resolution and spatial
error accounting. Older instruments and their saved report identities remain
separate.
