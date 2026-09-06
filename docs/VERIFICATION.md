# Verification record — 5 September 2026

This record concerns the first local implementation in `E:\git\Dissertation`. It establishes software and analytical-model behavior, not experimental imaging accuracy.

## Version 0.20 — explicit finite real/SLS media

The complete local Windows suite passed **2,157 tests in 476.85 seconds**, with
the same two third-party warnings. The 228 new cases comprise 88 numerical/schema
checks, 122 storage/API checks and 18 independent transfer/inverse checks.
Evidence: `artifacts/v020-final-pytest.log`. Locked Python setup and the final
frontend production build passed; the latter took **1.12 seconds**. No dependency
was added. Exact-commit Windows/Linux CI is retained separately in
`artifacts/v020-release-ci.json` and its GitHub Actions run.

The new typed layer contract rejects omitted or conflicting tags and fields,
nonfinite/boolean values, invalid passive moduli, more than eight authored rows
and exact represented thickness above 6 mm. Zero-thickness entries retain their
type and diagnostics but do not introduce physical interfaces. Enclosed real
impedance/speed propagation avoids a rounded SLS material conversion. The new
[mixed proof](MIXED_MATERIAL_PROOF.md) derives flux cancellation, outgoing
uniqueness, analytic reflected contractivity, gamma causality/periodization and
the finite frequency-tail and returned-value error bounds.

Numerical tests include independent pressure/velocity matrix exponentials,
analytic real-slab coherent spectra and finite causal echo sums, exact
representable real/elastic-SLS equivalence, matched front and terminal spacers,
exterior standoff and an explicit double-counting negative control. They check
actual coordinate retention, exact subdivision, zero thickness, difficult
contrasts, precision/tolerance behavior and rejection before coefficient
allocation. Root's separate p/v/deHoog implementation imports no production
material, scattering, pulse or inverse helper; real and quadrature transforms
are inverted separately.

Historical storage checks cover explicit typed snapshots, every represented
frequency/time coordinate, real-medium curves, ordinary scalar/material
consistency, frozen plan/count relationships, complete hashes and exact outward
component sums. Corruption controls reseal changed payloads to exercise the
structural validators, including jointly modified plans and output diagnostics.
Historical reads require no current schemas, kernels, proof-file lookup or FLINT.
They do not recertify a response. Predecode/retained-page checks, numerical/result
retention, CSV/HTTP accounting, exclusive atomic publication, path/link guards
and kind isolation remain explicit.

The isolated `volumes-v020` delivery root copied and SHA-verified **10,785 files
totaling 536,799,494 bytes**, with an idle online SQLite backup. Startup changed
only **85 existing updated_at values**; IDs, requests, state and every other
catalog field were preserved. All previous files and the post-startup catalog
remained unchanged through delivery. The old backend baseline contains 80 files:
only server registration and package version changed; the other 78 were preserved.
The eight numerical/storage/helper identities and new proof were frozen before
the isolated service and native publication.

Native browser creation passed in **24.682 seconds**, saving exactly three
reports: a frequency-only real slab, an arbitrary relaxing SLS baseline and the
same baseline with a positive matched nominal-water front spacer. Both RF
controls use 10 MHz, bandwidth 0.8, gamma order 12, 200 MHz sampling, 0–0.4 µs,
zero exterior standoff, 192-bit arithmetic and requested tolerance `1e-8`.
They retain 81 actual times; all three spectra retain 257 frequencies over
0–80 MHz. No acquisition or derived job was created.

The exact native verifier reran with retained IDs in **24.825 seconds**, creating
no new reports or jobs. The final strictly read-only browser pass took
**19.843 seconds**, with no POSTs after the ordinary initial preview, no page
errors, all three report files and eight catalogs unchanged, and exact JSON/CSV
equality. Checks cover typed clearing, blanks, reorder/zero/count limits,
rejected recording settings, stale preflight/history responses, cold reopening,
old SLS selector isolation, exact cursors and a 390 px layout. The generated-only
[workspace screenshot](images/mixed-acoustics-workspace.png) shows the actual
water-spacer report and its full reflected-error table; it contains no private
cross-section pixels. Receipts are indexed in `artifacts/v020-native-paths.json`.

Independent read-only delivery passed in **18.270 seconds**, creating no reports
or jobs. It prehashed all three new files before the first HTTP call, checked
canonical JSON/CSV/local identity including signed zero, verified every source
and proof hash, and retained all old files/catalog values. Across **771 saved
frequencies**, independent p/v matrices differed from saved reflected pressure
by at most `2.757e-17` and transmitted pressure by at most `7.528e-17`. The real
slab's maximum rounded energy residual was below `4.650e-16`.

Separate deHoog degrees 72 and 96 checked **six actual RF centers** at
0.125/0.25/0.375 µs across the two RF reports. Maximum degree change was below
`4.718e-24`; maximum selected complex difference was below `1.023e-11` and
magnitude difference below `9.026e-12`. Both published full-record numerical
totals were below `4.792e-9`, meeting the requested `1e-8`. Degree convergence
is not an independently certified oracle remainder and does not tighten those
published bounds.

The controlled finite spacer changes complex reflection by up to **0.264362919**
over the saved spectrum. Its independently checked two-way reflection and
one-way transmission phase relations differ by less than `1.727e-17` and
`9.704e-17`, respectively, with every other layer/exterior/pulse setting
unchanged. These are ordinary diagnostic differences under the authored model,
not detection accuracy or measured material validation. Eight-layer admission,
standalone authorship and reflected-only certification remain the scope; no HBM
assignment, raster or finite-beam propagation is implied.

## Version 0.19 — explicit material assignments and column coverage

The complete local Windows suite passed **1,929 tests in 474.52 seconds**, with
the same two third-party warnings. The 181 new cases comprise 88 core/schema
checks, 75 storage/API checks and 18 independent geometry-oracle checks. Evidence:
`artifacts/v019-final-pytest.log`. Locked dependency setup passed; dependencies
remain unchanged. Exact-commit Windows/Linux CI is recorded separately in
`artifacts/v019-release-ci.json` and its GitHub Actions run.

The core checks strict explicit manual/report-layer origins, units, passive
modulus ordering, required notes, canonical source IDs, selected versus whole
inventory, missing explicit air, occluded materials, full-depth paths and complete
snapshot/resource closure. Only four parameters transfer from a selected source
layer; its thickness, exteriors and numerical certificate remain source provenance.
Eight independently tabulated box/cylinder/sphere cases cover center/chord/tangent,
inclusive walls, overlap order and excluded defects. HBM checks retain all 26/28
segments; assignment count does not bypass the existing eight-layer SLS ceiling.

Storage/API tests remove original sources and parent files, trap current schema,
geometry and acoustic imports, and reopen/export saved columns from their complete
flattened closure. Rehashed incorrect bindings, coverage, labels, endpoints,
diagnostic arithmetic, resource forecasts and identities reject. Predecode tests cover six
retained sources and historical validation reserves. Catalog listing releases
each full record after making its compact summary; final publication rechecks
stream source/parent file hashes without decoding another retained copy. Collision,
path/link, disk and incomplete-publication controls preserve existing files.

All earlier backend files except application version and router registration
remain byte-identical to v0.18. The new instance runs on **8771**, with its own
`artifacts/volumes-v019` root and no auto-reload. **10,776 prior files totaling
536,210,301 bytes** were copied and SHA-256 verified, together with an online
backup of the idle catalog. Startup changed only 85 existing `updated_at` values
(82 acquisition and three observation rows). Complete post-startup catalog values
and earlier files remained unchanged during delivery. The private reference image
retained its original SHA and provisional approximately 4.6 µm/pixel metadata.

The real native workflow delivers exactly four assignment documents and five
saved columns, with no propagation/acquisition jobs:

| Assignment control | Document ID | Saved columns |
| --- | --- | --- |
| Unassigned six-site H100, 574 primitives | `275e347b-57c3-47e6-b0d7-1654ea567a78` | Two HBM6 points, 26 segments each over 2.65 mm |
| Manufactured complete coupon | `c5f4e274-0fba-4f97-a042-04f58dc5bde2` | Five segments over 1 mm |
| Same coupon with unassigned air enabled | `1c8e3fe8-045f-43b6-a273-71887143066a` | Seven segments over 1 mm; air explicitly missing |
| Coupon with one exact saved SLS layer binding | `8c98705b-9e5f-4c83-8bee-15595d5ea3a3` | Five segments over 1 mm |

The HBM points are `(49.42734375,39.876953125)` and
`(49.47421875,39.947265625)` mm. Each coupon column is at `(1,1)` mm. Whole H100
inventory includes six unassigned IDs; the two actual HBM6 columns encounter five
nonambient IDs. The complete/missing-air coupons share identical twin and binding
snapshots, with defect inclusion changed. The manufactured coefficients are
deliberately arbitrary and do not supply HBM properties.

An initial native attempt retained three documents/four columns before source
selection exposed two UI races. The source catalog now survives specimen
recapture, while per-material request identities prevent an older layer response
from changing a newer draft. Deterministic browser-local delayed-response checks
passed with zero POSTs after the initial preview. The completed **35.634-second**
native run reused the retained records and created only the remaining document
and column. It checks exact read-only source values, missing-air/subset coverage,
bad moduli/duplicate/unknown rejection, immutable geometry, source-free history and
JSON exports, keyboard segment selection and 390 px layout, with zero page errors.
Blank coordinates are rejected before conversion rather than silently becoming
zero. Original attempt IDs and failure evidence remain preserved.

Independent read-only delivery passed in **5.124 seconds**, creating no documents,
columns or jobs. It froze all nine native-created file hashes before the first HTTP
read and verified canonical JSON exports/complete parent reconstruction, exact
four-parameter bindings, scope sets and all five expected unique column locators.
It compared **69 positive segments** against separate direct chord equations and
midpoint material precedence, with **zero observed endpoint or thickness-sum
discrepancy**. The endpoint acceptance threshold is 1e-12 mm, a numerical geometry
check rather than experimental accuracy. Numeric material labels, missing
assignment locators and ambient policy also agree. Evidence:
`artifacts/v019-material-assignment-delivery.json`.

The frontend build used for native verification passed in **1.01 seconds**.
After removing duplicate carriage returns from the new JavaScript source, the
final build passed in **1.08 seconds** and every generated asset remained
byte-identical to the browser-verified build. The exact committed native
verifier passed again in **35.664 seconds**, reusing all four documents/five columns
with zero new publications or jobs after the final label and race-check updates.
A final **8.835-second read-only check** verified the actual HBM image, exports,
nine unchanged files and six unchanged catalog responses, with no POSTs after the
initial preview and no page errors. Final paths are in
`artifacts/v019-native-paths.json`; the [actual screenshot](images/material-assignment-workspace.png)
shows the entire physical-depth strip and the saved bump/TSV-core column. No
backend/source fingerprint changed after the server freeze. See
[MATERIAL_ASSIGNMENTS.md](MATERIAL_ASSIGNMENTS.md) for the supported workflow.
Typed finite real/SLS media, larger-stack numerical admission and HBM propagation
remain separate later contracts; complete assignment coverage does not enable them.

## Version 0.18 — saved SLS material comparisons

The complete local Windows suite passed **1,747 tests in 477.31 seconds**, with
two existing third-party warnings. That baseline included 133 new cases across
schema/core/math, storage/API and independent delivery-oracle tests. Evidence:
`artifacts/v018-final-pytest.log`. Locked dependency checks passed with no new
dependencies. The exact published commit's Windows/Linux CI evidence is retained
separately in `artifacts/v018-release-ci.json` and the GitHub Actions run.

The first Linux CI run caught a test fixture that set the candidate system to
`Linux` and expected a runtime difference even when the source already reported
Linux. The final test explicitly covers both Windows and Linux source metadata,
choosing the opposite candidate each time. All **134 final new tests passed in
12.95 seconds**, with the same two warnings; the extra case brings the current
whole-suite collection to 1,748. Evidence is
`artifacts/v018-platform-fixture-final-tests.log`; the first CI run is preserved
separately from final release CI. The correction changes only the test fixture:
production fingerprints, saved reports and native verification remain unchanged.

Tests cover actual binary64 subtraction including signed zero and subnormals,
five exact outward bounds, full/gate reductions and locators, frequency-only
scope, exact represented axes and supported semantics, complete parameter
differences, source changes, bounded decoding and publication. Historical tests
remove the originals and block current schemas, producer, material kernels and
FLINT imports while reading snapshots, exact-index views and JSON/CSV exports.
Resealed incompatible exteriors/pulses, false gates/cursors/locators, understated
bounds, incorrect residuals and corrupted nested identities reject. All earlier
backend files except application version and router registration remain
byte-identical to v0.17, including every source numerical/schema/proof identity.

Port **8770** runs the frozen backend with its own `artifacts/volumes-v018` root.
**10,771 prior dataset/report files totaling 535,701,407 bytes** were copied and
SHA-256 verified, with an online backup of the idle SQLite catalog. Existing
startup reconciliation changed only 85 `updated_at` values (82 acquisition and
three observation records). The full post-startup catalog and all earlier files
remained unchanged during delivery. The private cross-section stayed ignored
and retained its original hash and provisional approximately 4.6 µm/pixel scale.

Two new standalone source controls were prepared in **1.055 seconds** from
existing saved reports, preserving every other request field, including names:

