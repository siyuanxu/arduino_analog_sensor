import math
import pytest
from desktop_app.plot_ranges import aligned_zero_ranges


@pytest.mark.parametrize('force,displacement', [
    ([1000, 20000], [.1, 10]), ([-20000, -1000], [-10, -.1]),
    ([1000, 20000], [-10, -.1]), ([-3, 20], [-100, 2]),
    ([123], [7]), ([0, 0], [0]), ([1000], []), ([], [-2]),
    ([1e12, 2e12], [1e-6, 2e-6]), ([], []),
    ([None, math.nan, math.inf], [1, -2, -math.inf]),
])
def test_ranges_align_zero_and_include_all_finite_values(force, displacement):
    values = {'N': force, 'mm': displacement}
    ranges = aligned_zero_ranges(values)
    ratios = []
    for unit, (low, high) in ranges.items():
        assert math.isfinite(low) and math.isfinite(high) and low < 0 < high
        assert all(low <= value <= high for value in values[unit]
                   if value is not None and math.isfinite(value))
        ratios.append(-low / (high - low))
    assert ratios[0] == pytest.approx(ratios[1], abs=1e-12)


def test_empty_or_flat_zero_axis_does_not_force_positive_data_to_center():
    positive = aligned_zero_ranges({'N': [100, 1000], 'mm': []})
    zero = aligned_zero_ranges({'N': [100, 1000], 'mm': [0]})
    assert positive == zero
    low, high = positive['N']
    assert -low / (high - low) < .15


def test_unrepresentable_span_is_explicit():
    with pytest.raises(ValueError, match='跨度过大'):
        aligned_zero_ranges({'N': [-1e308, 1e308], 'mm': []})
