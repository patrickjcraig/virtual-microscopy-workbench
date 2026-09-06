"""Independent mixed-medium ODE, exact real identities and strict schema checks."""
from copy import deepcopy
import math
from fractions import Fraction

from flint import acb, arb, ctx
import numpy as np
import pytest
from scipy.linalg import expm

from virtual_microscopy import mixed_acoustics as kernel
from virtual_microscopy.mixed_schemas import MixedLayeredAnalysisRequest, MixedStack, MixedCausalPulseSettings


def layer(**changes):
    return {"kind":"sls","name":"Assumed SLS layer","thickness_mm":.05,"density_kg_m3":1000.,
        "relaxed_modulus_gpa":2.25,"unrelaxed_modulus_gpa":4.,"relaxation_time_us":.003,**changes}


def real_layer(**changes):
    return {"kind":"lossless_real","name":"Explicit real slab","thickness_mm":.05,
            "impedance_mrayl":2.,"sound_speed_m_s":2000.,**changes}


def stack(layers=None,incident=1.48,terminal=1.48):
    return {"incident":{"name":"Incident","impedance_mrayl":incident,"sound_speed_m_s":1480.},
        "terminal":{"name":"Terminal","impedance_mrayl":terminal,"sound_speed_m_s":1480.},
        "layers":[layer()] if layers is None else layers}


def response(spectrum,key):
    return np.array(spectrum[key]["real"])+1j*np.array(spectrum[key]["imag"])


def ode_oracle(specimen, frequencies):
    """Factorized modulus and p/v matrix exp; no material roots or r recursion."""
    zi,zt=(specimen[side]["impedance_mrayl"]*1e6 for side in ("incident","terminal"))
    rows=[]
    for f in frequencies:
        s=2j*np.pi*f*1e6
        matrix=np.eye(2,dtype=complex)
        for l in specimen["layers"]:
            if l["kind"] == "lossless_real":
                rho=l["impedance_mrayl"]*1e6/l["sound_speed_m_s"]
                modulus=l["impedance_mrayl"]*1e6*l["sound_speed_m_s"]
            else:
                rho=l["density_kg_m3"]
                a=1/(l["relaxation_time_us"]*1e-6)
                b=l["relaxed_modulus_gpa"]/l["unrelaxed_modulus_gpa"]*a
                modulus=l["unrelaxed_modulus_gpa"]*1e9*(s+b)/(s+a)
            # Normalize velocity as Zi*v for well-scaled independent matrices.
            generator=np.array([[0,rho*s/zi],[zi*s/modulus,0]],complex)
            matrix=matrix@expm(generator*l["thickness_mm"]*1e-3)
        h=matrix@np.array([1,zi/zt])
        rows.append(((h[0]-h[1])/(h[0]+h[1]),2/(h[0]+h[1])))
    return np.array(rows).T


@pytest.mark.parametrize("layers,zi,zt", [([],1.48,8.),([layer()],1.48,1.48),
    ([real_layer()],1.48,1.48),
    ([real_layer(thickness_mm=.03125),layer(),real_layer(thickness_mm=.015625,impedance_mrayl=3.)],1.48,8.),
    ([layer(unrelaxed_modulus_gpa=2.25)],1.48,8.),
    ([layer(),layer(thickness_mm=.02,density_kg_m3=1200.,relaxed_modulus_gpa=3.,unrelaxed_modulus_gpa=4.8,relaxation_time_us=.008)],1.48,1.48)])
def test_arb_scattering_vs_sqrt_free_transfer_oracle(layers,zi,zt):
    specimen=stack(layers,zi,zt)
    frequency=np.linspace(0,100,45)
    actual=kernel.reflection_spectrum(specimen,frequency)
    r,t=ode_oracle(specimen,frequency)
    np.testing.assert_allclose(response(actual,"reflection"),r,rtol=1e-11,atol=2e-12)
    np.testing.assert_allclose(response(actual,"transmission"),t,rtol=1e-11,atol=2e-12)
    assert min(actual["absorptance"])>=-2e-14
    np.testing.assert_allclose(actual["transmittance"],abs(t)**2*zi/zt,rtol=1e-11,atol=2e-12)
    assert "diagnostics" in actual and "rounded" in actual["diagnostics"]["evidence_status"].lower()


