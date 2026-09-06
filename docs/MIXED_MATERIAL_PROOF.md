# Reflected gamma response of finite real/SLS layers

This derives the numerical contract implemented by `virtual_microscopy/mixed_acoustics.py` and `mixed_time.py`, extending the [standalone SLS proof](SLS_MATERIAL_PROOF.md). The explicit finite-layer union retains its own material, spectrum and reflected-RF identities. No result here assigns properties to HBM, admits more than eight authored layers, validates spatial propagation or certifies measured accuracy. The finite-stack theorem does not guarantee numerical admission or conditioning for every parameter combination.

## 1. Exact inputs, units and finite-medium interpretation

Assume rest initial conditions, compressive pressure, positive real lossless exterior impedances Zi and Zt, and finitely many homogeneous layers of nonnegative thickness. Zero-thickness layers contribute identity propagation and are omitted from the physical interface chain while remaining in authored provenance. The incident plane is x=0 and terminal plane x=L. Use

\[
F(s)=\int_0^\infty f(t)e^{-st}\,dt,
\qquad f(t)=\frac1{2\pi i}\int_{\sigma-i\infty}^{\sigma+i\infty}F(s)e^{st}\,ds.
\]

A represented public number denotes that exact number. For a `lossless_real` layer, author Z>0 in MRayl and c>0 in m/s. Define, with exact conversion factors,

\[
Z_{SI}=10^6 Z,\quad \rho=Z_{SI}/c>0,\quad M=Z_{SI}c>0,
\quad s_{SI}=10^6s_{us},\quad d_m=d_{mm}/1000.
\]

Then \(\sqrt{\rho M}=Z_{SI}\), \(\sqrt{M/\rho}=c\), and the outgoing propagation factor is

\[
\gamma(s_{SI})=s_{SI}/c,\qquad P=e^{-s_{SI}d_m/c}
=e^{-s_{us}(1000d_{mm}/c)}.
\]

Both square roots above are positive real identities, not an instruction to reconstruct the authored medium through rounded density/modulus values. No tau, SLS surrogate or independent attenuation multiplier is introduced. In particular, the exact ratio derived from the binary64 number 1.48 MRayl and 1480 m/s differs slightly from an independently authored exact 1000 kg/m³. Derived displays must not replace the original Z/c identity.

For an `sls` layer use real \(\rho>0\), \(M_\infty\ge M_0>0\), \(\tau>0\) and

\[
M(s)=\frac{M_0+M_\infty\tau s}{1+\tau s}.
\]

Density is in kg/m³; enclose the original represented GPa and microsecond inputs before exact multiplication by \(10^9\) and division by \(10^6\). All following constitutive/flux equations use SI s and x; the gamma-pulse sections use microseconds consistently.

## 2. Material analyticity and outgoing branches

Real layers have constant positive M and Z and analytic \(\gamma=s/c\), with \(\operatorname{Re}\gamma\ge0\) on the closed right half-plane.

For SLS layers put \(\Delta=M_\infty-M_0\ge0\), \(a=1/\tau\), \(b=M_0/(M_\infty\tau)\), so \(a\ge b>0\). Since \(\operatorname{Re}(1+\tau s)\ge1\),

\[
\operatorname{Re}M(s)=M_\infty-\Delta\operatorname{Re}\frac1{1+\tau s}\ge M_0>0.
\]

Thus M is analytic, nonzero and off the negative-real square-root cut. Define \(Z=\sqrt{\rho M}\) by the analytic branch positive on real \(s\ge0\), then \(\gamma=\rho s/Z\). For \(\operatorname{Im}s\ge0\), the factorization \(M=M_\infty(s+b)/(s+a)\) gives
\(0\le\arg M\le\arg s\le\pi/2\). Hence
\(\arg\gamma=\arg s-\tfrac12\arg M\) lies in \([0,\pi/2]\); conjugation handles the lower half-plane. Therefore \(\operatorname{Re}\gamma\ge0\), strictly positive for \(\operatorname{Re}s>0\). At s=0, gamma=0. Independent principal evaluation of \(\sqrt{\rho s^2/M}\) is not equivalent branch logic.

