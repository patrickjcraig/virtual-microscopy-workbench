# Mathematical contract for the standalone scalar SLS instrument

This document derives the model and the reflected-response error bound used by
`sls_acoustics.py` and `sls_time.py`. It concerns exact represented input numbers
under an explicitly assumed one-dimensional scalar constitutive law. It does
not establish measured material accuracy, full elastic/shear physics, focused
SAM, calibrated loss parameters, or a transmitted time-domain certificate.

The material contract is `single-relaxation-longitudinal-1`; the reflected gamma
certificate is `scalar-sls-reflected-gamma-1`. The earlier constant-loss and
lossless instruments retain their original models and identities.

## 1. Constitutive law, signs and dimensions

Use rest initial conditions, compressive pressure and longitudinal strain, and
the Laplace transform `F(s)=integral_0^infinity f(t) exp(-s t) dt`, with inverse
factor `exp(s t)`. For every finite homogeneous layer, require real constants
`rho>0`, `Minf>=M0>0`, `tau>0`, and thickness `d>=0`. Then

```
M(s) = M0 + (Minf-M0) s tau/(1+s tau)
     = (M0+Minf tau s)/(1+tau s).
G(t) = M0 + (Minf-M0) exp(-t/tau), t>=0.
(1+tau d/dt) stress = (M0+Minf tau d/dt) strain.
```