def test_si_conversion_tau_three_nanoseconds_and_exact_derived_limits():
    spec=stack()
    spectrum=kernel.reflection_spectrum(spec,[0.,50.])
    values=spectrum["materials"][0]
    assert values["low_frequency_speed_m_s"]==1500.
    assert values["high_frequency_speed_m_s"]==2000.
    assert values["zero_frequency_impedance_mrayl"]==1.5
    f=50e6;s=2j*math.pi*f;tau=3e-9
    modulus=2.25e9+1.75e9*s*tau/(1+s*tau)
    impedance=np.sqrt(1000*modulus);gamma=1000*s/impedance
    assert values["attenuation_np_m"][1]==pytest.approx(gamma.real,rel=3e-15)
    assert values["attenuation_db_mm"][1]==pytest.approx(20/math.log(10)*gamma.real/1000,rel=3e-15)
    assert values["phase_speed_m_s"][1]==pytest.approx(2*math.pi*f/gamma.imag,rel=3e-15)
    assert values["phase_speed_m_s"][0]==1500. and values["attenuation_np_m"][0]==0.


def test_constitutive_identity_and_rhp_branch_against_exact_ball_algebra():
    l=layer()
    with kernel._ARITHMETIC_LOCK,ctx.workprec(128):
        for real in (0.,.01,10.):
            for imag in (-1000.,-1.,0.,1.,1000.):
                s=acb(real,imag)
                m,z,g=kernel._material(l,s)
                ss=s*10**6;tau=arb(l["relaxation_time_us"])/10**6
                m0=arb(l["relaxed_modulus_gpa"])*10**9;mi=arb(l["unrelaxed_modulus_gpa"])*10**9
                factored=(m0+mi*tau*ss)/(1+tau*ss)
                assert m.overlaps(factored) and z.real>0
                assert (z*z).overlaps(arb(l["density_kg_m3"])*m)
                assert (g*z).overlaps(arb(l["density_kg_m3"])*ss)
                if real>0: assert (ss/m).real>0 and g.real>0
                conjugates=kernel._material(l,acb(real,-imag))
                for a,b in zip((m,z,g),conjugates): assert a.conjugate().overlaps(b)


def test_material_asymptotes_with_si_units():
    l=layer(relaxation_time_us=1.)
    omega_low=1e-4/(l["relaxation_time_us"]*1e-6)
    omega_high=1e4/(l["relaxation_time_us"]*1e-6)
    with kernel._ARITHMETIC_LOCK,ctx.workprec(128):
        low=kernel._material(l,acb(0,omega_low/1e6))[2]
        high=kernel._material(l,acb(0,omega_high/1e6))[2]
    delta=1.75e9;tau=1e-6
    assert float(low.real)==pytest.approx(delta*tau*omega_low**2/(2*2.25e9*1500),rel=1e-6)
    assert float(high.real)==pytest.approx(delta/(2*4e9*tau*2000),rel=1e-6)


def test_zero_relaxation_tau_inactive_and_matched_phase():
    a=stack([layer(relaxed_modulus_gpa=4.,unrelaxed_modulus_gpa=4.)],2.,2.)
    b=deepcopy(a);b["layers"][0]["relaxation_time_us"]=100.
    f=[0.,13.,57.]
    ra,rb=(kernel.reflection_spectrum(v,f) for v in (a,b))
    assert ra==rb
    assert ra["reflection"]["phase_deg"]==[None]*3
    assert np.array_equal(response(ra,"reflection"),np.zeros(3))
    np.testing.assert_allclose(response(ra,"transmission"),np.exp(-2j*np.pi*np.array(f)*.025),atol=2e-15)
    assert ra["materials"][0]["zero_relaxation"] is True


def test_identical_subdivision_zero_width_removal_and_order_sensitivity():
    f=np.linspace(0,80,21)
    original=stack([layer(thickness_mm=.125)])
    split=stack([layer(thickness_mm=.0625),layer(thickness_mm=.0625)])
    with_zero=deepcopy(split);with_zero["layers"].insert(1,layer(thickness_mm=0,density_kg_m3=8000.))
    spectra=[kernel.reflection_spectrum(v,f) for v in (original,split,with_zero)]
    for key in ("reflection","transmission"):
        for current in spectra[1:]: np.testing.assert_allclose(response(current,key),response(spectra[0],key),atol=1e-15)
    pair=stack([layer(),layer(thickness_mm=.02,relaxed_modulus_gpa=16.,unrelaxed_modulus_gpa=20.)])
    reversed_pair=deepcopy(pair);reversed_pair["layers"].reverse()
    a,b=(kernel.reflection_spectrum(v,f) for v in (pair,reversed_pair))
    assert max(abs(response(a,"reflection")-response(b,"reflection")))>.05
    np.testing.assert_allclose(response(a,"transmission"),response(b,"transmission"),atol=2e-15)


@pytest.mark.parametrize("field,value", [("density_kg_m3",0),("density_kg_m3",True),
    ("relaxed_modulus_gpa",float("nan")),("unrelaxed_modulus_gpa",1.),("relaxation_time_us",0),
    ("pressure_loss_db_mm",0),("sound_speed_m_s",1500),("impedance_mrayl",1.5),("material_id","silicon")])