- Elastic source: inactive relaxation time **0.02 → 0.04 µs**; equal relaxed and
  unrelaxed moduli remove the relaxation term algebraically.
- Dispersive source: unrelaxed longitudinal modulus **4 → 4.5 GPa**; density,
  relaxed modulus, relaxation time, geometry, exteriors and recording unchanged.

The real native workflow passed in **30.900 seconds**, creating exactly three
comparisons and no source analyses or acquisition/observation jobs:

| Control | Comparison ID | Selection |
| --- | --- | --- |
| Same-source zero | `aa894c30-4ec1-4eb3-aff3-b9556d7f86fd` | Full 161-center record |
| Elastic inactive tau | `6ae03235-fe94-445c-8e33-a5d67cdfb640` | Full 161-center record |
| Single modulus | `db9ab91e-3a58-4c1a-811e-a811bccf204a` | 61 centers, 0.15–0.30 µs |

Every source contains 129 frequencies and 161 time centers. Native verification
checks preflight differences, rejected incompatible sources/gates, exact linked
frequency/time/material readouts, shared A/B scales, symmetric residual scales,
five full-record error bounds, immutable gates, complete exports, stale estimates,
cold history with browser-intercepted unavailable sources/calculation, and 390 px
layout. The receipt is
`web/test-artifacts/sls-comparisons-1788650118808/verification.json`.
The two prepared analyses are distinct from these three comparison publications.

The final frontend build passed in **1.20 seconds**. After a history-title wrap
correction and accepted parameter-change summary, a **6.081-second read-only**
pass verified the same final comparison, seven unchanged source/comparison files,
five unchanged catalog/report routes, complete exports and zero page errors.
There were zero POSTs after its initial preview. Evidence is
`web/test-artifacts/sls-comparisons-readonly-1788650413119/verification.json`.
The [actual screenshot](images/sls-comparison-workspace.png) shows a signed RF
residual of **0.024591494278974155 at 0.185 µs** for the modulus control.

Independent read-only delivery passed in **52.266 seconds**, creating no reports
or jobs. It checks all **1,449 reflected component subtractions**, all **3,483
spectral subtractions**, minimal outward composition of all five RF bounds,
complete snapshot/JSON/CSV roundtrips and nine exact API cursor views. It then
compares ideal-model residuals using the separate pressure/velocity transfer and
de Hoog inverse at actual indices 64, 96, 128 and 160 (0.16, 0.24, 0.32, 0.40 µs).
Degrees 96 and 128 agree below **3.89e-17**. For the single-modulus control,
selected complex-residual differences are below **3.07e-12**, and saved-magnitude
residual differences below **4.78e-12**, against error bounds of approximately
**9.84e-8**. These are numerical agreement/convergence diagnostics, not an
independently certified oracle remainder or experimental accuracy.

The final receipt is `artifacts/v018-sls-comparison-delivery-final.json`; it pins
verification-script and input-receipt hashes. Its first read-only predecessor
correctly executed the arithmetic checks but accidentally replaced the returned
bound dictionary with a loop-local scalar when formatting the receipt. That
verification-tool naming bug was fixed, the six oracle tests were rerun
successfully, and the complete read-only delivery was repeated into a new file.
The earlier receipt remains preserved; no production kernel or saved report
changed. Independent review also strengthened exact-zero sign checks and clarified
that the published totals bound error in a residual, not its amplitude.

The next preparation is an explicit material-assignment/column-coverage contract.
The current comparison feature performs no HBM propagation or material calibration.
See [SLS_COMPARISONS.md](SLS_COMPARISONS.md) for supported semantics and limits.

## Version 0.17 — standalone scalar SLS materials

The complete Windows suite passed **1,614 tests in 449.73 seconds**, with the
same two third-party deprecation warnings. The 154 new cases comprise 60
constitutive/scattering/time tests, 10 independent inverse checks and 84
analysis/store/API tests. Evidence is `artifacts/v017-final-pytest.log`. The
locked environment adds `mpmath==1.3.0` only to developer dependencies for the
independent time oracle; production continues to use the pinned Arb runtime.

The [new proof](SLS_MATERIAL_PROOF.md) derives the analytic material branch,
positive-real compliance coefficient, exterior pressure/velocity flux,
homogeneous uniqueness, analytic contractive reflection, causal gamma response,
exact carrier-centered periodization and discrete frequency-tail omission.
It explicitly separates a reflected numerical enclosure from transmitted
pressure and physical validation. Tests exercise exact represented SI
conversion, zero relaxation and inactive tau, zero thickness, conjugacy,
asymptotic attenuation, independently evaluated ODE transfer, layer splitting,
phase/sign, real-port energy, finite causal elastic echoes, pre-standoff response,
recording/precision/tolerance changes, conditioning and resource failures.

The independent developer oracle uses a square-root-free pressure/velocity
matrix exponential and mpmath's accelerated de Hoog inverse. It inverts the
real and quadrature transforms separately and imports no production material,
pulse, scattering, planner or inverse helpers. Two manufactured dispersive
fixtures at 10 MHz agree between degrees 48 and 72 to **2.64e-25** and
**1.48e-24** at the selected centers. Production differences are below
**9.65e-11**, inside their approximately **4.94e-9** published model bounds.
This is independent numerical agreement and degree convergence, not a separate
certified oracle remainder. The exact represented input and complete output
evidence is `artifacts/v017-independent-time-agreement.json`.

Service tests exercise immutable frequency-only and reflected-RF reports,
retained zero-thickness material curves, a large admitted 6,001-frequency
report, malformed passive inputs and provenance, resealed/zero/understated
certificates, exact saved centers, allocation/encoding/disk limits, publication
collisions/failures, shared API locking, restarts and historical JSON/CSV with
current kernels/schema imports disabled. The large-report test caught an
underestimated expanded-JSON reserve; the per-cell allowance was increased to
512 bytes before freezing the implementation. Earlier numerical/schema and
resume-fingerprint source files remain byte-identical to version 0.16.

The delivery server uses port **8769** and its own `artifacts/volumes-v017`
directory. **10,768 pre-existing dataset/report files, totaling 535,521,134 bytes**,
were copied and SHA-256 verified; the idle SQLite catalog used online backup.
The older instances and roots remain intact. The existing manager's startup
reconciliation refreshed only the `updated_at` timestamps of 82 acquisition
catalog entries and three observation entries; IDs, state, progress, requests
and every other table field remained unchanged. These timestamp changes are
recorded explicitly, then the complete post-startup catalog is required to
remain unchanged during delivery. Copy and delivery receipts are
`artifacts/v017-isolated-instance-copy.json` and `artifacts/v017-sls-delivery.json`.

Native controls created exactly three manually assumed standalone reports in
**27.795 seconds**, with no volume/observation jobs and no browser errors:

| Report | ID | Saved samples |
| --- | --- | --- |
| Two-layer frequency diagnostics | `ccb4ccd3-4c2c-4d7d-848c-e56047624291` | 129 frequencies |
| Dispersive slab, reflected gamma | `8379aa34-584e-4178-9a1b-a2fa2e38a057` | 129 frequencies; 161 times |
| Elastic slab control, reflected gamma | `03503f9f-2312-4cd0-8e77-f7c1d2d071fb` | 129 frequencies; 161 times |

The native run exercises layer add/remove/reordering, exact input units, inactive
elastic relaxation, derived material/frequency selection, invalid modulus/rate/
record requests without substitution, signed RF/quadrature and shared windows,
saved-time keyboard/cursor controls, exports, stale estimates, cold history and
390 px layouts. Evidence is
`web/test-artifacts/sls-acoustics-1788646300547/verification.json`.

The final frontend build passed in **1.15 seconds**, with the existing main-chunk
size advisory. A save-specific history-picker mismatch was corrected: the picker
now selects the report just saved, matching its displayed arrays and title.
Final reopening/export/390 px checks passed in **4.037 seconds** using the same
three reports. The save-specific UI regression used explicitly intercepted
browser responses from an existing frozen record; no additional report request
reached the server. The documentation screenshot followed a real GET reopening.
Final evidence is `web/test-artifacts/sls-readonly-1788646561415/verification.json`
and `web/test-artifacts/v017-production-build.log`; report/catalog hashes remain
unchanged, with zero page errors.

The read-only delivery check passed in **25.822 seconds**, reusing those same
three reports. JSON and CSV round-trip every field, source/proof fingerprints
match the delivered implementation, and all 10,768 older files and the complete
post-startup catalog remain unchanged. The 50 MHz delivered gamma fixtures
require more oracle terms than the shorter 10 MHz unit controls: degree 64
failed the convergence criterion. Degrees **96 and 128** agree to **2.98e-17**
(dispersive) and **1.67e-17** (elastic) at four actual centers each. Maximum
selected complex differences from the delivered response are respectively
**1.63e-11** and **7.56e-13**, inside the approximately **4.92e-8** published
bounds. The lower-degree disagreement is retained in
`artifacts/v017-delivery-oracle-degree-study.json`; the production calculation
was not changed to fit the oracle. Elastic attenuation remains exactly zero.

Release finalization also requires exact Windows/Linux CI. Its linked receipt and
verified commit are retained in `artifacts/ACTIVE_INSTANCE.json`; a pending
release does not replace the prior verified release marker.

## Version 0.16 — saved coherent observation comparisons

The complete Windows suite passed **1,460 tests in 425.72 seconds**, with the
same two third-party deprecation warnings. The 173 added cases comprise 48
numerical, 91 report-store and 34 source/core/API tests. Evidence is
`artifacts/v016-final-pytest.log`. Locked dependency synchronization and lock
verification passed. All earlier observation/acquisition fingerprint files
were checked byte-for-byte against the preceding release and remain unchanged.

Independent rational controls exercise six separate bound maps, unequal complex
and saved-magnitude certificates, subnormals, outward sums of published
components, overflow rejection, phase reversal and zero residuals. The unchanged
causal subtraction/diagnostic tests also pass. Analytic uniform-slab comparisons
check retained complex residuals against an independent slab oracle; full and
gated metrics retain exact saved time coordinates and conservative source bounds.

Source/API tests reject one-ULP coordinate differences, equal interiors with
different surrounding support, changed phase/weights/contracts, shifted time
references, unsupported units/material semantics, incompatible excitation,
invalid gates/indices and primary causal inputs. They permit and disclose
numerical tolerance/implementation differences, verify rows again during
inspection, reject fresh typed-byte corruption and preserve source data.
Creation works without original causal parent arrays; frozen initial views and
JSON/CSV reports reopen after the observation sources become unavailable.

The new report store tests bounded parsing before decode, source limits of
16/64 MiB, duplicate/nonfinite JSON, exact source snapshot hashes and summary
provenance, self-comparison deduplication, exclusive publication/collision/crash
behavior, path/link/junction guards, resource/disk rejection and stable bounded
catalogs. Historical reads trap imports of current numerical/source code and
FLINT. A real stored 14 × 14 × 121 observation also passes report creation,
changed-cursor and source-independent history/export checks.

The delivery instance uses port **8768** and the isolated
`artifacts/volumes-v016` root. Before launching it, **10,763 dataset/report files
totaling 527,702,518 bytes** were copied and SHA-256 checked against the prior
root. The idle catalog used SQLite's online backup; runtime locks were excluded.
The original port8767 instance and `volumes-v07` data remain untouched. This
avoids running two managers against a shared root. The operational copy receipt
is `artifacts/v016-isolated-instance-copy.json`; it is not a new acquisition.

The production browser build passed in **1.28 seconds**; the existing main-chunk
size advisory remains. Native verification created two uniform-slab self reports
with different gates. Recovery reused those exact reports and passed in
**28.619 seconds**, with no page errors, source/catalog changes, extra reports,
acquisitions or derived jobs. The evidence is
`web/test-artifacts/observation-comparisons-1788642606537/verification.json`, with
the original creation record linked inside. Two verifier-only checks needed
correction: a nullable link readiness predicate and a POST allowlist that omitted
the initial read-only HBM microstructure preview. Neither required a product fix.

Native checks cover shared A/B and symmetric residual scales, all three products,
all six bound maps, actual-time linked cursors, ordinary gates, immutable history,
JSON/CSV and source Zarr exports, the saved-observation compare action, old-kind
isolation, reload and 390 px layouts. Injected source-cursor and source-catalog
404 responses preserve the accepted view, frozen initial view and report history.
Those browser injections do not remove real data; separate API tests exercise
actual source absence in temporary test directories.

The concrete HBM delivery created exactly **three comparison reports** from the
two existing **62 × 30 × 1,601** coherent observations. It created no acquisitions
or observation jobs. Full-record and gated comparisons retain the same six
full-time bound maps. The selected gate is **0.32–0.40 µs**, inclusive of 65 saved
centers; the full recording is **0–2 µs**.

| Report | ID | Observed creation time |
| --- | --- | ---: |
| Controlled missing bump, selected gate | `ad1d5805-36a6-4f92-8eb8-edac49a0b632` | 10.672 s |
| Same-source zero control | `1c75b38e-5031-4483-92da-74220e516542` | 6.487 s |
| Controlled missing bump, full recording | `9e71e8d6-05f8-4d3a-8b42-4665e8b9dded` | 12.219 s |

