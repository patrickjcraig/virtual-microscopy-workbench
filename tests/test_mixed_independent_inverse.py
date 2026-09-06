"""Independent analytic and accelerated-transform checks of mixed reflected RF.

No production material, recursion, pulse or inverse helper enters the oracle.
Degree convergence is a diagnostic, never a certified inverse remainder.
"""
from copy import deepcopy

from mpmath import mp
import pytest

from tools.mixed_independent_oracle import frequency_transfer, inverse_transfer
from virtual_microscopy.mixed_acoustics import reflection_spectrum
from virtual_microscopy.mixed_time import causal_gamma_response


def real(z=3., c=2000., d=.015625):
    return {'kind':'lossless_real','name':'Explicit arbitrary real medium',
            'impedance_mrayl':z,'sound_speed_m_s':c,'thickness_mm':d}


def fixture(second_sls=False):
    medium=lambda z:{'name':'Assumed real exterior','impedance_mrayl':z,'sound_speed_m_s':2000.}
    sls={'kind':'sls','name':'Arbitrary dispersive control','thickness_mm':.03125,
         'density_kg_m3':1000.,'relaxed_modulus_gpa':4.,'unrelaxed_modulus_gpa':9.,
         'relaxation_time_us':.01}
    layers=[real(),sls]
    if second_sls:
        layers.append({**sls,'name':'Second arbitrary dispersive control','thickness_mm':.0078125,
                       'density_kg_m3':1500.,'relaxed_modulus_gpa':6.,'unrelaxed_modulus_gpa':15.,
                       'relaxation_time_us':.012})
    return {'incident':medium(3.),'terminal':medium(5.),'layers':[*layers,real(5.,2000.,.0078125)]}


def settings(**changes):
    return {'center_frequency_mhz':10.,'fractional_bandwidth':.8,'gamma_order':12,
            'surface_standoff_mm':0.,'absolute_tolerance':1e-8,'precision_bits':192,**changes}


def pulse(ctx, options, time):
    """Independent time-domain gamma expression at an exact represented time."""
    t=ctx.mpf(time)
    if t<=0:
        return ctx.mpc(0)
    m=options['gamma_order']; omega=2*ctx.pi*ctx.mpf(options['center_frequency_mhz'])
    rate=ctx.pi*ctx.mpf(options['center_frequency_mhz'])*ctx.mpf(options['fractional_bandwidth'])/ctx.sqrt(ctx.power(2,ctx.mpf(2)/(m+1))-1)
    return ctx.power(rate*ctx.e/m,m)*t**m*ctx.exp(-rate*t)*ctx.exp(ctx.j*omega*(t-m/rate))


def assert_record_encloses_reference(observed, index, reference, ctx):
    bound=ctx.mpf(observed['diagnostics']['total_error_bound'])
    actual=ctx.mpc(observed['rf'][index],observed['imaginary'][index])
    assert abs(actual-reference)<=bound
    assert abs(ctx.mpf(observed['envelope'][index])-abs(reference))<=bound


@pytest.mark.parametrize('second_sls',[False,True])
def test_mixed_rf_agrees_with_independent_pv_transfer_and_dehoog(second_sls):
    stack, options=fixture(second_sls),settings()
    initial=deepcopy((stack,options))
    times=[i/200 for i in range(81)]; indices=[25,50,75]
    low=inverse_transfer(stack,options,[times[i] for i in indices],degree=48)
    high=inverse_transfer(stack,options,[times[i] for i in indices],degree=72)
    actual=causal_gamma_response(stack,times,options)
    assert (stack,options)==initial and actual['time_us']==times
    assert 0<actual['diagnostics']['total_error_bound']<=options['absolute_tolerance']
    ctx=mp.clone();ctx.dps=70
    for index,a,b in zip(indices,low['values'],high['values']):
        reference=ctx.mpc(b['real'],b['imaginary'])
        assert abs(reference-ctx.mpc(a['real'],a['imaginary']))<ctx.mpf('1e-18')
        assert_record_encloses_reference(actual,index,reference,ctx)
    assert low['transfer_evaluations']<=3*(2*48+1)
    assert high['transfer_evaluations']<=3*(2*72+1)
    assert 'no oracle error certificate' in high['scope']


