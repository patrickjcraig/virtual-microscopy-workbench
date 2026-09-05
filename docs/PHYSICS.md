# Physics and evidence status

This workbench produces synthetic X-ray projections and scanning acoustic microscopy (SAM) data from the same geometric specimen. It is a reduced-order forward simulator for exploring contrast, defects and acquisition settings. It has no experimental calibration, measured resolution claim, or coupled thermal, electrical or mechanical field solution. Sharing a digital twin gives both modalities consistent input geometry; it does not establish experimental registration accuracy.

The models and links below were reviewed on 4 September 2026. The original preview implementation is in `virtual_microscopy/physics.py` and material defaults in `virtual_microscopy/materials.py`. Version 0.3 adds `sam_volume.py`: the same primary-echo model with explicit recording start/duration/sample rate, Gaussian pulse bandwidth, external water-standoff delay/loss, and persistent signed RF/envelope tiles. See [Saved acoustic acquisitions](SAVED_VOLUMES.md) for those acquisition equations and limits. The gate-dependent record duration and excluded water standoff described below apply to the legacy preview.

Version 0.4 adds the independent full-angle parallel projector in `xray_volume.py`.
It integrates exact segment lengths through a sampled material grid, including
90° side incidence, and applies Gaussian detector blur before photon noise. Saved
detector sampling is independent of the whole-specimen material grid. Counts,
transmission, zero-regularized negative-log transmission and log-validity masks
have axes `[view,v,u]`; every detector pose is retained. Exact voxel path lengths
do not remove geometry sampling error. Detector pixel-area integration, spectrum,
and scatter are absent. See [Saved X-ray acquisitions](XRAY_VOLUMES.md)
for its complete geometry, noise/zero-count conventions, resource bounds and
sampling distinctions. The single-projection angle/extent discussion below
describes the original preview, which remains available.

Version 0.5 adds `reconstruction.py`: CPU filtered backprojection of saved
parallel-beam line integrals into `[z,y,x]` attenuation in mm⁻¹. Hann/Ram-Lak
filtering uses detector pitch and stored poses; 180°/360° angular weighting is
explicit. It preserves source measurements, negative supported values and a
geometric support mask. It does not fill output from known material labels.
See [Reconstruction](RECONSTRUCTION.md) for invalid-log handling, truncation,
filter equations, interpolation, numerical checks and limits. A fine output
voxel pitch does not establish physical image resolution.

Version 0.6 adds `depth_mapping.py`: saved SAM RF and analytic envelope are
resampled using a declared homogeneous or piecewise-layered velocity and a
surface arrival time on the original recording axis. X/Y remain unchanged;
output axes are `[z,y,x]`. Data outside the recorded time interval or velocity
model remain masked. Speeds are explicitly supplied assumptions or provenance,
not inferred material truth. This mapping does not solve an acoustic inverse
problem or establish calibrated depth accuracy. See [SAM depth](SAM_DEPTH.md).

## Digital-twin interpretation and units

Version 0.7's explicit HBM patch expands into existing vertical cylinders and
ordered material overlays. It changes sampled occupancy without adding new
material properties or a new propagation model. Missing bumps use epoxy
replacements; contained voids use air. Per-column interval bounds make SAM
resource admission practical for small ROIs while retaining all surrounding
Gaussian context. Independent continuous vertical-ray and RF grid-sensitivity
experiments are described in [HBM microstructure](HBM_MICROSTRUCTURE.md).
Fine voxel spacing is not sufficient evidence of convergence: discrete layer
boundaries can move between grids and substantially change coherent RF phase.

Version 0.8 adds opt-in `continuous_columns_v1` for saved SAM and zero-angle
previews. It replaces only the vertical voxel-center material paths with ordered
primitive intervals and continuous interface depths. Existing scalar reflection,
transmission, loss, focus, pulse and lateral Gaussian response remain. Sphere
chords are geometrically continuous, while normal-incidence reflection at a
curved surface remains an approximation. No acquisition Z grid is used in this
mode; finite lateral and RF sampling and material uncertainty still apply.
The interface policy, resource limits and measured numerical comparisons are in
[COLUMN_PATHS.md](COLUMN_PATHS.md) and [VERIFICATION.md](VERIFICATION.md).