An independent Fraction oracle checked all six maps and **667,617 component
subtractions** across the three reports. It uses full waveform and separate
complex/magnitude-bound identities to prove equivalence for all **1,860 columns**
per report: 51 distinct pairs for each defect report and 37 for the zero control.
The full-record maximum absolute real-pressure difference is
**0.27842071575148486**; all self-comparison residual components are exactly zero.
Maximum complex and saved-magnitude residual bounds are respectively
**9.994271696109786e-8** and **9.994271703726208e-8**. These bound numerical model
residuals, without certifying physical contrast or detectability.

Delivery also checks independent ordinary metrics, gate products and deterministic
maxima; exact linked-view samples; complete deduplicated snapshots; lossless
source ZIP and report JSON/CSV exports; and source-free historical reads with
current-kernel traps. The two job catalogs and **10,765 pre-existing dataset/report
files** remained unchanged, including both native reports, the 524 previously
delivered acquisition files, all causal/observation volumes and 19 layered reports.
The run passed in **151.173 seconds**, including all oracles, exports and
preservation checks. Full evidence is
`artifacts/v016-observation-comparison-delivery/verification-report.json`.

The final read-only HBM browser reopening passed at output **x8/y41/t265**, mapping
to original **x9/y42** at **0.33125 µs**. Its selected signed RF difference is
**−0.19698976408247132**, complex total bound **9.994271685544949e-8**, and
saved-magnitude total bound **9.994271694864668e-8**. The report, both source
manifests and both job catalogs remained unchanged; there were no POSTs after
the initial specimen preview and no page errors. Evidence is
`web/test-artifacts/observation-comparison-delivery-1788642892125/verification.json`.
The visually inspected [workspace screenshot](images/observation-comparison-workspace.png)
is **1600 × 1550**, 254,201 bytes, SHA-256
`7583eb83d2e64641d44a9348472b457f4eb5a95536cd37fc85fe3c8129794292`.
It includes all six bound rows and full recording traces; the private supplied
reference image is excluded. The 390 px map and bound-table views also passed.

## Version 0.15 — finite coherent spatial observations

The complete final Windows suite passed **1,287 tests in 351.66 seconds**, with
the same two third-party deprecation warnings. The 153 new cases comprise 84
numerical, 40 plan/store, eight workflow/API and 21 export-reservation tests.
Evidence is `artifacts/v015-final-pytest.log`. Locked dependency synchronization
and lock verification also passed.

The new numerical kernel has **84 focused passing tests**. They exercise exact
dyadic weights, signed phase cancellation, constant complex fields, decimal and
subnormal values, nearest-even ties, independent component conversion checks,
exact-square magnitude brackets, source-bound propagation and outward-rounded
composition of published bounds. The implementation has no source/forward-solver
I/O. These checks establish the specified finite discrete operator, not an
experimental beam or measured spatial resolution.

The frozen plan/store has **40 focused passing tests**. They cover exact saved
coordinate generation and one-ULP rejection, exact-rational midpoint crop walls,
resource admission before decode, finite bounded metadata, original source-row
verification, typed whole-row commits, cancellation before publication, partial
repair/resume, corruption rejection, source-free completed export and original
source-certificate validation. Bounded validation caches retain content hashes;
they do not retain signal volumes or waive typed-byte integrity checks.

Shared-worker/API checks use actual source-backed jobs, committed-row cancel and
resume, cancellation inside a temporal block and during final source verification,
alternating old/new SQL claims, paused legacy batches and combined disk
reservations. An injected SQL-publication failure leaves no runnable catalog job
and retains the owned unpublished manifest as failed. A real spawned API job
checks all three products, actual source indices, inclusive gates and exact typed
ZIP contents, then reopens after moving only its owned temporary source out of
the source catalog. Historical acquisition fingerprint files remain unchanged.

The **21 focused export-reservation tests** cover both pending queues, disk
admission before ZIP allocation, a deliberately blocked copy with concurrent
worker progress and cancellation, allocation/copy failure cleanup, and startup
reconciliation that preserves orphan files. Near-capacity tests distinguish
already-running worker continuation from new job/resume/export admission, so a
growing export cannot double-charge its reservation to an admitted worker or
make its reserved bytes available to new work.

The native `verify:observations` pass completed in **70.584 seconds** in
`web/test-artifacts/observations-1788637920607/verification.json`. It created one
14 × 14 × 1,001 derived slab dataset, cancelled after one committed row, resumed
the same ID and verified that row's registry remained identical. All three
signals, five bounds, inclusive RMS gates, rejected gates, actual source indices,
keyboard cursors, signed display clipping, ZIP download, reload and kind isolation
passed. A spatially constant signed complex field remained exactly equal to its
source components. Source-manifest and legacy-job catalog hashes were unchanged;
there were zero uncaught page errors and no new acquisition jobs.

Desktop/support and 390 px controls, support, maps, traces and bound layouts
passed automated overflow checks and visual inspection. A simulated unavailable
parent-metadata response exercised the UI fallback while the real derived view
remained usable; actual source unavailability is tested with owned temporary
data in the Python workflow. The existing causal read-only native regression also
passed in `web/test-artifacts/causal-readonly-1788638028410/verification.json`:
two older sources reopened and exported, their hashes stayed unchanged, all 82
legacy job records remained present, and no POST occurred after the initial
main-workbench preview.

The final production frontend build passed in **1.09 seconds** with the existing main
chunk-size warning (`web/test-artifacts/v015-production-build.log`).

The frozen `tools/verify_observation_delivery.py` harness completed in **500.815
seconds**, creating exactly two derived observations and **zero acquisitions**.
It retained requests, estimates, job timelines, manifests, helper fingerprints,
typed ZIPs, independent numerical checks and before/after file/catalog snapshots
in `artifacts/v015-observation-delivery/verification-report.json`.

| Source fixture | Derived dataset ID | Shape `[y,x,time]` | Observed job time |
| --- | --- | --- | --- |
| HBM6 intact | `969e51c3-6938-4786-b5fb-49cd5034de2f` | 62 × 30 × 1,601 | 191.182 s |
| HBM6 missing gap-8/r3/c1 bump | `185aa9df-3075-472d-a5b1-7a8a65bb734d` | 62 × 30 × 1,601 | 190.289 s |

These are real submission-to-completion observations on this local machine,
including source verification and typed publication. The first job overlapped
part of the isolated regression suite. They are not performance guarantees. Each
volume retains 2,977,860 complex samples and all original 0–2 µs recording centers.
Its spatial extent is `[49.4296875,49.5703125,39.87890625,40.12109375]` mm in
`[xmin,xmax,ymin,ymax]` order, computed from exact crop midpoints.

Independent Fraction oracles cover all 1,860 columns through **103 intact and
144 missing-bump ordered neighborhood representatives**. They check both complete
component traces, exact nearest-even conversion, every magnitude bracket and
all five minimal bound maps. Every equivalent column must match its
representative's full traces and maps byte for byte. Maximum complex total and
magnitude total bounds in both results are respectively
`4.997135833047948e-8` and `4.997135844150178e-8`, below the requested `1e-7`.

The controlled source change remains the single epoxy replacement at
`hbm-6-mb-08-r03-c01`; all other twin fields and nominal primitives match. Its
27 changed source columns influence 53 retained columns under the stencil. All
1,807 output columns outside that support are byte-identical in all three
signals. All 53 supported columns show an RF change; maximum absolute RF
difference is `0.27842071575148486`. These are ordinary controlled synthetic
sensitivity/locality diagnostics, not enclosed change classification or measured
defect detection.

Each derived export contains **512 files**. Actual intact/missing dataset bytes
are **72,284,603 / 72,302,221**, below reservations of **74,322,116 / 74,339,728**.
The uncompressed ZIPs occupy **72,354,611 / 72,372,229 bytes**. All file payloads
and decoded typed arrays match exactly. Both exported copies pass cold historical
verification and views with source access and current synthesis entry points
trapped. The independent oracle established source-dependent conversion maxima;
offline checks verify the saved hashes, bound propagation/composition and
magnitude brackets within the documented historical limit.

Final snapshots preserve **all 524 earlier delivered acquisition files**, all six
saved causal datasets, 19 layered reports, all preexisting comparison reports,
the native slab observation and the complete old job catalog. Inspection also
leaves the two new observations unchanged. A too-tight tolerance control creates
no job. The original reference image and generated scientific data remain outside
Git.

The final delivered-HBM native reopening passed in
`web/test-artifacts/observation-delivery-1788638733697/verification.json`, after a
cosmetic bound-map label correction from grayscale to color scale. Output
`[y41,x8,t265]` correctly maps to source `[y42,x9,t265]`, at 0.33125 µs. It checks
the copied coordinates, 0.32–0.4 µs gate, native 72,372,229-byte ZIP download and
unchanged source/output hashes and both catalogs. There were zero POSTs after
the initial preview and zero page errors. The [final HBM workspace image](images/coherent-observation-workspace.png)
is an actual 1,568 × 2,090 browser capture with signed RF, full traces and all five
numerical bounds; it contains no private reference image.

## Version 0.14 — compatible saved causal-volume comparisons

The complete Windows suite passed **1,134 tests in 274.15 seconds**, with the
same two third-party deprecation warnings. The 150 new cases comprise 56
comparison-arithmetic tests, 72 immutable-store tests and 22 integration/API
tests. Locked environment synchronization and lock verification passed. The final
frontend production build passed in 1.18 seconds with the existing main-chunk
size warning (`web/test-artifacts/v014-production-build.log`). The
acquisition implementation files used by v0.13 saved-volume fingerprints remain
unchanged; comparison code lives in separate modules.

Numerical tests exercise signed real/quadrature and independently saved-magnitude
subtraction, exact outward conversion, normal/subnormal/tie cases, genuine strided
subtraction, wide-range diagnostic reductions, deterministic maximum locations
and differences of gated source statistics. An independent finite silicon-slab
echo oracle checks the paired response against the combined source and subtraction
bound. Same-source residuals are exactly zero while their conservative source
bounds remain present. These establish arithmetic and selected-model behavior,
not measured material or instrument accuracy.

Compatibility tests reject one-ULP differences in each actual coordinate vector,
changed excitation, unsupported semantics and invalid gates/cursors, while
retaining allowed tolerance/precision changes. Sources are checked before decode,
rows rechecked while processing and manifests checked again before publication.
Resource-limit rejection precedes expensive decode. Corrupt temporary source rows
are rejected without publishing a report.

Storage/API checks cover bounded finite JSON, decoded expansion, duplicate and
escaped keys, path isolation, exclusive immutable publication, injected storage
failures, exact JSON/CSV round trips and page-only catalog decoding. With the
forward kernel trapped, the API creates comparisons from saved rows and serves
linked cursor reads. An owned temporary source manifest is then physically
removed: frozen report read, export and initial view still work, while a new
cursor correctly fails. No acquisition jobs or source-byte changes occur during
comparison processing.

A complete native comparison pass succeeded in
`web/test-artifacts/causal-comparisons-1788633362601/verification.json`. It created
three reports from existing recordings: the HBM pair, a same-source zero residual
and a separate wider gate. It exercised all three signal products, shared A/B
and symmetric residual scales, linked actual-time cursors, both gate maps,
incompatible pulse and slab rejection, JSON/CSV and original source ZIP downloads,
reload, the saved-volume comparison entry, legacy-kind isolation and 390 px
layouts. It recorded zero page errors and zero acquisition-job POSTs, with the
complete job-catalog hash and both source-manifest hashes unchanged. The source
unavailability check in this browser pass injects a 404 response; actual removal
is covered by the owned temporary-source API test above.

The existing saved causal-volume read-only workflow also passed in
`web/test-artifacts/causal-readonly-1788633469189/verification.json`: two earlier
sources reopened with unchanged hashes, native ZIP exports and correct scales/time
views at desktop and 390 px. The job count remained 82 and no processing or
acquisition POST occurred after the initial main preview. No page errors occurred.

The final `tools/verify_causal_comparison_delivery.py` run passed in **76.86
seconds**, creating three immutable reports and **zero volume jobs**. Evidence is
`artifacts/v014-causal-comparison-delivery/verification-report.json` (36 files,
30,711,303 bytes). It retains exact requests, reports, JSON/CSV exports, changed
cursor responses, fixture identity checks, helper/oracle fingerprints and
before/after source/report hashes.

| Delivered comparison | Recording gate | Report ID |
| --- | --- | --- |
| HBM6 intact versus missing bump | 0.32–0.40 µs, 65 centers | `6c58620f-f861-4ee7-adc0-1e1a5494e6e8` |
| HBM6 intact versus itself | 0.32–0.40 µs, 65 centers | `e7f96fb1-3f85-4545-b685-ebb8fa6213fc` |
| HBM6 intact versus missing bump, full recording | 0–2 µs, 1,601 centers | `865f983d-2e2a-4c11-b084-9219ec222a5d` |

Each source has shape 64 × 32 × 1,601 `[y,x,time]`. Fixture checks establish two
distinct saved sources, the one intended epoxy-replacement overlay at
`hbm-6-mb-08-r03-c01`, and otherwise identical twin fields and nominal primitives.
All 27 sampled columns in its footprint change; all 2,021 outside columns remain
byte-identical in real, imaginary and saved-magnitude products. The RF maximum
absolute difference is 0.3151836225, signed negative, at `[42,9,265]` and 0.33125
µs. The complex residual maximum is 0.3273696743 at `[42,9,264]` and 0.33 µs.
Whole-record RF and complex RMS differences are 0.0054815497 and 0.0077517212.
These metrics describe this synthetic controlled change, not detection accuracy.