Here `M` is an effective **scalar longitudinal modulus**, not automatically
Young's modulus or the three-dimensional bulk modulus. The exponential
relaxation has a positive spring/dashpot realization; requiring passivity is
stronger than merely requiring causality. Primary constitutive context is given
by [Holm and Holm, sections II–IV](https://arxiv.org/html/1706.04828v1).
The specific stack and inverse arguments below are derived here.

The public request uses density in kg/m³, moduli in GPa, relaxation time in µs,
thickness in mm, exterior impedance in MRayl, and exterior speed in m/s.
Scattering uses SI quantities. In the inverse, `s_us` is per microsecond, so

```
s_SI = 10^6 s_us;       M_Pa = 10^9 M_GPa;
tau_SI = tau_us/10^6;   d_m = d_mm/1000;   Z_SI = 10^6 Z_MRayl.
```

Each conversion operates on an Arb enclosure of the original represented input
and an exact integer/rational factor. No intermediate binary64 SI value enters
the certified calculation. For example, authored `tau_us=.003` denotes that
represented number divided exactly by `10^6`, rather than an independently
rounded `3e-9` constant.

## 2. Analytic material and propagation branches

Let `Delta=Minf-M0`, `a=1/tau`, and `b=M0/(Minf tau)`, so `a>=b>0`.
On the closed right half-plane,

```
M(s)=Minf-Delta/(1+s tau),
Re M(s) >= M0 > 0.
```

Thus `M` is analytic and nonzero there. Define the unique analytic impedance
branch

```
Z(s)=sqrt(rho M(s)), positive for real s>=0,
gamma(s)=rho s/Z(s),
P(s,d)=exp(-gamma(s)d).
```

The square-root argument stays in the open right half-plane, away from its cut
and zero. For `Im s>=0`, the factored form `M=Minf(s+b)/(s+a)` gives
`0<=arg M<=arg s<=pi/2`. Therefore
`arg gamma=arg s-arg M/2` lies between zero and `pi/2`. Conjugation supplies the
lower half-plane, so `Re gamma>=0`; it is positive for `Re s>0`. At `s=0`,
`gamma=0`. This establishes decaying outgoing propagation on the chosen branch.
Taking an independent principal square root of `s*s*rho/M` is not the algorithm.

When `M0=Minf`, the implementation replaces the constitutive expression by its
constant value before evaluation: `Z=sqrt(rho M0)` and `gamma=s sqrt(rho/M0)`.
Relaxation time is inactive. Zero-thickness layers are omitted from the
propagation/interface stack, retaining their authored material diagnostics only.

## 3. Flux, uniqueness and bounded reflected transfer

Within a layer, the pressure/particle-velocity equations are

```
p' = -rho s v,
v' = -s p/M(s).
```

The identity

```
s/M(s) = [s + (a-b)s/(s+b)]/Minf
```

has positive real part for `Re s>0`, and nonnegative real part on its boundary.
Consequently

```
d Re(p conjugate(v))/dx
 = -rho Re(s)|v|² - Re(s/M)|p|² <= 0.
```

Pressure and velocity are continuous at every interface. With positive real
incident and terminal impedances `Zi,Zt`, write the incident-face fields as
`p=A+B`, `v=(A-B)/Zi`. Their real flux is
`(|A|²-|B|²)/Zi`; the outgoing terminal flux is `|T|²/Zt`.
Integration through the finite stack gives

```
(|A|²-|B|²)/Zi = |T|²/Zt + integral(nonnegative bulk terms) dx.
```

Hence `|B|<=|A|`. This derivation does not assume that an input impedance is finite.
Setting `A=0` forces `B=T=0`; terminal pressure and velocity then both vanish,
and uniqueness of the finite first-order ODE gives zero fields throughout.
This includes elastic layers, zero frequency and the empty stack.

The finite-layer ODE coefficients and fundamental matrices are analytic in
`Re s>0`. The two exterior boundary equations form a finite linear system;
the just-proved homogeneous uniqueness makes its determinant nonzero. Its
solution therefore defines an analytic reflected transfer `H(s)=B/A` with
`|H(s)|<=1`. At each finite boundary frequency the same argument supplies a
regular boundary value. A nonnegative lossless exterior round-trip delay `D0`
multiplies this transfer by `exp(-s D0)`, retaining analyticity and contractivity.

The numerical interface recursion uses pressure amplitudes. Internal impedances
are complex; the old real-interface Möbius disk identity is **not** its proof.
Every actual interface and recursion denominator must separately exclude zero
under Arb arithmetic. An unresolved enclosure rejects at the requested precision.
No modulus clipping or empirical stabilizing factor is permitted.

The power result also distinguishes transmitted pressure from transmitted
energy: energy transmission is `|T/A|² Zi/Zt`. Pressure transmission may exceed
one. The reflected unit bound is not a transmitted-RF certificate.

## 4. Causal gamma excitation and response bound

For integer `m>=4`, rate `a_g>0` and angular carrier `omega0`, define

```
g(t)=C t^m exp(-a_g t) exp(i omega0(t-m/a_g)), t>=0; g(t)=0, t<0,
C=(a_g e/m)^m,             D=C m!,
G(s)=exp(-i omega0 m/a_g) D/(s+a_g-i omega0)^(m+1).
```

The transform follows from the [NIST gamma integral](https://dlmf.nist.gov/5.9.E1).
For requested fractional amplitude-spectrum FWHM `beta` and carrier `f0`,
`omega0=2pi f0` and
`a_g=pi f0 beta/sqrt(2^(2/(m+1))-1)` in inverse microseconds. The envelope has
unit peak at `m/a_g`; this excitation delay is distinct from physical standoff.

Let `F=HG`, including lossless standoff in `H`. It is analytic in the right
half-plane and integrable on each vertical line because `|H|<=1` and the gamma
factor decays as `|omega|^(-m-1)`. Its inverse is independent of the chosen
positive line: horizontal contour contributions vanish by that same decay.
For `t<0`, moving the line to `sigma -> infinity` makes its absolute inverse
bound proportional to `exp(sigma t)/(a_g+sigma)^m`, which tends to zero.
The same estimate tends to zero at `t=0`. Thus the inverse is causal, continuous
at onset, and the boundary Fourier inversion gives the uniform bound

```
|y(t)| <= C0,
C0 = (1/(2pi)) integral |G(i omega)| d omega
   = D Gamma(m/2)/(2 sqrt(pi) Gamma((m+1)/2) a_g^m).
```

This proof, rather than a sampled passivity curve, supplies the global hypothesis
for periodization. A few frequency tests cannot replace any of these steps.

## 5. Infinite periodization and finite frequency omission

For period `T>0`, damping `sigma>0`, `delta=2pi/T`, and carrier-centered nodes
`s_k=sigma+i(omega0+k delta)`, the exact infinite inverse sum periodizes the
causal response:

```
exp(sigma t)/T sum_k F(s_k) exp(i(omega0+k delta)t)
 = sum_n exp(-sigma nT) exp(-i omega0 nT) y(t+nT).
```

The damped response is integrable and continuous; its sampled transform sum is
absolutely convergent because of gamma decay. These conditions justify Fourier
periodization. For actual requested centers `0<=t<=b<T`, all negative-`n` terms
vanish by causality, while the future aliases obey

```
E_alias <= C0 exp(-sigma T)/(1-exp(-sigma T))
         = C0/(exp(sigma T)-1).
```

Keeping only `-K<=k<=K`, `K>=1`, has a separate **discrete sum** omission bound:

```
E_cut <= exp(sigma b) D/T * 2 sum_(k>K) (k delta)^(-m-1)
      <= exp(sigma b) D/[pi m (K delta)^m].
```

The last inequality uses the decreasing power-series integral tail and
`T delta=2pi`. It is not an unaccounted quadrature approximation to the Bromwich
integral: the exact infinite sampled sum already has the stated alias identity.
No Gaussian support truncation or guessed omitted-echo tail is introduced.

The planner encloses all gamma constants, takes an outward represented damping,
and chooses a bounded integer `K`. It verifies alias and cutoff each at most a
quarter of the explicit tolerance before coefficient allocation. Work/space and
first-period guards reject unsupported requests without changing their settings.

## 6. Numerical operations and publication

All material operations, SI conversions, square roots, propagation, interfaces,
gamma coefficients, standoff and inverse phases use complex Arb balls. Inverse
evaluation uses degree-at-most-31 Horner blocks with separately enclosed phases;
actual requested binary64 time coordinates are enclosed as those exact values.
The shared arithmetic lock protects FLINT precision changes. FLINT's complex
and ball arithmetic interfaces are documented at
[complex arithmetic](https://flintlib.org/doc/acb.html) and
[ball arithmetic](https://flintlib.org/doc/using.html); the pinned runtime and
implementation identity accompany reports.

For every returned complex double `q`, compute an upper bound `C_t` on the
distance between the finite inverse-sum ball and that **actual returned value**.
For separately returned magnitude double `h`, compute `M_t` enclosing its distance
from the magnitude of the finite sum. No assumed libm/float conversion accuracy
replaces this readback enclosure. Publish outward doubles for `E_alias`, `E_cut`,
`max_t C_t` and `max_t M_t`, then publish an outward sum of those **published
components**. It bounds both complex pressure error and separately saved
magnitude error; it is deliberately conservative for each individually.

The total must remain at or below the requested tolerance. Nonfinite results,
ambiguous material branches, zero-containing denominators, unmet arithmetic
targets and excessive resources all reject. Frequency curves, phase displays,
reflectance/transmittance/absorptance and later summary reductions are explicitly
ordinary rounded diagnostics. Negative floating energy residuals are retained.

This enclosure excludes uncertainty in the authored constitutive law, layer
geometry, spatial model, fitted parameters and physical instrument. It creates
no SAM raster, inferred HBM material, focused beam or unique time-to-depth map.