@pytest.mark.parametrize('second_sls',[False,True])
def test_mixed_spectra_agree_with_independent_matrix_exponential(second_sls):
    stack=fixture(second_sls); frequencies=[0.,3.125,10.,30.,80.]
    oracle=frequency_transfer(stack,frequencies)
    actual=reflection_spectrum(stack,frequencies)
    ctx=mp.clone();ctx.dps=70
    for i,row in enumerate(oracle['values']):
        for field in ('reflection','transmission'):
            reference=ctx.mpc(row[field]['real'],row[field]['imaginary'])
            observed=ctx.mpc(actual[field]['real'][i],actual[field]['imag'][i])
            assert abs(observed-reference)<ctx.mpf('1e-12')
    assert actual['frequency_mhz']==frequencies


@pytest.mark.parametrize('frequencies',[[],[0.]*514,[False],[301.],[float('nan')]])
def test_independent_spectrum_rejects_unsupported_work(frequencies):
    with pytest.raises(ValueError):
        frequency_transfer(fixture(),frequencies)


def test_mixed_real_slab_rf_against_finite_causal_analytic_echo_series():
    stack=fixture(); stack['layers']=[real(2.,2000.,.03125)]
    options=settings(surface_standoff_mm=.0078125)
    times=[i/200 for i in range(81)]
    actual=causal_gamma_response(stack,times,options)
    ctx=mp.clone();ctx.dps=80
    r01=-ctx.mpf(1)/5; r12=ctx.mpf(3)/7
    delay=2000*ctx.mpf(.03125)/2000
    surface=2000*ctx.mpf(.0078125)/2000
    # Only finitely many echoes have arrived at any selected center. There is
    # no guessed infinite-series truncation or noncausal Gaussian tail.
    for i,t in enumerate(times):
        shifted=ctx.mpf(t)-surface
        reference=r01*pulse(ctx,options,shifted)
        for n in range(max(0,int(ctx.floor(shifted/delay)))):
            reference+=(1-r01*r01)*r12*(-r01*r12)**n*pulse(ctx,options,shifted-(n+1)*delay)
        assert_record_encloses_reference(actual,i,reference,ctx)


def test_independent_split_inverse_matches_empty_interface_gamma_analytically():
    stack=fixture(); stack['layers']=[]
    options=settings();ctx=mp.clone();ctx.dps=70
    actual=inverse_transfer(stack,options,[.125,.25],degree=48)
    for row in actual['values']:
        reference=ctx.mpf(1)/4*pulse(ctx,options,row['time_us'])
        assert abs(ctx.mpc(row['real'],row['imaginary'])-reference)<ctx.mpf('1e-28')


def test_finite_front_spacer_and_exterior_standoff_are_not_double_counted():
    stack=fixture(); options=settings()
    bare={**stack,'layers':stack['layers'][1:]}
    times=[i/200 for i in range(81)]
    finite=causal_gamma_response(stack,times,options)
    exterior=causal_gamma_response(bare,times,settings(surface_standoff_mm=.015625))
    doubled=causal_gamma_response(stack,times,settings(surface_standoff_mm=.015625))
    ctx=mp.clone();ctx.dps=60
    bound=ctx.mpf(finite['diagnostics']['total_error_bound'])+ctx.mpf(exterior['diagnostics']['total_error_bound'])
    differences=[]
    for i in range(len(times)):
        f=ctx.mpc(finite['rf'][i],finite['imaginary'][i]); e=ctx.mpc(exterior['rf'][i],exterior['imaginary'][i])
        assert abs(f-e)<=bound
        assert abs(ctx.mpf(finite['envelope'][i])-ctx.mpf(exterior['envelope'][i]))<=bound
        differences.append(abs(f-ctx.mpc(doubled['rf'][i],doubled['imaginary'][i])))
    assert max(differences)>ctx.mpf('0.01')


@pytest.mark.parametrize('degree,times',[(31,[.1]),(129,[.1]),(True,[.1]),(48,[]),(48,[0.]),(48,[.1]*33)])
def test_independent_inverse_rejects_unsupported_work(degree,times):
    with pytest.raises(ValueError):
        inverse_transfer(fixture(),settings(),times,degree=degree)