At the selected target column, the published source-bound sum is
9.994271648991719e-8, complex subtraction allowance 2.678552354989287e-16 and
combined complex bound 9.994271675777243e-8. Independent exact-Fraction checks
cover all 2,048 columns through 8/6/8 verified waveform-and-bound representatives
for the gated pair, same-source and full-record reports. The same-source
differences are exactly zero with their conservative bounds retained. Metrics,
signed maximum locators and gate maps were also checked directly against typed
source arrays. The slab-pair test exercises an independent analytical response;
this delivery verifies reductions of the saved data.

Historical JSON/CSV round trips and initial views passed in literal report-only
temporary roots with source access and current core/forward entry points trapped.
New cursor reads verified the frozen source identities. Incompatible slab rejection
left report and job catalogs unchanged. The delivery preserved **all 524 earlier
delivered dataset files, all six causal volumes, all 19 layered reports and all 31
preexisting comparison reports**, including the 11 causal reports from native QA.
All three delivered reports remained unchanged after reads, cursor changes and
exports; the complete acquisition-job catalog remained unchanged.

Final native reopening of the delivered gated report passed in
`web/test-artifacts/causal-comparison-delivery-1788633686465/verification.json`.
The actual recording cursor is `[42,9,265]`; the signed RF residual, 100 MHz
excitation caption and all five bound rows were inspected at desktop and 390 px.
The report, source manifests and complete job-catalog hashes remained unchanged,
with no POST after initial preview and no page errors. The 1,600 × 1,550 screenshot
at `docs/images/causal-comparison-workspace.png` was inspected directly. It contains
the running application and synthetic HBM results, not the private reference image.

## Version 0.13 — saved causal column-response volumes

The complete Windows suite passed **984 tests in 237.77 seconds**, with the same
two third-party deprecation warnings. The 113 new cases comprise 44 causal
raster-engine tests, 58 typed-storage tests and 11 workflow/API tests. Locked
environment synchronization, lock verification and the frontend production
build passed. The existing primary SAM and X-ray numerical models and saved
product contracts remain separate.

The new `sam_causal_rf_volume` resolves complete continuous material columns at
the actual global X/Y centers, reuses only exactly equal represented numerical
stacks, and retains the v0.12 gamma kernel's float64 real pressure, quadrature,
complex magnitude and per-column numerical certificate. This first observation
is explicitly independent, unfocused and lossless, with water exteriors. Tests
cover full HBM depth and all six sites, exact time coordinates, named versus
numerical material identity, thin features, strict inputs, canonical persisted
requests, bounded preflight and a complete 32 × 64 × 1,601 HBM raster.

Storage checks cover typed little-endian Zarr bytes, bounded JSON expansion,
predecode codec/chunk/path validation, coordinate and class maps, atomic row and
certificate commits, corrupt partial rows, immutable completed data and actual
output bytes within the advertised reservation. The writer rejects forged
analytic/cutoff bounds or solver/work diagnostics that differ from the frozen
kernel plan. A real worker test cancels after a committed row, restores verified
class responses and resumes with the solver blocked for those saved classes.
SQL failure injection covers both unpublished retained output and an uncertain
commit that already published exactly one runnable job. Historical read/export
works with current forward entry points blocked; the final certificate guard
also reopened all three earlier native causal QA datasets without changing bytes.

Native verification combines the HBM action sequence with the successful bounded
continuation in `web/test-artifacts/causal-volumes-1788628923462/verification.json`.
The early verifier was corrected for asynchronous view readiness and its slab
fixture's probe location. Native cancellation occurred before the first row;
committed-row cache restoration is established by the separate worker test above.
The continuation verifies float64 products, signed linked views and keyboard
cursors, actual-center gates and outside-record rejection, immutable reload and
ZIP download, kind/depth isolation, and desktop/390 px layouts. Its independent
slab oracle differed by at most 7.617e-10 from the saved complex pressure, within
the saved bound. These are split-run checks, not a claim of one uninterrupted
execution of the complete browser script.

The final-store read-only pass is recorded in
`web/test-artifacts/causal-readonly-1788629278912/verification.json`: unchanged
manifests, native exports, updated display-scale labels, nearest-saved-center
display rendering, unchanged job count and no processing/acquisition POSTs after
the initial main preview. The existing `verify:volumes` regression also passed
in `web/test-artifacts/volumes-1788629319675/verification.json`, including a new
legacy 24 × 32 × 401 acquisition, signed RF, post hoc RMS gate, export and reload.
All three successful browser records report zero page errors. The aggregate
record is `web/test-artifacts/causal-v013-native-summary.json`.

The final `tools/verify_causal_volume_delivery.py` run passed in **162.54 seconds**
at `http://127.0.0.1:8767`. Its immutable evidence is
`artifacts/v013-causal-volume-delivery/verification-report.json` (65 files,
187,697,238 bytes). It records the harness and imported oracle/helper fingerprints,
exact requests, estimates, typed arrays, class certificates, job histories,
standalone checks and byte-exact Zarr ZIP exports.

| Delivered dataset | Stored shape `[y,x,time]` | Exact classes | Maximum numerical bound |
| --- | --- | --- | --- |
| Silicon slab `53303ddf-a6c1-4b99-9bf5-d8122286fc97` | 16 × 16 × 1,001 | 1 | 4.987993007e-8 |
| Intact HBM6 `9e166ad9-6229-4c83-99a1-e831cc750fff` | 64 × 32 × 1,601 | 6 | 4.997135827e-8 |
| Missing bump `e4cdc105-9658-4c6d-9d19-b08a29a12bcd` | 64 × 32 × 1,601 | 8 | 4.997135828e-8 |

All three request absolute tolerance 1e-7 and 128-bit arithmetic. The slab uses
50 MHz, 400 MHz sampling and 0–2.5 µs; its independent finite causal echo sum
differs by at most 1.750e-10 in complex pressure. Both HBM volumes use the same
0.15 × 0.25 mm ROI, 100 MHz carrier, 800 MHz sampling and 0–2 µs recording,
gamma order 12 and fractional bandwidth 0.5. All 14 HBM class responses agree
exactly with independently extracted columns passed through the standalone
instrument. This establishes same-model integration consistency, not independent
physical validation.

The controlled change enables the epoxy replacement overlay for
`hbm-6-mb-08-r03-c01`. Its shape/center/size match that nominal bump; all six HBM
sites, the first five assemblies and every nominal primitive remain intact.
The largest absolute RF difference is 0.315184 (signed -0.315184) at `[42,9,265]`
(0.33125 µs), with whole-volume RF RMS difference 0.00548155. All 27 sampled
columns inside the bump footprint change, and all 2,021 outside columns remain
bit-identical in real, imaginary and magnitude arrays. These are synthetic
sensitivity diagnostics. Ordinary subtraction and bound addition do not provide
a newly certified comparison residual or a defect-detection accuracy claim.

Exactly three volume jobs and no application standalone reports were added by
the delivery. **All 524 files across 12 prior delivered acquisitions, all 19
preexisting layered reports and all three preexisting causal QA datasets remained
unchanged.** New source bytes also remained unchanged through all subsequent
checks. All actual dataset sizes fit their advertised output reservations. Each
ZIP passed CRC and exact payload checks; historical views passed with current
forward functions blocked. A separate no-overwrite check rejected an existing
output directory before network access.

The delivered intact HBM volume was reopened at `[42,9,265]` for the
[generated application screenshot](images/causal-sam-volume-workspace.png)
(1600 × 1850), with the real/imaginary/magnitude trace and full bound table.
Desktop and 390 px views were visually inspected. The read-only record
`web/test-artifacts/causal-delivery-reopen-1788629740920/verification.json`
confirms both delivered HBM manifest hashes and the complete job catalog stayed
unchanged, with zero POSTs after the initial main preview and zero page errors.

The [causal volume guide](CAUSAL_SAM_VOLUMES.md) documents the equations, controls,
stored products and admission limits. Numerical bounds concern the represented
scalar model, not material/geometry uncertainty, lateral sampling or measured
imaging accuracy. Repeated returns do not identify unique physical depths.
Compatible causal comparisons, finite lateral observation and calibrated
wave-physics extensions remain subsequent increments.

## Version 0.12 — causal multilayer reflected RF

The complete Windows suite passed **871 tests in 185.19 seconds**, retaining the
same two third-party deprecation warnings. The 82 new cases comprise 57 causal
kernel checks and 25 request/report/API checks. Locked environment synchronization,
lock verification and the frontend production build passed. Existing saved SAM
and X-ray solvers, product schemas and historical report bytes are unchanged.

The new excitation is an explicitly selected causal complex gamma pulse. Its
reflection includes coherent internal returns from the complete scalar stack.
Independent direct-interface, slab and two-layer propagation-event fixtures
check signed complex pressure and envelope against the reported combined
aliasing, frequency-cutoff and arithmetic bounds. Tests also cover exact actual
time centers, rejected contradictory recording metadata, nonuniform core inputs,
standoff, strong loss, high contrast, subdivision, zero-thickness collapse,
record-window consistency, precision rejection, and admission before expensive
allocation. The certificate includes conversion to the actual returned binary64
values; Arb radius alone is not used as their rounding-error bound.

The live `tools/verify_causal_layered_rf.py` delivery passed in **12.66 seconds**
at `http://127.0.0.1:8767`. Its evidence is saved in
`artifacts/v012-causal-delivery/verification-report.json`. Every report uses
1,001 identical time centers over 0–2.5 µs at 400 MHz, a 50 MHz carrier, 0.5
fractional bandwidth and gamma order 12. The independently selected spectrum
plot has 1,025 samples; it does not set the causal inversion grid.

| Saved experiment | Observed numerical evidence |
| --- | --- |
| Analytic 100 µm water/silicon/water slab | Direct sum of 106 causally contributing echo candidates; maximum complex and envelope difference 1.750e-10, within the 4.988e-8 accepted bound |
| HBM6 bump column at X=49.475, Y=39.95 mm | Complete 26-layer, 2.65 mm stack; 4,091 inversion terms, 128-bit arithmetic; bound 4.988e-8 for requested 1e-7 |
| Nearby column shifted +20 µm in X | Maximum signed RF difference 0.300563 from the bump column; explicitly a change of modeled column |
| Bump column with all solder pressure loss set to 3 dB/mm | Maximum signed RF difference 0.0231647; the assumed property changes are recorded |
| Same bump with requested tolerance 1e-9 and 192-bit arithmetic | 6,607 inversion terms; accepted bound 4.996e-10; complex difference from the ordinary calculation 2.460e-10, within the combined bounds of 5.038e-8 |

The slab oracle is a direct analytic causal-pulse echo sum, not a second call to
the production frequency recurrence. The delivery harness separately recomputes
the analytic bounds in Arb, verifies the exact requested time centers and source
assumptions, and reconstructs JSON/CSV exports exactly. These are numerical-model
checks and declared sensitivity experiments, not measured ground truth or defect
detection accuracy.

**All 524 files across 12 prior acquisition datasets and all 14 preexisting
layered report files remained byte-identical.** Exactly five standalone reports
were added and zero volume jobs were created. Historical reads passed with both
old and new numerical entry points blocked. A repeated invocation rejected the
existing output directory before contacting the API, preserving its evidence.
The completed evidence directory contains 46 files totaling 13,208,333 bytes.

Native Chrome `verify:causal-acoustics` and the legacy `verify:layered-acoustics`
regression passed, with reports in
`web/test-artifacts/causal-acoustics-1788623938017/verification.json` and
`web/test-artifacts/layered-acoustics-1788624125710/verification.json`.
The checks exercise mode switching with retained independent settings, actual
controls, signed RF and optional quadrature, amplitude-only display changes,
bound components, invalid requests, saved reports, exports and desktop/390 px
layouts. The independent interface check differed by at most 1.253e-10 from the
analytic response, within its 4.916e-8 bound. No page errors occurred.

The delivered HBM report `db62c814-16a8-4ab1-ad0d-ea8daa235308` was then reopened
read-only. Its hash and catalog counts stayed unchanged; no POSTs occurred. The
[generated application screenshot](images/causal-layered-rf-workspace.png)
(1600 × 1700) and mobile view were visually inspected. This release supplies a
standalone time-response report. Opt-in saved SAM ROI integration, an explicit
observation model for those volumes, and experimental calibration remain future
work. Later internal returns do not identify unique geometric depths. See
[CAUSAL_LAYERED_RF.md](CAUSAL_LAYERED_RF.md) for the equations and limits.

## Version 0.11 — layered-acoustics instrument

The full Windows suite passed **789 tests in 176.32 seconds**, with the same two
third-party deprecation warnings. The 90 new checks comprise 48 numerical-kernel,
17 column/analysis/schema and 25 immutable-report/API cases. Lock verification
and the frontend production build passed. Existing saved SAM and X-ray forward
models and product schemas are unchanged.

Independent fixtures check signed interface coefficients, analytic slab
reflection/transmission and echo series, impedance-weighted energy conservation,
passive constant loss, negative-frequency conjugacy, reciprocal transmission,
zero-thickness collapse, homogeneous subdivision, strong absorption, cancellation,
sampling refinement, global echo-tail tolerance, record-window independence and
explicit resource rejection. Review caught and fixed a floating-point pulse-edge
case where a sample inside the evaluated four-sigma support could be dropped by
the candidate search. Tests cover uniform and tightly spaced nonuniform centers.

