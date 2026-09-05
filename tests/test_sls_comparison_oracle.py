"""Independent delivery-oracle regression for IEEE exact-zero signs."""
import math

import pytest

from tools.sls_comparison_oracle import check_residual_arithmetic


def fixture():
    def source(rf, imaginary):
        return {'spectrum': {'frequency_mhz': [0., 1.]}, 'causal_pulse': {
            'time_us': [0., 1.], 'rf': rf, 'imaginary': imaginary,
            'envelope': [0., 0.], 'diagnostics': {'total_error_bound': 0.}}}
    a = source([0., -0.], [-0., 0.])
    b = source([-0., 0.], [0., -0.])
    residual = {'rf': [-0., 0.], 'imaginary': [0., -0.], 'envelope': [0., 0.]}
    tiny = float.fromhex('0x0.0000000000001p-1022')
    bounds = {'source_sum': 0., 'complex_arithmetic': 2*tiny, 'complex_total': 2*tiny,
              'magnitude_arithmetic': tiny, 'magnitude_total': tiny}
    return a, b, residual, bounds


def test_independent_oracle_preserves_both_signed_zero_subtractions():
    a, b, residual, bounds = fixture()
    checked = check_residual_arithmetic(a, b, residual, bounds)
    assert checked['verified_subtractions'] == 6
    assert checked['bounds'] == bounds


@pytest.mark.parametrize('component,index', [('rf', 0), ('rf', 1), ('imaginary', 0), ('imaginary', 1), ('envelope', 0)])
def test_oracle_rejects_flipped_zero_even_when_numeric_equality_holds(component, index):
    a, b, residual, bounds = fixture()
    before = residual[component][index]
    residual[component][index] = math.copysign(0., -math.copysign(1., before))
    assert residual[component][index] == before
    with pytest.raises(AssertionError):
        check_residual_arithmetic(a, b, residual, bounds)