def test_finite_layer_strict_fields(field,value):
    s=stack();s["layers"][0][field]=value
    with pytest.raises(ValueError): MixedStack.model_validate(s)


@pytest.mark.parametrize("field", ["source_column","pulse","twin","path_model"])
def test_no_legacy_or_source_inference(field):
    with pytest.raises(ValueError): MixedLayeredAnalysisRequest.model_validate({"kind":"mixed_layered_analysis","name":"Manual","stack":stack(),field:{}})


@pytest.mark.parametrize("patch", [{"precision_bits":True},{"precision_bits":128.},{"sample_rate_mhz":399.},
    {"record_duration_us":12.},{"gamma_order":True},{"record_start_us":float("inf")}])
def test_gamma_schema_guard(patch):
    with pytest.raises(ValueError): MixedCausalPulseSettings.model_validate(patch)


@pytest.mark.parametrize("frequency", [[True],[np.nan],[-1.],[301.],[1.,1.],np.zeros(8194)])
def test_frequency_rejection_before_material(frequency,monkeypatch):
    monkeypatch.setattr(kernel,"_material",lambda *_:pytest.fail("Material evaluated before preflight"))
    with pytest.raises(ValueError): kernel.reflection_spectrum(stack(),frequency)


def test_schema_layer_and_depth_bounds_and_roundtrip():
    with pytest.raises(ValueError): MixedStack.model_validate(stack([layer()]*9))
    with pytest.raises(ValueError): MixedStack.model_validate(stack([layer(thickness_mm=4.)]*2))
    req=MixedLayeredAnalysisRequest(kind="mixed_layered_analysis",name="Manual",stack=stack(),causal_pulse={})
    encoded=req.model_dump(mode="json")
    assert MixedLayeredAnalysisRequest.model_validate(encoded).model_dump(mode="json")==encoded


def test_spectrum_work_limit_checked_before_coefficient_allocations(monkeypatch):
    monkeypatch.setattr(kernel,"MAX_MATERIAL_FREQUENCY_WORK",100)
    monkeypatch.setattr(kernel,"_scattering",lambda *_:pytest.fail("Scattering evaluated before budget"))
    with pytest.raises(ValueError,match="budget"): kernel.reflection_spectrum(stack(),list(range(10)))


def test_exact_authored_real_parameters_are_not_rounded_derived_sls_properties():
    water=real_layer(impedance_mrayl=1.48,sound_speed_m_s=1480.)
    with kernel._ARITHMETIC_LOCK,ctx.workprec(192):
        m,z,g=kernel._material(water,acb(2,17))
        authored_z=arb(1.48)*10**6
        assert z.real==authored_z and z.imag==0
        assert m.real==authored_z*1480
        assert (g*1480).overlaps(acb(2,17)*10**6)
        # Decimal 1.48 is not exact binary64, so the exact model density is not 1000.
        rho=z.real/1480
        difference=Fraction(1.48)*10**6/1480-1000
        assert difference<0 and not rho.contains(1000)
    result=kernel.reflection_spectrum(stack([water]),[0.,13.,99.])
    material=result['materials'][0]
    assert material['kind']=='lossless_real' and material['zero_relaxation'] is None
    assert material['phase_speed_m_s']==[1480.]*3
    assert material['impedance_real_mrayl']==[1.48]*3
    for key in ('attenuation_db_mm','attenuation_np_m','impedance_imag_mrayl'):
        assert material[key]==[0.]*3
    assert not {'density_kg_m3','relaxation_time_us','relaxed_modulus_gpa'} & material.keys()


def test_real_elastic_equivalence_exactly_representable_and_eight_authored_counts():
    f=[0.,11.,53.,89.]
    real=stack([real_layer(thickness_mm=.125)])
    elastic=stack([layer(thickness_mm=.125,relaxed_modulus_gpa=4.,unrelaxed_modulus_gpa=4.)])
    a,b=(kernel.reflection_spectrum(v,f) for v in (real,elastic))
    for key in ('reflection','transmission'):
        np.testing.assert_allclose(response(a,key),response(b,key),rtol=0,atol=2e-16)
    layers=[real_layer(thickness_mm=0)]*3+[layer(thickness_mm=0)]*3+[real_layer(),layer()]
    out=kernel.reflection_spectrum(stack(layers),f)
    counts={key:out['diagnostics'][key] for key in ('layer_count','authored_layer_count','active_layer_count','lossless_real_layer_count','sls_layer_count')}
    assert counts=={'layer_count':8,'authored_layer_count':8,'active_layer_count':2,'lossless_real_layer_count':4,'sls_layer_count':4}
    assert [v['layer_index'] for v in out['materials']]==list(range(8))
    assert [v['zero_thickness'] for v in out['materials']]==[True]*6+[False]*2