Column tests retain complete z intervals, primitive precedence, explicit air,
thin films and HBM6 microfeatures. Edited properties are marked as changed
assumptions while preserving the original source twin. Persistence tests cover
atomic exclusive publication, collisions, disk/serialization/expanded limits,
tampered input/report hashes, unsafe paths and historical exports without current
schema construction or numerical execution. Actual large bounded reports remain
below their declared serialization and expanded-JSON estimates.

The live `tools/verify_layered_acoustics.py` delivery completed in **4.01 seconds**
at `http://127.0.0.1:8767`, preserving results under
`artifacts/v011-layered-delivery/verification-report.json`. It uses independent
closed-form complex coefficients, signed geometric echoes and direct compact
Gaussian sums, without importing the numerical kernel for its oracles:

| Saved experiment | Independent numerical evidence |
| --- | --- |
| Lossless 100 µm silicon slab in water | 77 impulses; maximum complex-coefficient error 1.185e-14; signed RF error 1.499e-15; global omitted-amplitude bound 9.188e-11 |
| Same slab, assumed constant 3 dB/mm pressure loss | 62 impulses; maximum complex-coefficient error 7.875e-15; signed RF error 1.152e-15; minimum absorbed-energy fraction 0.0102981; omitted-amplitude bound 9.907e-11 |
| HBM6 microbump column at X=49.475, Y=39.95 mm | 26 finite layers spanning all 2.65 mm of the specimen; maximum lossless energy residual 6.684e-14; all six sites, 574 primitives and 102 nominal microfeatures retained in the source twin |

The slab records use a requested aggregate echo tolerance of `1e-10`; independent
impulse spacing is 0.0237247924081 µs. Both RF results differ from their primary
baseline. The HBM column has no RF result because general multilayer time
synthesis is not enabled. Its sampled frequency plot does not certify narrow
resonances between samples or establish measured feature resolution.

All three JSON/CSV exports reconstructed their complete saved reports exactly;
JSON bytes matched the immutable stored files. Historical reads with numerical
functions blocked passed. **All 524 files across 12 previously delivered v0.9/
v0.10 datasets remained byte-identical. Zero volume jobs were created**, and the
entire job-catalog response was unchanged. A repeat verifier call against the
same output directory failed before API calls and preserved existing evidence.
The directory contains 30 files totaling 5,652,393 bytes including that check.

Native Chrome verification passed in
`web/test-artifacts/layered-acoustics-1788618839102/verification.json`, with no
page errors. It exercised real controls, four saved reports, HBM feature-column
entry, modified-source disclosure, a delayed old extraction losing to a newer
manual draft, independent slab echo signs/timing, rejected undersampling and
echo-budget/multilayer-RF requests, pressure transmission above one with correct
energy weighting, exact export/reload, and desktop/390 px layouts. Jobs stayed at
75 and batches at 14. Cursor and phase inspection created no reports. The separate
`delivery-reopen-verification.json` confirms the delivered HBM report reopened
without writes. The generated-only application screenshot is
`docs/images/layered-acoustics-workspace.png` (1600 × 1350); desktop and mobile
views were visually inspected.

A bounded copy of the existing microstructure browser verifier also passed at
`web/test-artifacts/microstructure-bounded-v011-1788619714817/verification.json`:
patch/defect editing, feature material sections, 3D focus, ROI preview, specimen
import/export, mobile layout, and reopening real saved SAM and X-ray datasets
with separate catalogs. It created no saved jobs. Its saved-acquisition/gate/
reload block was explicitly skipped; this is not a claim that the entire old
microstructure job verifier was rerun. Committed verification scripts are intact.

This is a bounded first increment of `LAYERED_ACOUSTICS_SPEC.md`. General
multilayer RF, saved reverberant SAM ROI acquisition, unique-depth interpretation,
focused elastic response and experimental calibration remain unimplemented.
The launcher also now recognizes healthy current-version servers rather than
requiring the historical 0.1.0 version string before opening the browser.

## Version 0.10 — X-ray recipes, batches and saved comparisons

The complete Windows suite passed **699 tests in 171.94 seconds**, with two
existing third-party deprecation warnings. Locked environment synchronization
and lock verification passed. The 108 added cases comprise 24 X-ray recipe
checks, 23 X-ray batch checks, 56 X-ray comparison checks, two real worker/API
checks and three SAM coordinate-chunk regression cases. The X-ray forward model
and saved product schema are unchanged.

Recipe tests cover complete frozen settings, immutable revisions, explicit kind
dispatch, mixed catalogs, strict integers/booleans, unsupported instrument fields,
rejected acoustic gates and single-defect isolation retaining all six HBM sites
and every unrelated primitive. Historical source recipes remain readable without
the current twin constructor or projector. Existing SAM requests still omit kind;
stored records, plans, payloads, hashes and staging journal meanings are retained.

Batch tests exercise real spawned workers, all-case admission, mixed-kind
rejection, ordered claims, SQL/staging failures, post-commit response replay,
disk reservation/rechecks, cancellation and exact seeded resume. A damaged
partial X-ray view is regenerated while healthy chunks and the completed first
case retain their bytes. Actual geometry/work/output caps remain enforced.

Independent comparison fixtures verify every global/per-view metric and support
denominator. Counts/transmission include zeros; logarithms exclude half-count
placeholders using common positive support. Negative logs and transmission above
one survive processing. Empty support and zero-reference L2 return nulls with
reasons. Tests reject nonfinite values even outside common support, broken saved
product relationships, corrupted hashes, incompatible units/semantics, shape and
all seven coordinate/pose arrays including one-ULP discrepancies. Reports retain
both manifests and processing fingerprints; JSON/CSV work without sources while
new views require original sources. The real photon-sweep API rejects native
unequal-I0 comparison and agrees with a direct float64 oracle after explicit
normalization, preserving all source bytes and creating no jobs on reopening.

Independent-seed Poisson checks use analytical mean and squared-RMSE sampling
tolerances over two realizations. Exact zero from the same stored realization is
a separate reproducibility check. Neither test is experimental accuracy evidence.

Independent review found a decoded-chunk admission gap in coordinate/pose arrays:
a small logical vector could declare a large compressed chunk. X-ray comparisons,
source-derived X-ray recipes and SAM comparisons now require canonical whole
coordinate chunks before decoding; guarded regressions verify rejection and byte
preservation. X-ray source and Zarr JSON have serialized and structural expansion
bounds before parsing; processing accounts for overlapping provenance, decoded
views and report displays. The 512 MiB estimate is not total process RSS.

Native Chrome `verify:xray-comparisons` passed full staged recipe transfer,
save/import/export/revision/from-source, invalid-case rejection, reviewed photon
batch and replay, unequal-I0 rejection then explicit normalization, independent
observed/expected policy, zero counts, negative logs and mask support, exact
linked differences, shared display scales, incompatible shape rejection, all
downloads, reload, 390-pixel layout and a real 160-view batch cancellation/resume
with stable dataset IDs. Batch-owned jobs route to **Manage batch** instead of
offering invalid individual controls. The record is
`web/test-artifacts/xray-comparisons-1788582830571/verification.json`.

Existing native `verify:comparisons` and `verify:xray` passed at
`web/test-artifacts/acquisition-comparisons-1788583032348/verification.json` and
`web/test-artifacts/xray-1788583094261/verification.json`. All three reported no
uncaught page errors. Screenshot inspection found a stale source-photon preview
after opening a different report; the frontend was fixed and rebuilt. Read-only
reopening and SAM **Manage batch** navigation then passed in
`xray-comparisons-1788582830571/reopen-verification.json`.

`tools/verify_xray_comparisons.py` delivered three two-case batches through the
live API in **50.52 seconds**, saving six projection volumes, six comparison
reports and six source ZIPs. The first attempt passed; no repair or reacquisition
was needed. Evidence is in
`artifacts/v010-xray-delivery/verification-report.json` (80 files, 13,161,753 bytes).
All **408 source files** retained their bytes, and all ZIP CRCs and payloads
matched their sources. JSON/CSV fields, exact coordinates, linked products/masks
and no-forward view traps passed. Independent full-array float64 QA and streamed
production metrics differed by at most **2.842170943040401e-14**, a reduction-order
residual, not an imaging accuracy measure. Reusing the evidence directory failed
before API access and preserved all 80 artifacts.

| Saved case pair | Shape per source (view,v,u) | Transmission RMSE | Logarithm RMSE |
| --- | --- | ---: | ---: |
| Noiseless coupon, 1,000 / 8,000 incident photons | 12 × 48 × 64 | 0 | 0 |
| Coupon detector FWHM, 0 / 0.35 mm | 12 × 48 × 64 | 0.0201189182 | 0.0262034802 |
| Six-site H100, 60 / 120 keV | 12 × 48 × 64 | 0.1778344303 | 0.7639463741 |

The photon example observed exact saved transmission/log equality after explicit
per-source normalization; native unequal-I0 comparison was rejected. That exact
equality is a result of this noiseless pair and is not generalized to noisy scans.
The coupon material pitch is 62.5 × 62.5 × 25 µm. The H100 material grid is
64 × 64 × 128 with pitch 937.5 × 937.5 × 20.703125 µm. Its six modeled HBM sites
and 102 authored local bumps/TSVs are retained in the twin, but the coarse grid
does not resolve the assumed 10 µm TSVs or 25 µm bumps. A nonzero energy residual
does not establish their detectability.

The H100 delivery batch is `bf86f53d-16e6-4acb-9dc5-07f1bd7544d3`; source IDs are
`c6cab074-e284-4c24-8244-a9573cd6931a` and
`2a7dc89a-6d1f-4b15-a990-c4639d565dbd`. Transmission report
`2b8b67d1-e231-4a9f-afb2-ebed2a4fed65` and logarithm report
`0986e1fb-4937-4d63-aaaa-8a8a99819c55` remain available in the local catalog.

The final frontend build passed. Read-only reopening of the delivered H100 report
passed in `xray-comparisons-1788582830571/delivery-reopen-verification.json`:
source energies/grids stay visible, the setup can be reopened, and all three
projection maps fit in a 1600 × 1100 desktop viewport without scrolling. Plots
precede detailed metrics/support; model notes remain accessible. A 390-pixel
layout has no horizontal overflow, and no acquisition requests or page errors
occurred. The inspected [1600 × 1700 screenshot](images/xray-acquisition-comparison.png)
shows view 0, detector row 32 and column 53 with shared projections, sinograms and
profiles. It contains generated H100 data, not the private reference cross-section.

## Version 0.9 — SAM recipes, transactional batches and saved comparisons

The complete Windows suite passed **591 tests in 127.28 seconds**, with the same
two third-party deprecation warnings. The 111 new cases comprise 28 recipe/case
tests, eight independent recipe-review regressions, 24 batch lifecycle cases,
48 comparison tests and three real worker/API integration tests. Locked Python
environment synchronization and the production frontend build pass. This release
does not change the forward propagation equations or saved dataset schema.

Recipe checks cover explicit defaults, immutable revisions, checksums and strict
import, actual recording-endpoint gates, rejecting text/boolean numeric sweeps,
unique bounded values, inactive continuous-Z rejection and all-case rejection.
An isolated missing-bump pair preserves all six HBM sites, every unrelated
primitive and another authored defect. Historical source recipes retain missing
method/kind fields and otherwise unsupported old geometry without calling the
current constructor. New imported requests meet current bounds; path checks
precede source array access. Existing record bytes are retained across failures.

Batch checks exercise real spawned-worker completion/reopening and injected
failures in staging, the second SQL case insert, committed-response delivery and
completion/catalog reconciliation. Invalid admission publishes no jobs;
same-key replay returns the same batch before re-estimation. Tests verify case
ordering, cancellation/resume, corrupt partial-chunk repair, retained good chunks,
byte-identical completed datasets, changed solver/estimate rejection, other-job
disk reservations, legacy catalog migration and owned staging path guards.
Unpublished artifacts remain preserved and marked in their ownership journals.

Comparison tests use independently authored stored arrays with sign reversal,
scaling, offsets, zero references, nonfinite values and asymmetric coordinates.
They check every global and gate metric, signed B−A maximum and physical locator,
saved-envelope independence, exact inclusive boundaries, empty-gate rejection,
one-ULP coordinate mismatches, units/time references, corrupt input and source
mutation during processing. Instrumented reads remain bounded; report reads and
JSON/CSV exports work without sources or the forward solver. New views verify
original source identities. A real two-case API sweep matched direct float64
calculations from its completed arrays and reopened without creating new jobs.

Independent review found and corrected two comparison admission issues. Queued
datasets are now rejected as incomplete before array lookup, returning 422 rather
than a misleading disk error. Serialized JSON length alone undercounted unusual
metadata containing many empty containers; source/report parsing now uses a
conservative structural expansion bound, with overlapping verification and
retained report/provenance included in the published workspace estimate. These
estimates bound the declared processing components, not total process RSS.

Native Chrome `verify:comparisons` passed complete recipe transfer, save/load/
revision/import/export, source-derived recipes, invalid-rate/inactive-Z rejection,
a reviewed two-case sweep, idempotent replay, actual cancellation/resume with the
same dataset IDs, exact signed trace differences, shared display scales, immutable
gate revisions, incompatible-axis rejection, all report/source downloads, reload
and a 390-pixel layout. The passing record is
`web/test-artifacts/acquisition-comparisons-1788580146530/verification.json`.
Existing `verify:volumes` passed separately at
`web/test-artifacts/volumes-1788580190683/verification.json`. Both reported no
uncaught browser page errors. Browser testing caught and fixed strict recipe
import losing JSON float spellings and duplicate IDs shared by difference-scale
inputs and plot canvases. Original file text is now retained through import.

