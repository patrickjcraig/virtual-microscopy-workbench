"""Mixed reflected gamma contract, independent real/elastic echoes and admission."""
from copy import deepcopy
from fractions import Fraction
import math

import numpy as np
import pytest

from test_mixed_acoustics import layer,stack,real_layer
from virtual_microscopy import mixed_time as kernel


def settings(**changes):
    return {"center_frequency_mhz":50.,"fractional_bandwidth":.5,"gamma_order":12,
        "absolute_tolerance":1e-7,"precision_bits":128,"surface_standoff_mm":0.,**changes}


def gamma(time,config):
    values=np.asarray(time,float)
    m=config["gamma_order"];f=config["center_frequency_mhz"]
    a=math.pi*f*config["fractional_bandwidth"]/math.sqrt(math.expm1(2*math.log(2)/(m+1)))
    result=np.zeros(values.shape,complex);mask=values>0;x=a*values[mask]
    result[mask]=np.exp(m*(1+np.log(x/m))-x)*np.exp(2j*math.pi*f*(values[mask]-m/a))
    return result


def elastic_slab(spec,time,config):
    l=spec["layers"][0]
    assert l["relaxed_modulus_gpa"]==l["unrelaxed_modulus_gpa"]
    z0,z2=(spec[side]["impedance_mrayl"] for side in ("incident","terminal"))
    c=math.sqrt(l["relaxed_modulus_gpa"]*1e9/l["density_kg_m3"])
    z1=l["density_kg_m3"]*c/1e6
    r01=(z1-z0)/(z1+z0);r10=-r01;r12=(z2-z1)/(z2+z1)
    t01=2*z1/(z1+z0);t10=2*z0/(z0+z1)
    delay=2000*l["thickness_mm"]/c
    t=np.asarray(time)-2000*config["surface_standoff_mm"]/spec["incident"]["sound_speed_m_s"]
    result=r01*gamma(t,config)
    for n in range(1,max(0,math.floor(float(t.max())/delay))+1):
        result+=t01*t10*r12*(r10*r12)**(n-1)*gamma(t-n*delay,config)
    return result


def actual(result):
    return np.asarray(result["rf"])+1j*np.asarray(result["imaginary"])


def certified(result,oracle,tolerance):
    d=result["diagnostics"];bound=d["total_error_bound"]
    assert 0<bound<=tolerance
    assert max(abs(actual(result)-oracle))<=bound
    assert max(abs(np.asarray(result["envelope"])-abs(oracle)))<=bound
    keys=("analytic_alias_bound","frequency_cutoff_bound","arithmetic_complex_bound","arithmetic_envelope_bound")
    assert Fraction(bound)>=sum((Fraction(d[key]) for key in keys),Fraction())
    assert d["certificate_version"]==kernel.CERTIFICATE_VERSION
    assert "transmission" not in result


@pytest.mark.parametrize("zi,zt", [(1.48,8.),(8.,1.48),(4.,4.)])
def test_empty_interface_polarity_complex_carrier_and_standoff(zi,zt):
    spec=stack([],zi,zt);config=settings(surface_standoff_mm=.05)
    time=[i/400 for i in range(161)]
    result=kernel.causal_gamma_response(spec,time,config)
    expected=(zt-zi)/(zt+zi)*gamma(np.array(time)-2000*.05/1480,config)
    certified(result,expected,config["absolute_tolerance"])
    assert result["time_us"]==time


@pytest.mark.parametrize("rho,modulus,zi,zt", [(1000.,4.,1.48,1.48),(1000.,.004,8.,2.),(8000.,128.,1.48,8.)])
def test_elastic_slab_independent_finite_causal_echo_series(rho,modulus,zi,zt):
    spec=stack([layer(thickness_mm=.125,density_kg_m3=rho,relaxed_modulus_gpa=modulus,unrelaxed_modulus_gpa=modulus)],zi,zt)
    time=[i/400 for i in range(241)];config=settings(surface_standoff_mm=.02)
    result=kernel.causal_gamma_response(spec,time,config)
    certified(result,elastic_slab(spec,time,config),config["absolute_tolerance"])


def test_dispersive_zero_width_and_identical_subdivision():
    config=settings();time=[i/400 for i in range(201)]
    original=stack([layer(thickness_mm=.125)])
    split=stack([layer(thickness_mm=.0625),layer(thickness_mm=.0625)])
    with_zero=deepcopy(split);with_zero["layers"].insert(1,layer(thickness_mm=0,unrelaxed_modulus_gpa=500.))
    results=[kernel.causal_gamma_response(v,time,config) for v in (original,split,with_zero)]
    for result in results[1:]:
        bound=result["diagnostics"]["total_error_bound"]+results[0]["diagnostics"]["total_error_bound"]
        assert max(abs(actual(result)-actual(results[0])))<=bound
    assert actual(results[1]).tobytes()==actual(results[2]).tobytes()