def test_matched_front_and_terminal_spacers_have_different_reference_plane_factors():
    f=np.array([0.,7.,23.,71.])
    base=stack([layer()],1.48,3.)
    front=deepcopy(base);front['layers'].insert(0,real_layer(thickness_mm=.125,impedance_mrayl=1.48,sound_speed_m_s=1480.))
    back=deepcopy(base);back['layers'].append(real_layer(thickness_mm=.0625,impedance_mrayl=3.,sound_speed_m_s=1800.))
    a,b,c=(kernel.reflection_spectrum(v,f) for v in (base,front,back))
    pf=np.exp(-2j*np.pi*f*(1000*.125/1480))
    pt=np.exp(-2j*np.pi*f*(1000*.0625/1800))
    np.testing.assert_allclose(response(b,'reflection'),response(a,'reflection')*pf**2,rtol=1e-13,atol=2e-15)
    np.testing.assert_allclose(response(b,'transmission'),response(a,'transmission')*pf,rtol=1e-13,atol=2e-15)
    np.testing.assert_allclose(response(c,'reflection'),response(a,'reflection'),rtol=1e-13,atol=2e-15)
    np.testing.assert_allclose(response(c,'transmission'),response(a,'transmission')*pt,rtol=1e-13,atol=2e-15)


def test_lossless_slab_geometric_formula_not_intensity_recursion():
    spec=stack([real_layer(thickness_mm=.03125,impedance_mrayl=8.)],1.48,3.)
    f=np.linspace(0,100,41);l=spec['layers'][0]
    r01=(8-1.48)/(8+1.48);r12=(3-8)/(3+8)
    p=np.exp(-2j*np.pi*f*(1000*l['thickness_mm']/l['sound_speed_m_s']))
    expected_r=(r01+r12*p*p)/(1+r01*r12*p*p)
    expected_t=(2*8/(1.48+8))*(2*3/(8+3))*p/(1+r01*r12*p*p)
    result=kernel.reflection_spectrum(spec,f)
    np.testing.assert_allclose(response(result,'reflection'),expected_r,rtol=3e-14,atol=3e-14)
    np.testing.assert_allclose(response(result,'transmission'),expected_t,rtol=3e-14,atol=3e-14)
    np.testing.assert_allclose(np.array(result['reflectance'])+result['transmittance'],1.,atol=5e-16)


@pytest.mark.parametrize('field,value',[('impedance_mrayl',0),('impedance_mrayl',True),('sound_speed_m_s',float('inf')),
    ('relaxation_time_us',.003),('density_kg_m3',1000.),('relaxed_modulus_gpa',2.25),('kind','SLS'),('name','   ')])
def test_real_strict_fields_and_explicit_tag(field,value):
    medium=real_layer();medium[field]=value
    with pytest.raises(ValueError): MixedStack.model_validate(stack([medium]))


def test_kind_and_exact_thickness_guards_no_implicit_union_or_float_sum():
    with pytest.raises(ValueError): MixedLayeredAnalysisRequest.model_validate({'name':'untagged','stack':stack()})
    for medium in (real_layer(),layer()):
        del medium['kind']
        with pytest.raises(ValueError): MixedStack.model_validate(stack([medium]))
    entries=[real_layer(thickness_mm=3.),layer(thickness_mm=3.),real_layer(thickness_mm=2**-53)]
    assert sum(v['thickness_mm'] for v in entries)==6.
    with pytest.raises(ValueError,match='exact represented'): MixedStack.model_validate(stack(entries))
    with pytest.raises(ValueError): MixedStack.model_validate(stack([real_layer(thickness_mm=0)]*9))


def test_real_rhp_conjugation_and_contractivity_for_mixed_stack():
    spec=stack([real_layer(),layer(),real_layer(impedance_mrayl=.001,thickness_mm=.0001)])
    with kernel._ARITHMETIC_LOCK,ctx.workprec(192):
        for sigma in (0.,.01,3.):
            for omega in (0.,1.,100.):
                s=acb(sigma,omega)
                m,z,g=kernel._material(spec['layers'][0],s)
                assert m.real>0 and z.real>0
                if sigma>0: assert g.real>0 and (s*10**6/m).real>0
                r,t,_=kernel._scattering(spec,s)
                rc,tc,_=kernel._scattering(spec,s.conjugate())
                assert r.conjugate().overlaps(rc) and t.conjugate().overlaps(tc)
                assert float(abs(r).upper())<=1+2e-15


def test_spectrum_never_mutates_authored_stack_and_actual_frequency_list():
    spec=stack([real_layer(),layer()]);frequencies=[0.,13.,41.]
    before=deepcopy((spec,frequencies))
    result=kernel.reflection_spectrum(spec,frequencies)
    assert (spec,frequencies)==before
    assert result['frequency_mhz']==frequencies