The final read-only HBM report reopening, error-state recovery, source labels,
linked maximum-RF-difference cursor and desktop/mobile render also passed in
`reopen-verification.json` beside the native record. The inspected
[comparison screenshot](images/sam-acquisition-comparison.png) uses generated
HBM data only and displays the actual common A/B scales, zero-centered residual,
physical coordinates and complete signed RF plots. Image axes are independently
scaled for inspection, as the caption states; metrics use unchanged raw arrays.

`tools/verify_acquisition_comparisons.py` delivered three two-case batches through
the live API, then independently read their six small saved volumes for direct
float64 oracles. Production comparisons remained streamed. The successful local
evidence is `artifacts/v09-acquisition-delivery-complete/verification-report.json`.

| Experiment | Shape per source (Y,X,time) | RF relative L2 (B versus A) | Maximum absolute RF difference |
| --- | --- | ---: | ---: |
| HBM6, selected missing gap-8 row-3 column-2 bump disabled/enabled | 64 × 64 × 401 | 0.05081944 | 0.01332805 |
| HBM6 focus 0.45 versus 0.65 mm | 32 × 32 × 401 | 0.67897764 | 0.07511730 |
| Copper film, 128-plane voxel versus continuous paths | 16 × 16 × 201 | 0.65682295 | 0.17767061 |

The HBM defect pair uses ROI `[49.44,39.91,49.56,40.09]` mm, 100 MHz carrier,
800 MHz RF sampling, 0.5 fractional bandwidth, 0.55 mm focus, 0.2–0.7 µs record,
zero external standoff and a shared 0.26–0.7 µs gate. It retains all six HBM sites
and every unrelated primitive; the only added material primitive is the selected
epoxy-filled missing-bump overlay. The focus sweep uses the same carrier, rate
and record with a declared 0.25 × 0.25 mm region. The aligned method pair uses
a separate 4 × 3 mm copper-film coupon, 50 MHz/400 MHz RF and a 0.1–0.6 µs record.
These are numerical contrasts under assumed physics, not defect detection rates,
measured resolution or evidence that two sampling choices establish convergence.

Saved comparison IDs are `b9376e97-6a34-4aee-b0cb-bc4cd23ed8e4` (HBM defect),
`c71fb2ad-6621-4b30-9d0f-265adce993a2` (focus), and
`1c53bef6-33ce-473e-84dd-9712cf471673` (method). The HBM source IDs are
`f3d831bd-cdf1-4654-890d-71229a5413a3` and
`80a89d79-6cc9-4d3f-8664-3b57332b9c9b`.

Each pair has exactly matching X/Y/time arrays. Global RF/envelope, gated-map
and signed-maximum/location checks agree with direct calculations; the largest
scalar metric residual is **1.665 × 10⁻¹⁶**, under declared float64 reduction
tolerances. All six ZIP CRCs and every payload match **116 unchanged source
files**. Exported JSON and all CSV fields preserve frozen provenance; source
views trigger no forward model or acquisition jobs. Reusing an existing output
directory fails without changing any of the 57 delivered artifact files.

Two earlier verification-tool-only failures remain preserved: an RMS signed
value differed by 1.735 × 10⁻¹⁸ between summation orders, and Python's default CSV
field bound rejected a full provenance cell. The tool now uses its stated
reduction tolerance and an explicit bounded CSV field limit. The first completed
HBM batch was reused throughout; no completed acquisition was repeated to repair
these checks. Original source archives and failure evidence remain outside Git.

## Version 0.8 — continuous normal-incidence paths

The complete Windows suite passed **480 tests in 105.03 seconds**, with the same
two third-party deprecation warnings. The 82 new cases comprise 27 continuous
geometry/integral tests, 23 saved/preview numerical tests, 24 path-contract and
historical-data tests, and eight saved-lifecycle/API tests. Locked environment
synchronization and the final production frontend build pass.

Independent geometry tests cover box/cylinder intervals, sphere chords and
tangency, clipped full-depth paths, ordered overlap, equal-material coalescing,
nontransitive endpoint coincidence and ambiguous tiny-intersection rejection.
Scalar tests independently compute signed reflection, material travel times,
transmission/loss/focus, a non-grid-aligned 15 µm film and explicit air/water
behavior. Pulse tests cover the declared discrete fractional-delay law, record
edges, full preview time traces, halo-only features, translated ROI placement,
seeded X-ray noise, inactive-Z invariance and allocation rejection.

Read-only review found and fixed a one-ULP coordinate provenance defect. For an
ordinary decimal ROI, recomputing a saved X coordinate from the unpadded origin
gave 0.38515625 mm while its calculated path used 0.38515625000000003 mm. A box
ending at the former coordinate classified the two columns differently. Saved
axes now copy the exact padded-coordinate slices used by geometry; the boundary
counterexample is a regression. Preview/probe coordinates are independently
checked against those same sampled vectors. Instrumentation also confirmed that
actual cumulative candidate/event work equals the published preflight counts for
saved, full-preview and probe paths, including preparation and repeated halos.

Lifecycle tests acquire real continuous arrays, cancel during preparation and
after a committed tile, resume exactly, repair missing/corrupt chunks while
preserving good chunks, and reject changed methods, source identity or estimates
before signal writes. Two actual API acquisitions with inactive Z counts 128 and
1,024 have different frozen request hashes and identical RF/envelope/coordinates.
Views, post hoc gates, exports and restart/reopen preserve stored data. Completed
legacy records without a path field remain readable under a changed solver.

The standalone `tools/verify_continuous_hbm.py` experiment retains complete signed
RF/envelope arrays and frozen inputs for a 0.12 × 0.18 mm HBM6 ROI, 64 × 64 raster,
100 MHz carrier, 0.5 fractional bandwidth, 800 MHz sampling, 0.2–0.7 µs record,
0.55 mm focus and zero external water standoff. Results are local in
`artifacts/v08-hbm-independent-final/`:

| Numerical comparison | Executed result |
| --- | --- |
| Continuous Z setting 128 versus 1,024 | All RF/envelope/XY/time arrays exactly identical; request hashes differ |
| Voxel RF at 256 / 512 / 1,024 Z planes versus continuous RF | 189.40% / 73.56% / 30.76% relative L2 difference |
| Continuous missing gap-8, row-3, column-2 bump | 5.082% RF relative L2 difference; max RF 0.013328, envelope 0.011491 in relative units |
| 32² versus 64² raster at the fixed physical probe | 1.506% RF relative L2 difference, using declared bilinear interpolation |
| 800 versus 1,600 MHz RF sampling on the 32² raster | 0.220% RF relative L2 difference at shared saved times |
| Continuous X-ray optical depth versus independent ordered-ray oracle | Maximum absolute difference 8.89 × 10⁻¹⁶ over nine rays for each intact/defect case |

These are differences among numerical configurations under common assumed
materials and propagation. The continuous path equations remove vertical
voxel-boundary quantization for the supported primitives; neither two-grid
sensitivity nor exact Z-setting invariance establishes convergence of all
sampling choices, physical resolution or experimental defect detectability.
The X-ray oracle uses a separate midpoint-interval implementation and was
established against analytical slabs in the preceding release.

The continuous intact 64² recording took **8.343 seconds** in one fresh process,
with **234,946,560 bytes** observed peak working set, versus **2.116 seconds** and
**269,238,272 bytes** for the 1,024-plane voxel recording. Numerical admission
estimates 284,614,216 bytes for the continuous case, with eight-row tiles,
30,614,864 candidate tests, 18,860,768 event-work units and 18,649,664 RF cells.
Process peaks include interpreter and retained validation arrays; estimates have
a different numerical scope. These observations are not performance guarantees.

The compact 0.125 × 0.175 mm HBM preview is rejected under continuous combined
workspace limits. The explicitly chosen **0.15 × 0.25 mm** preview is admitted
at **530,295,424 estimated bytes**, with 15-row cores and the full finite A/B
record. No ROI, method, angle or sampling request is silently changed.

Native Chrome `verify:continuous-paths` passed method selection, rejected tilt,
inactive-Z equality, explicit larger HBM ROI, saved signed RF, post hoc gates,
download/reload, imported/exported choices, historical read-only export and
390-pixel layouts. It caught a compact-catalog method label being defaulted after
the request was stripped; catalog summaries now preserve the frozen method and
have separate regressions. Existing `verify:h100` and `verify:volumes` passed
sequentially. No uncaught browser page errors occurred in the passing flows.
The [workspace screenshot](images/continuous-hbm-workspace.png) contains generated
geometry/signals only; its broad-gate C-scan is weak-contrast on the visibly fixed
0–0.50 display window, without hidden normalization.

Two delivered continuous datasets are saved in the active local catalog:

| Case | Dataset ID | ZIP bytes |
| --- | --- | ---: |
| H100 HBM6 / continuous intact | `912a631d-6432-4faa-8323-a3118812ad51` | 6,260,717 |
| H100 HBM6 / continuous missing gap8 r3 c2 | `18c32ae0-059c-4fc8-b6e2-6ab0d307567d` | 9,643,565 |

Each contains 64 × 64 × 401 RF/envelope samples and **13,144,200 uncompressed
array/coordinate bytes** under the protocol above. All committed chunks,
coordinate hashes and ZIP CRCs verify. Every array and coordinate is exactly
equal to its corresponding offline experiment file. Reopened API traces match
all 401 saved samples; views and exports leave all dataset files byte-identical.
Additional completed SAM, projection, CT and depth examples pass integrity
verification with current code. The paired preview uses the separately declared
larger ROI and has maximum transmission difference 0.009541 after the normal
detector response with noise disabled. Archives, preview snapshots and reports
remain outside Git in `artifacts/v08-hbm6-delivery/`; no measured image is included.

## Version 0.7 — explicit HBM bumps, TSVs and local defects

The complete Windows suite passed **398 tests in 87.76 seconds**, with the same
two third-party deprecation warnings. The 83 new cases comprise 39 geometry,
22 section/API, 18 saved-SAM microstructure and four preview tests. The locked
Python environment also synchronizes successfully.

The default HBM6 patch adds 48 solder-proxy bumps and 54 copper TSV cylinders to
the six-site H100 twin, for **574 primitives**. Geometry tests verify canonical
ordering, stable identities, local translations, strict imports, partial edits,
disabled-parameter retention, restoration of absent sites, primitive limits and
rejected overlaps or orphaned defects. Missing bumps replace solder with epoxy;
voids are contained air spheres. These dimensions and materials are assumptions.

Sections are checked against asymmetric global coordinates and independently
selected feature centers. The tests cover XZ/YZ planes, explicit bounds, fixed
planes, excluded defects and invalid selections. A 0.1 µm void deliberately falls
between display samples: the image is unchanged but warns that the included
defect is undersampled. Removing that defect from the view removes its warning.

Saved-SAM resource tests compare the conservative column-interface bound with
actual voxel transitions and generated echoes, including random primitive
overlaps, edge columns and response halos. They verify unchanged hard budgets,
target-specific sampling warnings, actual missing-bump RF changes, nominal
restoration when defects are excluded, and byte-identical resumed output.
The geometry compiler is now part of SAM/X-ray forward-source fingerprints.
Previously completed SAM, projection, CT and depth datasets all pass integrity
verification with current code in the new local catalog.

Fine-ROI previews now tile only visible output rows while retaining full spatial
halos and the original RF time record. An independent direct pulse calculation
matches the returned C-scan, A-scan and B-scan, including halo-only sources,
pulses centered outside both gate boundaries and a later out-of-gate echo.
Separate tests reject both hard work limits before complex RF allocation.
Independent code review found no coordinate, halo, gate or budget regression.
The ready 64 × 64 × 1,024, 100 MHz preset used 16-row cores, at most **6,884,136**
work cells per tile and **27,536,544** aggregate work cells. Its full trace has
1,051 samples ending at 1.3125 µs despite the 0.26–0.7 µs gate. One fresh-process
measurement took **1.676 seconds**, with **452,222,976 bytes** peak working set;
this observation is not a runtime or process-memory guarantee.

`tools/verify_microstructure_xray.py` compares voxel projections with an
independent continuous vertical-ray oracle through ordered primitive intervals.
The selected gap-4 missing bump changes the continuous 80 keV transmission from
0.3454153 to 0.3569677. The expected log-transmission ratio is **0.032897805**;
sampled ratios at 256/512/1,024 depth planes are **0.0454058 / 0.0340544 /
0.0283786**. The finest result still has approximately **13.7% relative error in
this contrast**, and refinement is nonmonotonic. The oracle separately confirms
0.0768032 optical depth from material outside the chosen HBM assembly. The
experiment retains the surrounding package and disables noise/detector blur;
it does not establish instrument observability. Arrays and report are local in
`artifacts/v07-xray-independent/`.

`tools/verify_microstructure_sam.py` records a separate 0.12 × 0.18 mm ROI at
64 × 64 lateral positions, 100 MHz carrier and 800 MHz time sampling over
0.2–0.7 µs. Its gap-8, row-3, column-2 missing bump changes the finest-grid RF
by **3.54% relative L2**, with maximum absolute RF difference **0.0813** and
envelope difference **0.0728** in relative units. Intact 256/512-plane RF differs
from the 1,024-plane comparison by approximately **183% / 82% relative L2**.
The finest grid is a comparison reference, not converged truth: voxel boundary
motion strongly affects coherent phase. Full signed RF/envelope arrays, exact
requests, resource measurements and reports are retained in
`artifacts/m7-microstructure-sam-validation/`. These numerical signal changes
do not establish physical resolution, calibrated accuracy or defect detectability.
The next increment is continuous material-path timing and its independent tests.

