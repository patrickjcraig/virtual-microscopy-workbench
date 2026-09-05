# SAM acquisition recipes, batches and saved comparisons — v0.9

The workbench can retain a complete SAM acquisition recipe, review two to four
explicit cases, acquire them through its existing local worker, and compare the
saved signed RF and analytic-envelope arrays. A reference is the selected
comparison baseline. Results remain synthetic relative signals under the
documented primary-echo model; numerical residuals do not measure experimental
accuracy, physical resolution or defect detection probability.

## Browser workflow

1. Open **Recipes & comparisons**, from the main workbench or saved SAM volumes.
   **Use current specimen** stages the current twin. Review the entire SAM form:
   path method, X/Y raster, active voxel depth, ROI, frequency, bandwidth, focus,
   water standoff, recording start/duration, sample rate and defect participation.
   An optional default processing gate does not shorten the recording.
2. **Save recipe** creates an immutable record. Load stages controls without
   submitting jobs. Editing and saving creates a new revision linked to its
   parent. Import/export retain strict JSON and checksums. The browser transmits
   the original imported JSON text, preserving floating-point number spellings
   used by its frozen hash. Do not manually rewrite an exported recipe's hashes.
3. Select one case variable and two to four unique explicit values. Supported
   fields are frequency, focus, fractional bandwidth, path method, active voxel
   depth samples, one authored local HBM defect, or participation of all enabled
   defects. **Review cases & resources** expands every request and displays
   differences relative to the first case, estimates and warnings. A bad case
   rejects the whole proposal. Edit the retained controls and review again.
4. Submit the reviewed batch. The worker records one dataset per case. The batch
   view shows completed cases and each case's row progress, errors and source
   dataset. Cancel preserves saved data. Resume verifies and queues compatible
   unfinished cases. Completed acquisitions are never repeated by resume.
5. Select completed sources A and B, choose a shared inclusive gate, and create a
   comparison report. Inspect linked X/Y/time cursors, A/B maps and traces with
   shared amplitude scales, and zero-centered B−A difference displays. The
   envelope comes from its independently saved array, not `abs(RF)`.
6. Changing the gate creates a new immutable report. Display windows and cursors
   leave its metrics unchanged. Export report JSON/CSV and, separately, each
   source's complete Zarr archive.

An isolated HBM experiment requires a named defect already authored in an
enabled, physically present stack patch and `include_defects=true`. It changes
only that defect's enabled state, rebuilding its existing contiguous assembly
block. Other local/global defects and the other HBM sites remain unchanged. If
the selector is empty, first author the local defect in the HBM editor. The
separate global participation pair explicitly includes/excludes all defects.

Frequency cases retain the specified sample rate: each case must satisfy the
existing eight-samples-per-carrier-period rule. Continuous paths reject a depth
sample sweep because that field is inactive. No control silently changes the
ROI, path method, rate, record length or numerical sampling to admit a case.

## Identity and lifecycle

Recipes are complete strict JSON records in the local SQLite catalog. Each has
a canonical UUID, schema version, creation time, parent revision, request hash,
record hash and optional frozen source provenance. New imported nonhistorical
requests meet current geometry/acquisition validation. A recipe created from a
completed dataset retains that dataset's original request and manifest. Reading
or exporting it does not invoke today's geometry constructor; fresh case review
reports current incompatibilities explicitly. Source-provenance hashes establish
internal consistency, not the authenticity of imported external evidence.

Batch plans preserve recipe identity, named overrides, exact expanded requests,
estimates and differences. Every case also freezes its dataset/job UUID, material
snapshot hash and solver identity. All case manifests are staged before one
SQLite transaction publishes every job and batch link. A failed preparation
publishes zero runnable jobs. Owned staging journals distinguish unpublished
artifacts as abandoned; recovery retains those artifacts and performs no
arbitrary deletion. An acknowledged or lost-response retry with the same key
returns the same batch; changed proposal/content with that key is rejected.