The v1 JSON twin contains a bounded volume and ordered boxes, spheres and vertical cylinders. Object positions and full extents use **millimetres**. The x axis points right, y increases down the image and z increases from the specimen top into its thickness. Later objects replace earlier objects wherever they overlap. A void is therefore an air object placed after the solid it removes. Excluding defects skips objects with `role: "defect"`, exposing the underlying structures.

This is a material geometry model, not an electrical netlist or automatic CAD interpretation. Silicon, copper, solder, epoxy, FR4 and air labels select fixed property presets. Primitive geometry is sampled onto a finite material grid for the forward calculation. Features smaller than grid spacing can disappear, change apparent thickness or shift when the grid changes. The 3D viewer can show a thin primitive even when the acquisition grid cannot represent it reliably.

| Quantity | Model unit | Interpretation |
| --- | --- | --- |
| Geometry, ray length, focus | mm | Specimen coordinates and path lengths |
| Photon energy | keV | One monoenergetic acquisition setting |
| Mass attenuation coefficient, `mu/rho` | cm²/g | Material-dependent coefficient |
| Density | g/cm³ | Convert to kg/m³ by multiplying by 1000 |
| Longitudinal sound speed, `c` | m/s | Convert to mm/µs by dividing by 1000 |
| Acoustic impedance, `Z = rho c` | MRayl | 1 MRayl = 10⁶ kg/(m² s) |
| Acoustic frequency | MHz | Cycles per µs |
| Acoustic time and gate limits | µs | Round-trip time from the top reference plane |
| X-ray image | `I / I0` | Relative transmitted intensity |
| SAM signal | relative echo amplitude | A model amplitude without a calibrated volts/pressure scale |

Unoccupied space is water for SAM and a transparent air background for X-ray. Explicit air objects remain air cavities in the SAM model and have their small tabulated attenuation in X-ray. SAM uses **t = 0 at the specimen top plane**, excluding any physical water standoff; water gaps inside the bounded volume still contribute travel time and attenuation. A displayed gate is a time interval; it is not a universal depth interval in a stack with several sound speeds.

## X-ray forward model

The monochromatic primary transmission follows

\[
T(u,v;E)=I/I_0=\exp\left[-\int_{\mathrm{ray}(u,v)}\mu(E,\mathbf{x})\,ds\right].
\]

The linear coefficient used with millimetre paths is

\[
\mu_{\mathrm{mm}^{-1}}(E)
=\frac{\rho_{\mathrm{g/cm^3}}}{10}
 \left(\frac{\mu}{\rho}\right)_{\mathrm{cm^2/g}}(E).
\]