def test_record_period_precision_and_tolerance_variants_use_actual_centers():
    spec=stack();time=[i/400 for i in range(241)]
    tight=settings(absolute_tolerance=1e-10,precision_bits=192)
    base=kernel.causal_gamma_response(spec,time,settings())
    exact=kernel.causal_gamma_response(spec,time,tight)
    subset=time[40:180]
    cropped=kernel.causal_gamma_response(spec,subset,tight)
    assert cropped["time_us"]==subset
    assert cropped["diagnostics"]["period_us"]!=base["diagnostics"]["period_us"]
    assert max(abs(actual(base)-actual(exact)))<=base["diagnostics"]["total_error_bound"]+exact["diagnostics"]["total_error_bound"]
    assert max(abs(actual(cropped)-actual(exact)[40:180]))<=cropped["diagnostics"]["total_error_bound"]+exact["diagnostics"]["total_error_bound"]
    assert exact["diagnostics"]["total_error_bound"]<base["diagnostics"]["total_error_bound"]


def test_pre_standoff_causality_and_no_sample_clipping():
    config=settings(surface_standoff_mm=.2);time=[i/400 for i in range(101)]
    result=kernel.causal_gamma_response(stack(),time,config)
    assert time[-1]<result["diagnostics"]["surface_time_us"]
    certified(result,np.zeros(len(time),complex),config["absolute_tolerance"])


@pytest.mark.parametrize("time,config", [([-1.,0.],{}),([0.,0.],{}),([0.,float("nan")],{}),([0.,1.],{}),
    ([0.,.0025],{"precision_bits":True}),([0.,.0025],{"pressure_loss_db_mm":0}),
    ([0.,.0025],{"sample_rate_mhz":400}),([0.,.0025],{"record_start_us":True,"record_duration_us":.05,"sample_rate_mhz":400}),
    ([0.,.0025],{"record_start_us":.1,"record_duration_us":.05,"sample_rate_mhz":400})])
def test_input_admission_precedes_scattering(time,config,monkeypatch):
    monkeypatch.setattr(kernel,"_scattering",lambda *a,**k:pytest.fail("Scattering before input validation"))
    with pytest.raises(ValueError): kernel.causal_gamma_response(stack(),time,config)


def test_explicit_record_metadata_roundtrips_exact_actual_values():
    config=settings(record_start_us=.1,record_duration_us=.05,sample_rate_mhz=400.)
    time=[.1+i/400 for i in range(21)]
    assert kernel.causal_gamma_response(stack(),time,config)["time_us"]==time
    changed=time.copy();changed[10]=float(np.nextafter(changed[10],np.inf))
    with pytest.raises(ValueError,match="match"): kernel.causal_gamma_response(stack(),changed,config)


@pytest.mark.parametrize("setting", ["MAX_FREQUENCY_TERMS","MAX_LAYER_FREQUENCY_WORK","MAX_INVERSE_WORK","MAX_PEAK_BYTES"])
def test_budget_rejection_before_coefficient_allocation(setting,monkeypatch):
    monkeypatch.setattr(kernel,setting,1)
    monkeypatch.setattr(kernel,"_scattering",lambda *a,**k:pytest.fail("Scattering before budget"))
    with pytest.raises(ValueError,match="budget"): kernel.causal_gamma_response(stack(),[i/400 for i in range(101)],settings())


def test_underresolved_precision_rejects_without_relaxing_request():
    with pytest.raises(ValueError,match="bound|precision"):
        kernel.causal_gamma_response(stack(),[i/400 for i in range(801)],settings(precision_bits=64,absolute_tolerance=1e-12))


def real_slab_oracle(spec,time,config):
    """Independent path enumeration: only echoes whose causal onset is in the record."""
    medium=spec['layers'][0];assert medium['kind']=='lossless_real'
    z0=spec['incident']['impedance_mrayl'];z1=medium['impedance_mrayl'];z2=spec['terminal']['impedance_mrayl']
    r=(z1-z0)/(z1+z0);back=(z2-z1)/(z2+z1)
    transmission_product=4*z0*z1/(z0+z1)**2
    t=np.asarray(time)-2000*config['surface_standoff_mm']/spec['incident']['sound_speed_m_s']
    delay=2000*medium['thickness_mm']/medium['sound_speed_m_s']
    value=r*gamma(t,config)
    for n in range(1,max(0,math.floor(float(max(t))/delay))+1):
        value+=transmission_product*back*(-r*back)**(n-1)*gamma(t-n*delay,config)
    return value