The existing single spawned worker processes cases in order. Failure,
interruption or cancellation stops later case claims. Later cases may remain
queued but held until **Resume batch** or **Cancel batch**. The main preview
guard treats that held queue as unfinished work, so resolve the batch before
requesting another preview. Individual job controls cannot bypass batch controls.
Restart reconciles completed manifests with the job catalog before resuming work.

## Exact comparison contract

Sources must be complete saved SAM RF datasets with valid paths, array registry,
manifest identity, canonical chunk checksums and coordinate hashes. Their shape,
float64 X/Y/time arrays, signal/coordinate units and declared time reference must
match exactly. One-ULP coordinate differences are rejected with axis/maximum
discrepancy details. No registration, resampling, time intersection, peak shift
or amplitude fitting is performed. Even a method or frequency sweep may yield
incompatible floating-point coordinates; a rejection preserves those coordinates.

For N saved samples, A is the selected reference and D=B−A. Float64 row reductions
compute RF bias `sum(D)/N`, MAE `sum(abs(D))/N`, RMSE `sqrt(sum(D²)/N)`, and relative
L2 `sqrt(sum(D²)/sum(A²))`. The maximum absolute difference retains its signed
value and the first Y/X/time locator. A zero reference norm gives `null` with an
explicit reason. Envelope statistics apply independently to the stored envelope.

The gate uses saved times and the existing inclusive 1e−9 µs boundary tolerance.
It records the actual selected endpoints and sample count, rejecting empty or
out-of-record gates. Peak-envelope and RMS-RF maps for A/B and their signed
differences have separate map metrics. Settings, geometry, material and solver
differences remain visible; changing several variables prevents attribution to
one variable. A display fit changes only the shared visible amplitude window.

Finished reports freeze source manifests, array registry, identities, exact
coordinates, gate maps, metrics, formulas and processing version. Reads and
JSON/CSV exports work without source directories; new trace/time views require
both original sources. All source access is read-only, including historical data.

## Resource admission

Each batch has two to four cases and at most 2 GiB of estimated final arrays.
Existing per-acquisition limits apply. Storage admission includes other queued
output, this batch's uncompressed output, the largest simultaneous temporary
cache and the existing 64 MiB free-space reserve. Peak numerical workspace is
the maximum case estimate, since one worker executes at a time. Disk is checked
again before preparation/resume; external applications can still consume space.

Comparison sources are bounded to the existing 16–256 X/Y and 2–16,384 time
dimensions, 512 MiB of RF plus envelope per source, and 64 MiB canonical source
chunks. Metrics read at most eight rows at once, reduced further for long records.
JSON is checked before parsing: source manifests allow 2 MiB serialized/64 MiB
conservative expanded size; reports allow 64 MiB serialized/192 MiB expanded size.
The expansion estimate is `8*bytes + 256*count({, [, comma, colon)`, deliberately
counting punctuation inside strings. Verification, overlapping provenance,
decompressed chunks, reductions and retained views enter a 512 MiB workspace
admission estimate. These are conservative processing estimates, not process RSS
guarantees. No limit violation silently discards a case or coarsens its data.

## Reproduce verification

Run the Python suite with `uv run pytest -q`. With the server running, set
`MICROSCOPY_URL` and `MICROSCOPY_CHROME_PATH`, then run `npm run verify:comparisons`
from `web`. Run live acquisition verifiers sequentially. The test creates and
preserves synthetic recipes, batches, recordings and reports in the local catalog.

The independent delivery tool, `tools/verify_acquisition_comparisons.py`, retains
one HBM6 isolated-defect pair, one focus sweep and one aligned voxel/continuous
method pair, with exact inputs, reports and archives in a new output directory.
Its small-fixture direct array calculations validate production streamed metrics;
they are verification code, not an alternative production processing path.
Executed results and limitations are in [VERIFICATION.md](VERIFICATION.md).