When \(M_0=M_\infty\), use the exact constant constitutive expression before arithmetic; tau is inactive. This includes the elastic controls without a limiting argument in tau.

## 3. Mixed flux balance and interface cancellation

In either medium type the scalar state obeys

\[
p'=-\rho s v,\qquad v'=-\frac{s}{M(s)}p.
\]

For a real layer \(\operatorname{Re}(s/M)=\operatorname{Re}s/M\ge0\). For an SLS layer,

\[
\frac{s}{M(s)}=\frac{s+(a-b)s/(s+b)}{M_\infty},
\quad
\operatorname{Re}\frac{s}{s+b}=
\frac{\sigma(\sigma+b)+\omega^2}{(\sigma+b)^2+\omega^2}\ge0
\quad(s=\sigma+i\omega,\ \sigma\ge0).
\]

Differentiation therefore gives

\[
\frac{d}{dx}\operatorname{Re}(p\bar v)
=-\rho\operatorname{Re}s\,|v|^2
-\operatorname{Re}(s/M(s))|p|^2\le0.
\]

Integrate separately over each positive-length layer. Pressure and particle velocity are continuous at every ideal interface, so the boundary flux terms at adjacent layer faces cancel exactly, irrespective of the two material tags. There are no interface losses, sources, imperfect bonds or extra surface impedances in this contract.

Let A and B be incident/reflected pressure amplitudes at x=0 and C the outgoing terminal pressure amplitude. The exterior relations are

\[
p(0)=A+B,\ v(0)=(A-B)/Z_i,\qquad p(L)=C,\ v(L)=C/Z_t.
\]

Because Zi and Zt are real, the integrated identity is

\[
\frac{|A|^2-|B|^2}{Z_i}
=\frac{|C|^2}{Z_t}
+\sum_j\int_{I_j}\left[\rho_j\operatorname{Re}s\,|v|^2+
\operatorname{Re}(s/M_j(s))|p|^2\right]dx.
\]

This yields \(|B|\le|A|\). At imaginary-axis frequency the usual energy transmission is \(|C/A|^2 Z_i/Z_t\); transmitted pressure need not be at most one. No transmitted time-domain bound is inferred.

## 4. Outgoing uniqueness, analyticity and standoff

Set A=0 in the last identity. Its left side is nonpositive while every right-side term is nonnegative, so B=C=0. Consequently both terminal state components vanish. The finite first-order linear ODE has unique evolution from that state, giving zero fields throughout every layer. This argument also covers the empty stack, all-elastic stacks, zero frequency and every finite imaginary-axis frequency, even when all bulk terms vanish. It never assumes a finite input impedance.

The layer ODE matrices and fundamental matrix exponentials are analytic for \(\operatorname{Re}s>0\). The two exterior boundary equations form a finite linear system; outgoing uniqueness makes its homogeneous determinant nonzero. Thus its reflection solution \(H(s)=B/A\) is analytic there and satisfies \(|H(s)|\le1\). The same finite-frequency uniqueness and regular material coefficients give continuous regular boundary values on the imaginary axis. No assertion of denominator conditioning or a uniform finite-precision margin follows.

A nonreflecting lossless incident-medium standoff of length \(d_0\ge0\) adds only

\[
\widetilde H(s_{us})=e^{-s_{us}D_0}H(s_{us}),
\qquad D_0=2000d_{0,mm}/c_i\ \text{microseconds}.
\]

It preserves analyticity and the reflected unit bound. Finite front and terminal layers remain part of H; they are not stripped into standoff. A front spacer matched in impedance gives two-way reflection and one-way transmission propagation factors. A terminal-matched spacer changes transmission phase but not reflection. Equating a finite front layer with an exterior delay requires the explicitly corresponding medium/delay and reference-plane convention; the algorithm must never add it twice.