Native Chrome `verify:microstructure` passed 16 workflow checks: loading the
ready preset, atomic patch edits, all six sites, asymmetric XZ/YZ sections,
missing-bump/void occupancy, defect exclusion, authored disabled records, close
3D view and return, rejected-edit retention, actual fine-ROI preview, saved
signed SAM RF with frozen patch provenance, post hoc gating, ZIP download,
reload, twin import/export and 390-pixel layout. The native `verify:hbm` and
`verify:volumes` regressions also passed sequentially. These checks found and
fixed a site-selection reset when reopening the editor. No uncaught page errors
occurred in the passing workflows. Browser reports and downloads remain local in
`web/test-artifacts/`.

The final production frontend build passes. Additional focused native checks
verify the revised close-view labels at 390 pixels, three local-defect markers,
hidden nominal missing-bump mesh and restoration of all seven package labels.
The inspected [editor](images/hbm-microstructure-editor.png) and
[close-view](images/hbm-microstructure-focus.png) screenshots show generated
geometry; the supplied reference pane is closed and its image is excluded.

The delivered comparison pair is saved in the active local catalog at
`http://127.0.0.1:8767`, using the ready preset's **0.125 × 0.175 mm** ROI.
Both datasets contain **64 × 64 × 401** signed RF/envelope samples over
0.2–0.7 µs at 800 MHz, with 100 MHz carrier, 0.5 fractional bandwidth, 0.55 mm
focus, zero external standoff and 1,024 material depth planes. This ROI differs
from the standalone SAM experiment above and samples different lateral rays.

| Saved case | Dataset ID | ZIP bytes |
| --- | --- | ---: |
| H100 HBM6 / intact explicit patch | `3f5a1694-987a-489a-886a-c6803264f3a9` | 6,265,269 |
| H100 HBM6 / missing bump gap8 r3 c2 | `7fb0140d-a602-4fbb-be11-a6df5e83e088` | 9,663,688 |

Each stores **13,144,200 uncompressed array/coordinate bytes**. All committed
coordinates/chunks and archive CRCs pass verification; exporting leaves every
dataset file byte-identical. X/Y/time coordinates match across the pair. The
reopened API views reproduce all 401 saved RF/envelope/time samples exactly at
the largest-difference location, with every dataset file still unchanged. The
RF relative L2 difference is **6.417%**, maximum absolute RF difference is
**0.013512**, and envelope difference is **0.012174**. The corresponding actual
X-ray previews, with the normal detector response and noise disabled, differ by
at most **0.009847 transmission**. Full preview snapshots, signed differences,
ZIP archives and the numerical report are retained locally in
`artifacts/v07-hbm6-delivery/`. No measured reference image or generated volume
is included in Git. These values characterize this synthetic configuration only.

## Version 0.6 — SAM time-to-depth estimates

The complete Windows suite passed **315 tests in 86.83 seconds**, including
the 247 existing cases and 68 depth-mapping/storage/view cases. The same two
third-party deprecation warnings remain. The new tests comprise 24 analytical
mapping cases, 20 storage cases and 24 view/API cases. A separate combined
SAM/X-ray/CT/depth storage regression passed 80 cases.

Analytical tests verify homogeneous and cumulative layered travel times, surface
water delay from frozen metadata, explicit surface references, nonzero recording
starts, nonuniform source time samples, exact source X/Y retention, signed RF and
independent saved envelope interpolation. Out-of-record and out-of-model depths
have distinct semantics with masked placeholders. Invalid layer ordering,
nonuniform spatial grids, numeric overflow and flattened travel times are
rejected. Bounds and temporary/output/workspace requirements are checked before
allocation. No primitive material labels enter the velocity model.

The independent experiment `tools/verify_depth_mapping.py` constructs analytic
Gaussian RF pulses at known reflector times without using the production SAM
forward simulator. It uses **3.90625 µm output depth sampling**:

| Synthetic timing check | Observed result | Analytic expectation |
| --- | --- | --- |
| Matching homogeneous model, reflectors at 0.6/1.4 mm | Peak errors −0.391/+0.391 µm | Zero |
| Matching layered model, reflectors at 0.25/1.25 mm | Peak errors +1.953/−1.953 µm | Zero |
| Assumed speed 10% high | Peak shifts +58.59/+140.63 µm | +60/+140 µm |
| Surface time 20 ns late | Both peaks shift −39.06 µm | −40 µm |

The profile arrays and report are retained locally in the ignored
`artifacts/m6-depth-independent-validation/` directory. The differences above
reflect interpolation and finite output sampling in a synthetic test. They do
not establish acoustic resolution or experimental H100 depth accuracy.

Storage/API tests verify frozen source and processing metadata hashes, exact
X/Y identity, coordinate and signal checksums, binary masks, signed RF,
nonnegative envelope, safe paths, conservative disk requirements, cache cleanup,
cancel/resume equality and damaged partial-chunk repair. Source corruption is
rejected before mapping. Completed derived data verify without requiring the
raw source directory. A real saved-SAM acquisition is mapped, exported, reopened
after application restart and checked for byte-identical source files.
Independent asymmetric arrays verify all three spatial slice axes, profiles,
original-time cursor values, mask semantics and immutable browsing.
Final review added view-side coordinate checksum and support-interval checks:
finite monotone coordinate edits, changed lateral coordinates and zero-signal
mask flips are rejected. A supported zero amplitude remains valid. These bounded
checks use frozen derived metadata and require no raw-source directory.

Native Chrome `verify:depth` passed the saved-source mapping workflow with a
translated ROI, explicit surface offset, two velocity layers, signed RF,
nonnegative envelope, and both out-of-record and out-of-model masks. It checked
exact source X/Y retention, null travel times beyond the model, gray invalid
pixels, linked mouse/keyboard navigation, weak-signal display windowing without
new jobs, ZIP export, reload persistence, four separate dataset catalogs, and
desktop/390-pixel layouts. `verify:depth-jobs` cancelled after **2 of 128 committed
slices**, then resumed the same dataset to completion. Existing slice checksums,
coordinate checksums, model metadata, request and source manifest were unchanged.
Both workflows completed with zero uncaught browser page errors. Locked Python
environment synchronization and the production frontend build also pass.
The existing native H100, saved-SAM and saved-source CT workflows pass against
0.6 as well, including their acquisition, inspection, export and reload paths.
Saved-SAM and CT checks ran sequentially after mapping jobs finished, respecting
the shared worker's intentional preview/acquisition exclusion.

The delivered HBM6 example maps source
`b407c38d-244b-4158-8683-bc4e56c03b37` to dataset
`a3f7a787-2eed-417f-85d6-a6f18a08efa5`. The **128 × 64 × 64 Z/Y/X** volume
retains the 8 × 9 mm HBM6 footprint at X 45.5–53.5 and Y 35.5–44.5 mm, with depth
0–2.65 mm. X/Y/Z pitch is **125 / 140.625 / 20.703125 µm**. It explicitly assumes
a uniform **5,000 m/s** speed and uses the saved source water-delay reference;
this is not a calibrated H100 velocity or material-depth estimate. Its source
retains all six physical HBM assemblies.

All derived samples are supported by this assumed model and the recorded time
interval. That validity does not certify the model. RF ranges from approximately
−0.091698 to +0.104059; envelope peaks at 0.112287, in their original relative
units. The three signals and four coordinates occupy **6,294,528 uncompressed
bytes**; the archive is **448,677 bytes** and passes ZIP CRC checks. The observed
queue/mapping/export interval was **52.81 seconds** while other verification
work was active; this is not a runtime guarantee. Original raw files and X/Y
coordinates remained unchanged. The [depth workspace screenshot](images/sam-depth-workspace.png)
shows actual saved data under an explicitly chosen display window.

## Version 0.5 — saved-source CPU CT reconstruction

The complete Windows Python suite passed **247 tests in 95.69 seconds**, including
the 197 existing cases and 50 reconstruction/atomic-storage cases. The same two
third-party deprecation warnings remain. Locked environment synchronization and
the production frontend build pass. Tests cover physical attenuation scaling,
asymmetric ellipses with displaced rotation/detector centers, multiple Y planes,
equivalent 180°/360° weighting, detector-sampling refinement, filter/cutoff effects,
negative values, invalid logarithms, truncation masks and physical half-pixel
edges. Nonuniform, limited-angle and noncanonical poses are rejected. Resource
estimates are checked before allocating the filtered-projection cache.

The independent experiment in `tools/verify_reconstruction.py` uses closed-form
continuous ellipse projections rather than the production forward projector.
For 128 views over 180°, a 32 × 216 detector and a 64 × 16 × 64 reconstruction,
the selected plane's interior mean is **0.6125577 mm⁻¹** against an analytic
**0.6125000 mm⁻¹** (relative bias **0.00942%**). Whole-plane RMSE is
**0.0189471 mm⁻¹**. An independent bilinear ray sampler evaluates seven unseen
angles using 2,048 midpoint samples per ray: relative projection L2 error is
**3.7455%** and projection RMSE is **0.00744037**. The retained report and NPZ are
in the ignored local `artifacts/m5-fbp-validation/` directory. These are results
for this synthetic numerical experiment, not experimental or H100 accuracy.

Storage/API tests verify frozen source-manifest identity, source immutability,
negative attenuation, finite masked placeholders, coordinate/chunk checksums,
safe paths, temporary-cache cleanup, bounded disk preflight and byte-identical
resume. Completed reconstructions remain inspectable and exportable after their
source directory becomes unavailable. Independent asymmetric saved arrays verify
exact XY/XZ/YZ slices, profiles and physical cursor coordinates. A real API job
also survives application restart and exports all arrays with embedded provenance.

Live Windows testing exposed a transient manifest-read/atomic-replace sharing
conflict. Atomic writes now retry only the Windows sharing/access errors over a
bounded 0.63-second interval. Tests verify transient recovery, permanent failure,
preservation of the old manifest and temporary-file cleanup. The affected live
job resumed from 8 of 16 committed slices and completed; a fresh complete CT
browser workflow then passed without interruption.

Native Chrome `verify:reconstruction` verifies a frozen synthetic source while
the preview contains H100, non-cubic output and translated bounds, exact slice
axes/values, click and keyboard cursor movement, negative display limits without
new jobs, Zarr ZIP download, reload, SAM/X-ray/CT catalog separation and desktop/
390-pixel layouts. `verify:reconstruction-jobs` cancelled a 96-slice H100-derived
job after **three committed slices**, confirmed incomplete data were unavailable,
then used the native resume button to complete it. The original committed slice
checksums and source manifest were unchanged. Neither workflow had uncaught page
errors. The native H100, saved-X-ray and saved-SAM regressions also pass against
0.5, including the legacy RF/time/gating workflow and its desktop/mobile layout.

The delivered H100 reconstruction is dataset
`1a3b7510-4bf1-4b18-942d-87426a62900c`, derived from the previously delivered
60-view source `391fa82b-60cf-4a40-9794-cfddac1f1c3b`. It has shape
**64 × 64 × 96 in Z/Y/X**, Hann filtering at detector Nyquist, and the full
60 × 60 × 2.65 mm envelope. Output pitch X/Y/Z is **625 / 937.5 / 41.40625 µm**.
Its attenuation range is approximately **−0.22575 to 0.59075 mm⁻¹**. The working
copy interpolated 46 invalid source log samples; the original projection files
were byte-identical after reconstruction and export. All output voxels have full
geometric detector support; that does not establish reconstruction accuracy.

The source retains all six HBM assemblies. Its coarse detector and sparse angles
cannot resolve fine HBM microstructure, despite the much smaller output Z pitch.
Attenuation, coverage and coordinate arrays occupy **3,147,520 uncompressed bytes**;
the local archive is **1,640,714 bytes** and passes ZIP CRC checks. Reconstruction
and export took approximately **16.80 seconds** in this observed run. These are
example timings, not a performance guarantee. The
[CT workspace screenshot](images/ct-reconstruction-workspace.png) shows actual
saved attenuation. Generated volumes, validation arrays and the supplied reference
image remain outside Git. Cone CT, laminography, acoustic depth conversion and
experimental calibration are future work.

## Version 0.4 — saved full-angle X-ray projections

The complete Python suite passed **197 tests in 49.55 seconds**, including all
139 prior cases and 58 X-ray solver/storage/view and integration cases. The same
two third-party deprecation warnings remain. Independent analytical cases cover
axis-aligned, negative and oblique box paths, 90° incidence, material replacement,
asymmetric pose conventions, surrounding attenuation outside a detector crop,
PSF halo continuity, geometry convergence, Poisson statistics, byte-identical
seeded resume, negative noisy logarithms, zero-only log substitution and float32
underflow. Oversized geometry, halo or ray work is rejected before allocation.

