"""Independent exact-rational controls for the standalone comparison adapter."""
from fractions import Fraction as Q
import math

import numpy as np
import pytest

from virtual_microscopy import sls_comparison_math as m


def signals(real, imaginary, magnitude):
    return {k:np.array(v,np.float64) for k,v in zip(m.SIGNALS,(real,imaginary,magnitude))}


def upward(q):
    v=float(q)
    return math.nextafter(v,math.inf) if Q(v)<q else v


@pytest.mark.parametrize('eps_a,eps_b',[(0.,0.),(2.**-40,2.**-35),(1e-7,4e-8),(float.fromhex('0x0.0000000000001p-1022'),)*2])
def test_independent_fraction_five_minimal_outward_components(eps_a,eps_b):
    a=signals([1.,-2.,.3],[1e4,4.,-3.],[25.,3.,0.])
    b=signals([-1.,3.,.4],[5e3,-8.,2.],[3.,25.,.1])
    observed=m.column_bounds(a,b,eps_a,eps_b)
    source=upward(Q(eps_a)+Q(eps_b))
    rho={k:Q(1,2**53)*(Q(max(abs(x) for x in a[k]))+Q(max(abs(x) for x in b[k])))+Q(1,2**1074) for k in m.SIGNALS}
    c=upward(rho['rf']+rho['imaginary']); e=upward(rho['envelope'])
    expected={'source_sum':source,'complex_arithmetic':c,'magnitude_arithmetic':e,
        'complex_total':upward(Q(source)+Q(c)),'magnitude_total':upward(Q(source)+Q(e))}
    assert observed==expected
    difference=m.checked_differences(a,b)
    for k in m.SIGNALS:
        for x,y,d in zip(a[k],b[k],difference[k]):
            assert d==float(Q(float(y))-Q(float(x)))
            assert abs(Q(float(d))-(Q(float(y))-Q(float(x))))<=rho[k]


def test_signed_complex_and_saved_magnitude_residuals_are_distinct():
    a=signals([3.,0.],[4.,0.],[5.,0.]);b=signals([-3.,1.],[-4.,0.],[5.,1.])
    d=m.checked_differences(a,b)
    assert d['rf'].tolist()==[-6.,1.] and d['imaginary'].tolist()==[-8.,0.]
    assert d['envelope'].tolist()==[0.,1.]
    assert np.hypot(d['rf'],d['imaginary']).tolist()==[10.,1.]
    metrics=m.rf_metrics(a,b,d,np.array([.1,.2]))
    assert metrics['complex']['max_location']=={'time_index':0,'time_us':.1,'signed_real_difference':-6.,'signed_imaginary_difference':-8.}
    assert metrics['rf']['bias']==-2.5
    assert metrics['complex']['rmse']==pytest.approx(math.sqrt(101/2))


def test_gates_keep_actual_locator_and_difference_of_statistics():
    a=signals([1.,-3.,0.,9.],[0.]*4,[1.,3.,0.,9.])
    b=signals([1.,0.,3.,9.],[0.]*4,[1.,0.,3.,9.])
    d=m.checked_differences(a,b);time=np.array([.1,.2,.3,.4])
    metrics=m.rf_metrics(a,b,d,time,(1,3))
    assert metrics['rf']['max_location']['time_index']==1
    assert metrics['rf']['max_location']['signed_difference']==3.
    gate=m.gate_products(a,b,d,1,3)
    assert gate['rms_rf']['difference']==0.
    assert gate['peak_envelope']['difference']==0.
    assert gate['residual_gate']['rms_rf']==3.
    assert gate['residual_gate']['peak_absolute_saved_magnitude_difference']==3.


def test_zero_reference_and_self_have_explicit_null_reasons():
    a=signals([0.,0.],[0.,0.],[0.,0.]);d=m.checked_differences(a,a)
    metrics=m.rf_metrics(a,a,d,np.array([0.,.1]))
    assert metrics['complex']['relative_l2'] is None
    assert metrics['complex']['relative_l2_reason']
    assert metrics['complex']['max_location']['time_index']==0
    assert m.column_bounds(a,a,1e-8,1e-8)['source_sum']>=2e-8


def test_gradual_underflow_arithmetic_and_strided_recordings():
    tiny=np.nextafter(0.,1.)
    a=signals([0.,tiny],[-tiny,0.],[0.,tiny]);b=signals([tiny,0.],[0.,-tiny],[tiny,0.])
    for source in (a,b):
        for k in source:
            source[k]=np.repeat(source[k],2)[::2]
            assert not source[k].flags.c_contiguous
    d=m.checked_differences(a,b)
    assert d['rf'].tolist()==[tiny,-tiny]
    bounds=m.column_bounds(a,b,float(tiny),float(2*tiny))
    assert bounds['source_sum']==3*tiny
    assert bounds['complex_arithmetic']>=2*tiny


@pytest.mark.parametrize('bad',[math.nan,math.inf,-1.,True])
def test_bad_source_totals_reject(bad):
    a=signals([0.,1.],[0.,0.],[0.,1.])
    with pytest.raises(ValueError):m.column_bounds(a,a,bad,0.)


@pytest.mark.parametrize('mutation',[lambda a:a.update(rf=np.array([1],np.float32)),
    lambda a:a.update(rf=np.array([np.nan,0.])),lambda a:a.update(imaginary=np.array([0.])),
    lambda a:a.update(envelope=np.array([-1.,1.]))])
def test_signal_dtype_finiteness_shape_and_magnitude_guards(mutation):
    a=signals([0.,1.],[0.,0.],[0.,1.]);mutation(a)
    with pytest.raises(ValueError):m.checked_differences(a,a)


def test_source_and_subtraction_overflow_reject_without_mutation():
    large=np.finfo(float).max
    a=signals([0.,0.],[0.,0.],[0.,0.])
    with pytest.raises(ValueError):m.column_bounds(a,a,large,large)
    a['rf'][0]=-large;b={k:v.copy() for k,v in a.items()};b['rf'][0]=large
    with pytest.raises(ValueError):m.checked_differences(a,b)
    assert a['rf'][0]==-large


def test_frequency_diagnostics_above_old_rf_count_limit_and_no_phase_difference():
    f=np.linspace(0,150,3001)
    a={'reflection':{'real':np.ones(len(f)),'imag':np.zeros(len(f)),'magnitude':np.ones(len(f))},
       'transmission':{'real':np.ones(len(f)),'imag':np.zeros(len(f)),'magnitude':np.ones(len(f))},
       'reflectance':np.ones(len(f)),'transmittance':np.ones(len(f)),'absorptance':np.zeros(len(f))}
    b={key:{k:2*v for k,v in value.items()} if isinstance(value,dict) else 2*value for key,value in a.items()}
    result=m.spectrum_comparison(a,b,f)
    assert len(result['difference']['reflection']['real'])==3001
    assert result['metrics']['reflection']['real']['bias']==1.
    assert result['metrics']['reflection']['complex']['max_location']['frequency_index']==0
    assert 'phase_deg' not in result['difference']['reflection']
    assert 'bounds' not in result