## 5. Causal gamma response and uniform amplitude bound

Use the causal gamma excitation with inverse-microsecond rate \(a_g>0\), angular carrier \(\omega_0\), integer \(m\ge4\), and

\[
g(t)=C_g t^m e^{-a_gt}e^{i\omega_0(t-m/a_g)}\mathbf1_{t\ge0},
\quad C_g=(a_ge/m)^m,\quad D=C_gm!,
\]
\[
G(s)=e^{-i\omega_0m/a_g}\frac D{(s+a_g-i\omega_0)^{m+1}}.
\]

The transform is the gamma integral. For fractional amplitude-spectrum FWHM beta and f0 in MHz, \(\omega_0=2\pi f_0\) and
\(a_g=\pi f_0\beta/\sqrt{2^{2/(m+1)}-1}\). The unit-envelope peak at m/ag is excitation latency, not travel depth.

Let \(F=\widetilde H G\). It is analytic in the right half-plane and obeys
\(|F(\sigma+i\omega)|\le D[((a_g+\sigma)^2+(\omega-\omega_0)^2)]^{-(m+1)/2}\).
The inverse integral is absolutely convergent. Moving between two finite positive vertical contours is valid because their horizontal contributions decay as \(|\omega|^{-m-1}\). Moving the right contour to \(\sigma\to\infty\) bounds the inverse by a constant times \(e^{\sigma t}(a_g+\sigma)^{-m}\); this tends to zero for t<=0. Thus the resulting continuous y is causal, including y(0)=0.

Taking \(\sigma\downarrow0\) is justified by dominated convergence using the displayed integrable majorant and regular boundary values. Hence

\[
|y(t)|\le C_0:=\frac1{2\pi}\int_{\mathbb R}|G(i\omega)|d\omega
=\frac{D\,\Gamma(m/2)}{2\sqrt\pi\,\Gamma((m+1)/2)\,a_g^m}.
\]

The bounded analytic transfer proof supplies this global hypothesis. Sampled passivity or selected-time numerical agreement cannot replace it.

## 6. Exact periodization and separate frequency omission

Choose T>0, sigma>0, delta=2pi/T and \(s_k=\sigma+i(\omega_0+k\delta)\). The function \(h(t)=e^{-\sigma t-i\omega_0t}y(t)\) is integrable and causal. On one period, its translated sum converges uniformly by the C0 exponential bound. Its Fourier coefficients follow by absolutely integrable interchange; the coefficient series is absolutely convergent by gamma decay. Fourier reconstruction therefore gives

\[
\frac{e^{\sigma t}}T\sum_{k\in\mathbb Z}F(s_k)e^{i(\omega_0+k\delta)t}
=\sum_{n\in\mathbb Z}e^{-\sigma nT-i\omega_0nT}y(t+nT).
\]

For all actual saved centers \(0\le t\le b<T\), negative-n terms vanish. The n=0 term is y(t), and future aliases satisfy

\[
E_{alias}\le C_0\frac{e^{-\sigma T}}{1-e^{-\sigma T}}.
\]

For K>=1, retaining only -K<=k<=K introduces the distinct discrete-series omission

\[
E_{cut}\le\frac{2e^{\sigma b}D}{T}\sum_{k>K}(k\delta)^{-m-1}
\le\frac{e^{\sigma b}D}{\pi m(K\delta)^m}.
\]

The second inequality is the decreasing-power integral tail. There is no additional unstated quadrature error: the exact infinite sampled sum has already been related to y by periodization. Arbitrary circular wrapping, clipping support or guessed omitted-echo tails are not part of this derivation. Every requested center must satisfy the first-period condition; changing the recording end changes planning, not the physical transfer.

## 7. Enclosed numerical publication

Let u(t) denote the exact finite inverse sum and B_t an Arb enclosure containing it after every material, conversion, propagation, standoff, gamma and inverse operation. For actual returned complex binary64 q_t and separately returned magnitude h_t, enclose