Storage tests acquire real mixed SAM/X-ray queues through spawned workers and
verify cancellation/resume, damaged partial-view regeneration, atomic read-back
of all four products, pose/coordinate checksums, source identity and immutable
completed datasets. An actual legacy eight-column SQLite catalog migrates without
changing the SAM manifest or RF bytes. Export rejects redirected roots and nested
symlinks/junctions before reading them; the Windows suite includes an actual
directory-junction case. Missing/nonfinite products and nonbinary/nonfinite
log-validity masks produce explicit errors instead of plausible displayed data.

Independent asymmetric saved arrays verify exact projection, detector-row
sinogram and profile selection, physical extents and angular display-bin edges.
A real API acquisition verifies distinct 0°/90° views, persistent data after an
application restart, all seven coordinate/pose arrays in the ZIP, instrument-kind
guards and the old untagged SAM request. View/product changes leave dataset bytes
and the number of acquisition jobs unchanged.

The native Chrome `verify:xray` workflow passed using an H100 dataset with
**8 × 32 × 48** samples and actual angles 0°, 45°, ..., 315°. It checks the full
specimen remains in the X-ray request despite a selected acoustic ROI, independent
geometry/detector sampling, distinct 0°/90° data and orthogonal rays, stored
profile/sinogram agreement, all three displayed products, zero-count mask
semantics, native ZIP download, reopening after reload, separate SAM/X-ray
catalogs and desktop/390 px layouts. No uncaught page errors occurred.

The separate native `verify:xray-jobs` check cancels a 72-view acquisition before
its first committed view, confirms the incomplete catalog entry is disabled and
the view API returns 409, then resumes the same frozen request and dataset through
completion. Backend tests separately cover already-committed partial views. The
browser also verifies automatic field sizing after moving the rotation center and
independently reported material/detector pitches. No uncaught page errors occurred.
The native H100 regression passes with seven component labels, retained reference
metadata, exports, imported presets and probe behavior. The production build passes.
The native `verify:volumes` SAM regression also passes against the final 0.4
server: acquisition, frozen HBM ROI, signed RF, linked time slices, RMS gating,
native ZIP download, reload and desktop/mobile layout. The previously delivered
0.3 HBM6 dataset still returns all 801 A-scan samples after the server restart.

A separate delivered acquisition retains six HBM assemblies and uses a
**96 × 96 × 1,024** material grid, a **64-row × 96-column** detector, and
**60 angles from 0° through 354° in 6° steps**. It uses 80 keV, 50,000 incident
photons/pixel, seeded Poisson noise and a nominal 20 µm detector FWHM. Its
material pitch is 625 × 625 × 2.588 µm, while detector pitch is approximately
625.609 × 937.5 µm. The detector undersamples the nominal blur and cannot resolve
fine HBM microstructure; these values are sampling, not physical resolution.

All four signal arrays and seven coordinate/pose arrays passed export integrity
checks. They occupy **5,905,760 uncompressed bytes**; the local Zarr ZIP is
**3,386,007 bytes** and passes ZIP CRC verification. Acquisition plus the two
cardinal view reads and export took approximately **23.77 seconds** locally,
which is an observed example rather than a runtime guarantee. The dataset ID is
`391fa82b-60cf-4a40-9794-cfddac1f1c3b`; generated data remain outside Git.

This release provides synthetic monoenergetic parallel-beam projection stacks,
not reconstructed xyz attenuation, cone geometry or experimentally calibrated
H100 microscopy. The [projection workspace screenshot](images/xray-volume-workspace.png)
shows actual saved data. The supplied reference image remains outside Git.

## Version 0.3 — saved acoustic volumes

The complete Python suite passed **139 tests in 19.58 seconds**, including the 82
existing regression cases and 57 new solver, storage, view and process/API cases.
The same two third-party deprecation warnings remain. Analytical checks cover
front/back-interface timing, pressure polarity and loss, explicit external water
delay/attenuation, pulse bandwidth, arbitrary recording starts and contributing
tails, and correspondence with the legacy preview at its equivalent bandwidth.
Rectangular axes, global ROI coordinates, neighboring PSF context, tile seams and
byte-identical resumed RF/envelope data are tested.

Storage checks run actual spawned workers and verify owner locks, cancellation,
restart recovery, worker replacement, frozen inputs, missing/corrupted partial
chunks, read-back commits, completed-dataset immutability, disk budgets and path
boundaries. Completed export checks independently verify combined input identity,
coordinate shape/type/checksum, RF/envelope shape/type and every committed row.

Independent asymmetric arrays verify XY/X–time/Y–time axis order, exact signed
traces, temporal display pooling and gate equations. View/gate calls leave all
dataset bytes unchanged. A real API job is acquired, gated, exported, and reopened
after an application lifespan restart; traces remain identical and a subsequent
legacy preview succeeds. Requests beyond resource limits fail before allocation.

The native Chrome H100 regression check passed on version 0.3, retaining seven
component labels, references, exports, imported presets and probe behavior. This
record distinguishes these software checks from measured microscopy validation.

The native Chrome `verify:volumes` check passed with a real HBM6 ROI dataset of
**24 × 32 × 401** samples. It verifies the frozen ROI, retained RF polarity,
XY/X–time/Y–time orientation and physical cursor, post hoc RMS gating without a
new acquisition, a native Zarr ZIP download, catalog reopening after page reload,
and desktop/390 px mobile layouts without horizontal overflow. No uncaught page
errors occurred. The 17-check HBM editor/ROI regression also passed.

The separate native Chrome `verify:volume-jobs` check cancelled a 64-row HBM6
acquisition before its first committed row, verified the incomplete dataset's
disabled UI and HTTP 409 view response, then resumed the same dataset and frozen
request through completion. No uncaught page errors occurred. This browser check
complements the Python tests for already-committed partial chunks. The
[saved-volume workspace screenshot](images/sam-volume-workspace.png) shows the
completed time volume and its retained signed RF; the source image is absent.

A separate delivered HBM6 dataset records **64 × 64 × 801** samples over 0–2 µs
at 400 MHz, using 1,024 material depth samples, a 50 MHz carrier and 0.5 fractional
bandwidth. All committed coordinates and chunks passed export integrity checks.
Its uncompressed arrays/coordinates occupy **26,254,600 bytes**; the local Zarr ZIP
is **1,921,799 bytes**. This compression ratio is an observation for this synthetic
layered specimen, not a preflight assumption or general guarantee. The local
dataset ID is `b407c38d-244b-4158-8683-bc4e56c03b37`; generated volumes and browser
test artifacts stay outside Git.

## Version 0.2 — layered HBM and region scans

The complete numerical/API suite passed **82 tests in 13.33 seconds**. The same two third-party deprecation warnings remain. Twenty HBM cases cover six physical sites, contiguous layers, parameter edits, 12-high templates, functional-state material invariance, strict import consistency, material-precedence protection, fixed defects and generator safety. Twenty ROI/section cases cover analytical full-depth attenuation and echo times, independent depth sampling, global coordinates and endpoint probes, PSF context outside the ROI, XZ/YZ material sections and invalid input. Existing physics/API cases remain passing.

The final production build and native Chrome `verify:h100`, `verify:hbm` and `verify:exports` checks passed. The HBM report contains 17 checks, including seven visible component labels, 8/12-high edits, rejected geometry retention, original acquisition snapshots after edits, electrical-state material invariance, ROI acquisition and global mouse/keyboard coordinates, restoring full-field/automatic-depth settings, cancellation of pending editor responses, and a 390 px dialog layout. Full-map-to-ROI and ROI-to-different-ROI transitions verify that stale-map interactions cannot overwrite the pending probe coordinates. Reference-image checks verify local SHA-256 identity, natural raster size, imported scale/FOV metadata, mismatched identity handling and an unavailable-image fallback. No uncaught browser errors occurred in the tested flows.

The CLI command with `--hbm-roi hbm-6 --resolution 128 --no-noise` completed in **961 ms** with 1,024 depth samples and global ROI coordinates. The saved synthetic run is `21c5a16c-c80c-4154-9608-2f4f6d5349c1` in ignored `artifacts/m1-hbm6-cli/`. This is an observed local runtime, not a performance guarantee.

The [layer editor screenshot](images/hbm-layer-editor.png) shows an applied 12-high template and its material YZ section. The original user image stays outside Git; only its identity and provisional scale metadata are published. Material sections are geometric views; this version has no saved RF/projection volume, CT reconstruction or experimental H100 calibration.

## H100 extension and public repository

The later H100 extension passed **46 tests in 6.55 seconds** with the same two third-party deprecation warnings. The new cases cover H100 geometry/source metadata, preserved legacy examples, larger probe coordinates, rejection of malformed references/presets, synthetic contrast at 128/192 grids, and generator refusal to overwrite existing files. See [H100_REVIEW.md](H100_REVIEW.md) for the independent source/geometry review and [H100_MODEL.md](H100_MODEL.md) for the explicit assumed stackup.

The H100 headless delivery run used the specimen's recommended settings (80 keV, 50 MHz, 0.34–0.45 µs gate, 0.85 mm focus, probe 22.5/24 mm, noise off) and completed in 519 ms. Its JSON retains all three NVIDIA references, facts/assumptions, and actual acquisition settings. All 46 tests are distinct from any claim of experimental validation; whole-package lateral sampling remains coarse.

The final frontend production build, `npm run verify:h100`, and `npm run verify:exports` passed. The H100 native Chrome check covered URL/specimen selection, published references, six non-overlapping component labels, JSON downloads and retained provenance/warnings, probe coordinates beyond 30 mm, exact imported acquisition presets, plain-text rendering, and restored BGA defaults. No uncaught browser errors occurred in these flows. The [delivery screenshot](images/h100-workbench.png) shows the tested H100 workbench; generated verification reports and downloads remain in ignored `web/test-artifacts/`.

The project now has an independent Git repository and public origin at `https://github.com/patrickjcraig/virtual-microscopy-workbench`. The parent Sandia repository was not modified. GitHub Actions is configured for locked Python setup, the complete numerical/API suite, and a frontend production build on both Windows and Linux.

## Numerical and API checks

Command: `.\.venv\Scripts\python.exe -m pytest -q`

**37 passed in 2.38 seconds.** Two third-party deprecation warnings were emitted by the Starlette test client; there were no test failures.

The 21 physics cases cover NIST coefficients and millimetre conversion, Beer–Lambert slabs at normal incidence and ±45 degrees, energy response, off-plane projection movement, Poisson reproducibility/variance, material precedence and explicit air cavities, signed reflection, round-trip time/transmission/loss, air-gap shadowing, gate exclusion, coherent RF/envelope behavior, probe/full-scan consistency, positive B-scan time bins, geometry sampling warnings and excessive-compute rejection. The 16 API cases include parameterized geometry/acquisition rejection cases, bundled specimen validation, concurrency handling, finite array outputs, input hashes and repeated acquisitions.

A default 128 × 128 BGA run completed in approximately **0.55–0.64 seconds** in the observed runs. The recorded headless export in `artifacts/verified-bga/` contains 128 × 128 X-ray and SAM arrays, 918 RF samples and a 459 × 128 B-scan; all values were checked finite. Its JSON includes settings, twin, material values/provenance, timestamp, model version and input hash. The gate is 0.42–0.56 µs.

An intact/defective comparison with noise disabled changed X-ray transmission by a maximum absolute 0.2402 and gated SAM amplitude by 0.0386. Those are numerical differences within the synthetic model, not detection accuracy. A broad 0.1–0.65 µs gate initially masked the delamination in the C-scan because it included a stronger earlier echo; the example default was adjusted to isolate die attach.

## Frontend and browser checks

- Production frontend build passed.
- Full `npm audit --audit-level=moderate` reported zero vulnerabilities at verification time.
- Chromium loaded the workbench and completed an automatic acquisition; no console errors or uncaught browser exceptions were observed in the checked flows.
- Defect toggle marked data stale; re-acquisition changed the SAM peak from approximately 0.129 to 0.091.
- Exploded geometry view, specimen selection, model-specific gate defaults, valid JSON upload, keyboard probe movement, linked A/B inspection and invalid-gate messaging were exercised.
- Axe 4.12.1 returned **0 violations, 39 passes and 0 incomplete checks** on the tested desktop state after contrast and scroll-region fixes. This automated result is not a comprehensive accessibility certification.
- At a 390 px viewport, document width remained 390 px: no horizontal overflow.
- Independent Playwright verification completed native Chrome downloads of both JSON exports. The twin was 42,301 bytes and acquisition 4,695,877 bytes; both parsed and contained the expected geometry/data/provenance. Probe inspection preserved the original acquisition and stored its own arrays/settings separately. The agent-browser helper's canceled-download result did not reproduce in this native download test.

The repeatable export check is `web/scripts/verify-exports.mjs`, exposed as `npm run verify:exports`. Start the API first and set `MICROSCOPY_CHROME_PATH` to a local Chrome/Chromium executable if the Playwright default browser is unavailable. Generated test files live in ignored `web/test-artifacts/`.

## Delivery state and limits

The loopback service was restarted with the final backend and checked again at `http://127.0.0.1:8765`; the default acquisition completed. The Windows launch script passed PowerShell syntax parsing. The finished desktop view is saved in `artifacts/workbench-final.png`.

Imports currently use ordered primitive JSON geometry and the fixed material library. Native CAD/EDA import, measured transducer/source calibration, elastic full-wave SAM, polychromatic/scattered X-ray transport, cone CT/laminography reconstruction and coupling to electrical/thermal/mechanical fields are future work. The 0.5 baseline supports parallel-beam CT from saved synthetic projections. The docs and UI identify sampling limits and illustrative material/PSF choices.