@pytest.mark.parametrize('z,c,zi,zt',[(2.,2000.,1.48,1.48),(.01,300.,8.,2.),(20.,8000.,1.48,8.)])
def test_real_slab_independent_echo_oracle_covers_signed_complex_and_magnitude(z,c,zi,zt):
    spec=stack([real_layer(impedance_mrayl=z,sound_speed_m_s=c,thickness_mm=.03125)],zi,zt)
    time=[i/400 for i in range(241)];config=settings(surface_standoff_mm=.02)
    result=kernel.causal_gamma_response(spec,time,config)
    certified(result,real_slab_oracle(spec,time,config),config['absolute_tolerance'])
    assert min(result['rf'])<0
    assert max(np.asarray(result['envelope'])-np.abs(result['rf']))>.001


def test_finite_front_spacer_exterior_standoff_equivalence_and_terminal_delay_invariance():
    base=stack([layer(thickness_mm=.03125)])
    front=deepcopy(base);front['layers'].insert(0,real_layer(thickness_mm=.0625,impedance_mrayl=1.48,sound_speed_m_s=1480.))
    terminal=deepcopy(base);terminal['layers'].append(real_layer(thickness_mm=.125,impedance_mrayl=1.48,sound_speed_m_s=1800.))
    time=[i/400 for i in range(241)]
    a=kernel.causal_gamma_response(base,time,settings(surface_standoff_mm=.0625))
    b=kernel.causal_gamma_response(front,time,settings())
    c=kernel.causal_gamma_response(terminal,time,settings(surface_standoff_mm=.0625))
    for other in (b,c):
        bound=a['diagnostics']['total_error_bound']+other['diagnostics']['total_error_bound']
        assert max(abs(actual(a)-actual(other)))<=bound
        assert max(abs(np.array(a['envelope'])-other['envelope']))<=bound
    assert a['diagnostics']['surface_time_us']>0
    assert b['diagnostics']['surface_time_us']==0 # Front medium stays in the transfer, not metadata standoff.


def test_exact_real_elastic_time_equivalence_and_real_subdivision():
    real=stack([real_layer(thickness_mm=.125)])
    elastic=stack([layer(thickness_mm=.125,relaxed_modulus_gpa=4.,unrelaxed_modulus_gpa=4.)])
    split=stack([real_layer(thickness_mm=.0625),real_layer(thickness_mm=.0625)])
    time=[i/400 for i in range(201)];config=settings(absolute_tolerance=1e-9,precision_bits=192)
    results=[kernel.causal_gamma_response(v,time,config) for v in (real,elastic,split)]
    for result in results:
        certified(result,real_slab_oracle(real,time,config),config['absolute_tolerance'])
    for result in results[1:]:
        assert max(abs(actual(result)-actual(results[0])))<=result['diagnostics']['total_error_bound']+results[0]['diagnostics']['total_error_bound']


def test_mixed_actual_nonuniform_centers_and_frozen_input_roundtrip():
    spec=stack([real_layer(thickness_mm=.015625),layer(thickness_mm=.03125)])
    time=[0.]+[i/800+(1e-6 if i%2 else 0.) for i in range(1,101)]
    time_before=time.copy();before=deepcopy(spec);config=settings()
    result=kernel.causal_gamma_response(spec,time,config)
    assert result['time_us']==time_before and spec==before and time==time_before
    estimate=kernel.estimate_causal_gamma(spec,time,config)
    for name in ('layer_count','authored_layer_count','active_layer_count','lossless_real_layer_count','sls_layer_count',
                 'frequency_terms','time_samples','layer_frequency_work_units','inverse_work_units','estimated_peak_bytes'):
        assert estimate[name]==result['diagnostics'][name]
    assert estimate['layer_count']==2 and estimate['lossless_real_layer_count']==1


@pytest.mark.parametrize('bad_layers',[[{**real_layer(),'relaxation_time_us':.003}],
    [{**layer(),'impedance_mrayl':1.48}], [{k:v for k,v in real_layer().items() if k!='kind'}], [real_layer()]*9])
def test_typed_stack_guards_precede_any_coefficient_work(bad_layers,monkeypatch):
    monkeypatch.setattr(kernel,'_scattering',lambda *a,**kw:pytest.fail('No material/scattering before typed admission'))
    with pytest.raises(ValueError): kernel.causal_gamma_response(stack(bad_layers),[0.,.0025],settings())


def test_authored_zero_layers_retained_in_budget_without_active_transfer_effect():
    a=stack([real_layer()]);b=stack([real_layer()]+[layer(thickness_mm=0)]*7)
    time=[i/400 for i in range(81)];config=settings()
    ar,br=(kernel.causal_gamma_response(v,time,config) for v in (a,b))
    for key in ('rf','imaginary','envelope'): assert ar[key]==br[key]
    d=br['diagnostics']
    assert d['authored_layer_count']==8 and d['active_layer_count']==1
    assert d['sls_layer_count']==7 and d['lossless_real_layer_count']==1
    assert d['material_frequency_work_units']==d['frequency_terms']*(12*8+8)
    assert d['estimated_peak_bytes']>ar['diagnostics']['estimated_peak_bytes']