\[
C_t\ge\sup_{z\in B_t}|z-q_t|,\qquad
M_t\ge\sup_{r\in |B_t|}|r-h_t|.
\]

Then triangle and reverse-triangle inequalities give
\(|q_t-y(t)|\le E_{alias}+E_{cut}+C_t\) and
\(|h_t-|y(t)||\le E_{alias}+E_{cut}+M_t\).
Publish outward finite doubles for alias, cutoff, max Ct and max Mt, then an outward total enclosing the exact sum of those published doubles. This conservative total bounds both errors over the saved centers. It must be<=the explicit requested tolerance.

The implementation encloses each original represented public input before SI conversion, selects the proved SLS root, excludes zero in every actual interface/recursion denominator, protects global precision with the shared lock, and verifies enclosures against the actual returned floats. Nonfinite/ambiguous balls, insufficient precision, excessive work/storage or a failed total reject without silent changes. The proof does not guarantee every allowed numerical combination will be admitted.

The saved certificate concerns the stated scalar model only. It excludes parameter/geometry uncertainty, calibrated material loss, lateral coupling, focus, transmitted RF, frequency curves, summary gates and unique depth interpretation. Old SLS certificate identities remain unchanged; mixed results require their own proof and implementation identity.

## Evidence and references

The existing [SLS proof](SLS_MATERIAL_PROOF.md) supplies the constitutive/flux and gamma derivation being extended. Its original primary references are retained: [Holm and Holm, constitutive/passivity context](https://arxiv.org/html/1706.04828v1), [NIST gamma integral](https://dlmf.nist.gov/5.9.E1), and FLINT's [complex-ball](https://flintlib.org/doc/acb.html) and [ball-arithmetic](https://flintlib.org/doc/using.html) documentation. The mixed-stack argument above is derived here; this derivation adds no claim of a newly verified external theorem.

Implementation acceptance is separate from the theorem. Focused controls compare material/scattering calculations with independent pressure/velocity matrix exponentials, real-slab causal echo sums, exact real/elastic equivalence, zero-thickness omission, exact subdivision, and finite-front versus exterior-delay identities. Independent high-precision numerical inversions are agreement diagnostics, not an enclosed inverse remainder. Only an accepted Arb calculation with its saved total supplies the stated model-specific numerical error enclosure.

## 8. Identities, numerical admission and limits

The material model is `lossless-real-or-single-relaxation-longitudinal-1`; frequency diagnostics use `scalar-mixed-scattering-arb-0.20.0`; the causal model uses `scalar-mixed-causal-gamma-arb-0.20.0` and certificate `scalar-mixed-reflected-gamma-1`. Original SLS schemas, numerical modules, proof and certificate identities remain unchanged.

The schema counts all authored layers, including zero-thickness entries, toward eight and requires the exact sum of represented thicknesses to be at most 6 mm. Actual spectra have at most 8,193 centers from 0 to 300 MHz; their 128-bit calculations are rounded diagnostics. The causal calculation permits at most 2,049 actual centers and 16,385 inverse frequencies; each actual time lies in [0,12] microseconds and inside the chosen first period. When explicit record metadata is present it must agree exactly with every supplied center. It never replaces those centers.

Preflight conservatively counts every authored layer as a full SLS computation even when it is real or has zero thickness. With n layers and F spectrum centers, work is F(24n+8) and planned peak bytes are 16 MiB+F(1024+768n)+8192n. With N inverse frequencies and Q actual times, material work is N(12n+8), inverse work NQ, and planned peak bytes are 32 MiB+4096N+4096Q+16384n. Limits are two million material-work units, 25 million inverse-work units and 128 MiB numerical workspace; wrapper/publication memory is separately admitted. These are conservative owned-allocation budgets, not process-RSS or runtime guarantees. All causal parameter, frequency, work and workspace checks precede coefficient allocation; the precision lock covers planning and synthesis. Ambiguous denominators, nonfinite balls or an insufficient final enclosure reject rather than clipping a denominator or loosening the requested error target.
