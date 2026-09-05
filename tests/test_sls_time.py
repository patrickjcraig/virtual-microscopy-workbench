"""Reflected SLS gamma contract, independent elastic echo timing and guards."""
from copy import deepcopy
from fractions import Fraction
import math

import numpy as np
import pytest

from test_sls_acoustics import layer,stack
from virtual_microscopy import sls_time as kernel


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