Thus a homogeneous slab of thickness `d_mm` at normal incidence has `T = exp(-mu_mm_inv * d_mm)`. The attenuation coefficient represents removal from the primary beam. Total attenuation is appropriate here; the mass **energy-absorption** coefficient is a different quantity and must not be substituted for it. These equations follow the narrow-beam monoenergetic model described in [NIST, mass attenuation coefficient definition](https://physics.nist.gov/PhysRefData/XrayMassCoef/chap2.html).

The selected angle rotates the projection direction about y through the specimen center. Ray integration includes increased distance through a slab and lateral displacement through the specimen. The detector uses the same displayed field of view, so some tilted projections can leave that field. At zero angle, image x/y correspond to specimen x/y. At nonzero angle, a detector pixel integrates a slanted ray; it is not the same physical column used by a SAM probe. The transmission image is convolved with an illustrative Gaussian detector response of **20 µm FWHM** before photon noise is added. This is an assumed blur parameter, not measured detector resolution.

With photon noise enabled, the intended counting model is

\[
N(u,v)\sim\operatorname{Poisson}(N_0T(u,v)),\qquad
T_\mathrm{noisy}=N/N_0,
\]

where `N0` is the incident photons per pixel setting. A fixed seed makes a run reproducible. An individual noisy sample can exceed 1 even though noise-free transmission is bounded by 1. Counts alone do not represent a calibrated exposure, detector dose or tube current. Display windowing is separate from the stored transmission arrays.

This preview model omits the source spectrum, beam hardening, finite focal spot, calibrated detector energy response/MTF, scatter reaching the detector, fluorescence, phase contrast and reconstruction. One projection is radiography. The separate saved-data workflow supports parallel-beam CT reconstruction; cone CT and computed laminography remain future work.

## SAM forward model

SAM is approximated as water-coupled, normally incident longitudinal pulse-echo inspection of vertical material columns. The pressure reflection coefficient at an interface between impedances `Z1` and `Z2` is

\[
r_{12}=\frac{Z_2-Z_1}{Z_2+Z_1},\qquad
t_{12}=\frac{2Z_2}{Z_2+Z_1}.
\]

`r` retains its sign: entry from a solid into an air gap produces a strong negative echo. For an echo returning through a previously crossed interface, the pressure transmission product is

\[
t_{12}t_{21}=\frac{4Z_1Z_2}{(Z_1+Z_2)^2}=1-r_{12}^2.
\]

Consequently the primary echo from a deeper interface includes the product of `1-r²` over all shallower interfaces. A nearly total reflection at an air gap strongly suppresses later primary echoes. This accounts for direct-path shadowing; it does not sum repeated reverberations. The pressure coefficients and their energy relationship are derived in [Demanet, MIT *Waves and Imaging*, section 1.2.5, printed pages 35–37](https://ocw.mit.edu/courses/18-325-topics-in-applied-mathematics-waves-and-imaging-fall-2015/c5db24b1a6d0d3301b1a3f21f6ceba3a_MIT18_325F15_CompleteLect.pdf).

The round-trip arrival time for an interface reached through layers `k` is

\[
t_j[\mu\mathrm{s}]=2000\sum_{k<j}\frac{d_k[\mathrm{mm}]}{c_k[\mathrm{m/s}]}.
\]

The ideal primary-echo amplitude can be expressed as

\[
A_j=r_j\prod_{k<j}(1-r_k^2)\,
 10^{-L_j/20}\,F(z_j;z_f),
\]

where `L_j` is round-trip **amplitude attenuation expressed in dB**, and `F` is a dimensionless approximate focus weighting. The implemented material loss law is `alpha(f) = alpha50 (f_MHz / 50)^b` in dB/mm, with `alpha50` and exponent `b` stored in the material library. It is evaluated at the selected central frequency, not applied spectrally across the pulse bandwidth. Each traversed layer contributes `2 alpha(f) d_mm` to `L_j`. The `/20` converts amplitude dB to a pressure-amplitude ratio; using `/10` here would instead apply a power conversion.

A column's time signal is assembled from shifted finite-bandwidth pulses,

\[
a(t)=\sum_j A_j\,p(t-t_j).
\]

The pulse and finite lateral blur are deliberate approximations to the transmit/receive response. They allow changing frequency, focus and time gates to change the synthetic contrast. They are not an identified transducer impulse response or measured point-spread function. In particular, a small Gaussian blur width must not be presented as a verified microscope resolution. Ultrasound contrast, envelope detection and round-trip attenuation are illustrated in the [official k-Wave B-mode example](https://www.k-wave.org/documentation/example_us_bmode_linear_transducer.php); that documentation is conceptual context, not evidence that this app runs k-Wave.

The **A-scan** presents signed amplitude and a nonnegative envelope at the selected x/y probe. The **B-scan** presents time versus x at the selected y. The **C-scan** returns the maximum envelope over the inclusive selected time gate at each x/y. If a gate is narrower than one time sample and contains no samples, the nearest sample is used with an explicit warning. No output is independently rescaled to its own maximum; changing the gate or specimen should not erase amplitude differences.

The numerical pulse is complex, `p(t) = exp[-0.5 (t/sigma_t)^2] exp(i 2 pi f t)`, with `sigma_t = 0.75 / f_MHz` µs. The time step is `1 / (8 f_MHz)` µs. Fractional echo arrival times use phase-aware deposition into two adjacent time samples. A lateral Gaussian filter is applied to the complex signal before the modulus is taken, so this approximation retains interference between neighboring columns. The signed RF trace is the real part and its envelope is the complex modulus.

The assumed F-number is 2, the water wavelength is `lambda_mm = 1.48 / f_MHz`, and the lateral Gaussian full width at half maximum is set to `1.02 F# lambda_mm`. Its Gaussian standard deviation is FWHM divided by `2 sqrt(2 ln 2)`. Focus weighting uses `F(z;zf) = 1 / [1 + ((z-zf)/zR)^2]`, with `zR = 2 lambda_mm F#²`. These are model-design choices inspired by diffraction scales; the numerical constants are not a calibration claim. The B-scan export retains at most 512 time bins by taking an envelope maximum in each bin. This preserves peaks for display, but gives it a coarser time sampling than the full A-scan.

The acquired image has `resolution` samples in x and y. Geometry defaults to twice that number in z; version 0.2 also permits independent `depth_samples` of 128/256/512/1024. Echoes with pre-pulse amplitudes below `1e-8` are omitted. The acoustic acquisition is limited to 12 µs, with a warning when later echoes are excluded. The requested probe snaps to its nearest sampled column inside the acquisition extent; the result retains both requested coordinates and sampled center. Numerical warnings identify intersecting thin geometry and an undersampled modeled focal spot. Very large resolution/frequency/time-window combinations are rejected by the compute budget rather than silently reducing requested sampling.

An optional XY ROI refines lateral sampling while retaining the entire specimen depth, including water gaps and all overlying/underlying material at that position. The sampled grid also contains a lateral halo covering the larger of the X-ray and acoustic Gaussian kernels, with scipy's four-sigma truncation radius. This keeps material just outside the acquisition rectangle in the blur calculation. The halo is clipped at specimen boundaries, where the existing nearest-edge extension remains. Only the requested region is returned; origin, padded grid shape, image extent and acquisition shape are recorded separately. ROI X-ray incidence is restricted to zero degrees until a general ray-geometry backend is implemented.

Editable HBM stacks compile to ordered silicon and epoxy layers plus coarse contacts. Functional electrical state leaves these materials unchanged; physical presence is a separate explicit setting. The material section viewer samples the same primitives on a 2D plane without attenuation, acoustic propagation or reconstruction. A finer section rendering therefore does not prove the acquisition grid resolves every displayed layer.

The model omits full elastic propagation, shear and surface waves, mode conversion, refraction, dispersion, actual lens aperture, curved-interface scattering, propagating diffracted wavefronts, roughness-induced speckle, repeated reflections and calibrated electronics. Solids are represented using an effective longitudinal scalar sound speed. These omissions can be material for real microelectronic stacks, especially anisotropic silicon, fiber-reinforced laminates, sloped interfaces, thin layers and strong air discontinuities.

## Material provenance

NIST provides elemental and compound photon attenuation data; mixture coefficients can be obtained from mass fractions `w_i` using `(mu/rho)_mix = sum_i w_i (mu/rho)_i`. Composition and mass fraction are distinct from volume fraction. NIST also notes independent-atom approximations and the need to treat absorption-edge discontinuities carefully. See [NIST XCOM introduction and limitations](https://physics.nist.gov/PhysRefData/Xcom/Text/intro.html) and the [copper coefficient table](https://physics.nist.gov/PhysRefData/XrayMassCoef/ElemTab/z29.html).

The material library must distinguish tabulated elemental values, assumed mixture recipes and illustrative bulk properties. “Solder,” “epoxy” and “FR4” do not uniquely specify chemistry, fillers, porosity, cure state or anisotropy. A NIST citation for an attenuation value does not validate the density, sound speed or acoustic attenuation chosen for the same material. Acoustic attenuation and focus settings remain illustrative defaults until independently characterized.

The v1 X-ray library uses six energies: 40, 50, 60, 80, 100 and 150 keV, interpolated in log energy/log attenuation. Elemental [silicon](https://physics.nist.gov/PhysRefData/XrayMassCoef/ElemTab/z14.html), copper and [tin](https://physics.nist.gov/PhysRefData/XrayMassCoef/ElemTab/z50.html) are tabulated directly. Solder is a **pure-tin proxy**. Epoxy uses [PMMA mass attenuation](https://physics.nist.gov/PhysRefData/XrayMassCoef/ComTab/pmma.html) with an assumed epoxy density. FR4 is a **60% silica / 40% PMMA mass-fraction proxy**, with silica calculated from the silicon and [oxygen tables](https://physics.nist.gov/PhysRefData/XrayMassCoef/ElemTab/z08.html). Explicit air uses the [dry-air table](https://physics.nist.gov/PhysRefData/XrayMassCoef/ComTab/air.html). These tabulated energies lie above the tin K edge; extending the energy range requires edge-aware data and interpolation rather than extrapolating the present table.

Nominal longitudinal velocities of 4660 m/s for copper, 3320 m/s for tin and 1480 m/s for water are consistent with the material table in [Olympus/Evident ultrasonic technical notes](https://adobeassets.evidentscientific.com/content/dam/downloads/276829456/UT_Technical_Notes_201907.pdf). The silicon speed is a nominal [100] value applied isotropically; polymer/composite velocities and all acoustic loss laws are uncalibrated model inputs. The manufacturer table provides reference values, not measurements of the example specimens.

A later measured-material record should retain material/lot identity, composition or grade, density, temperature, longitudinal and shear velocities, direction/orientation, attenuation versus frequency, measurement method, uncertainty, source version and access date. Preserve the actual values and assumptions in exports so a result can be reproduced after library updates. Do not silently replace an unknown material by a visually similar preset.

## What verification establishes

Analytical and numerical checks should separately establish:

1. **Units and limits:** homogeneous X-ray slab transmission agrees with the exponential solution; thicker absorbing paths reduce noise-free transmission; uniform acoustic impedance gives zero internal reflection.
2. **Echo behavior:** a layer's arrival time follows the two-way path calculation; reflection polarity follows the impedance step; upstream transmission losses reduce deeper primary echoes; the time gate changes the selected echo content.
3. **Numerical behavior:** seeds reproduce results; arrays remain finite; geometry overwrite order is deterministic; changing grid spacing reveals rather than conceals sensitivity to undersampled features.
4. **Application behavior:** import validation, controls, stored settings, export provenance, image orientation and linked probes agree with the documented coordinate convention.

These are verification targets, not a statement that every check has passed. The executed test results and their scope are reported separately. Passing software tests establishes consistency with these chosen equations and interfaces; it cannot prove that a real circuit, transducer or X-ray scanner behaves the same way.

Experimental validation would require independently measured specimens and held-out acquisitions, calibrated geometry, material properties and instrument responses, plus uncertainty reporting. A physical SAM calibration-block study provides an example of separating spatial resolution from sampling: [Tamulevičius et al., *Microscopy* 65, 429–437 (2016)](https://academic.oup.com/jmicro/article-abstract/65/5/429/2594969). Numerical grid pitch, physical spatial resolution, landmark localization precision and target-registration error are separate quantities. Shared synthetic coordinates alone establish none of the experimental quantities.

## Next modeling upgrades

The immediate foundation for further work is a stable twin schema, reproducible runs and analytic baselines. After the first interface is reviewed, useful upgrades are an instrument spectrum and detector response for X-ray; measured pulse/beam parameters and multilayer reverberation for SAM; and import of labeled CAD/voxel material geometry with explicit units. These are future options rather than capabilities of this release.

Full-wave acoustics should be introduced only with a suitable solid/fluid model and mesh/time convergence checks. The focused-source study by [Martin, Ling and Treeby (2016), author-hosted accepted manuscript](https://discovery.ucl.ac.uk/id/eprint/1508939/) compares a transducer representation with analytic models and physical measurements; it is a useful validation pattern, not validation of the Gaussian approximation here. Coupling electrical heating, thermomechanical deformation or damage into the twin would be a separate multiphysics extension requiring constitutive laws, boundary conditions and an explicit coupling strategy.
