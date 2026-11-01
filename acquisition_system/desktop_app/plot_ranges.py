"""Independent physical scales sharing one vertical position for zero."""
import math


def aligned_zero_ranges(axis_values, minimum_padding=.001):
    """Include visible data and zero on every axis, with one shared zero ratio.

    Only dimensionless fractions are combined across axes (N and mm cannot be
    added). Empty/flat-zero axes follow the others without moving their zero.
    """
    extents = {}
    fractions = []
    for unit, values in axis_values.items():
        finite = [float(value) for value in values if value is not None and math.isfinite(value)]
        negative = max(0.0, -min(finite, default=0.0))
        positive = max(0.0, max(finite, default=0.0))
        magnitude = max(negative, positive)
        # Normalize before adding extents, avoiding overflow from a + b.
        scaled_span = negative / magnitude + positive / magnitude if magnitude else 0.0
        padding = max(magnitude * (.08 * scaled_span), minimum_padding)
        lower, upper = negative + padding, positive + padding
        if not math.isfinite(lower) or not math.isfinite(upper):
            raise ValueError('数值过大，无法建立有限的绘图范围')
        extents[unit] = lower, upper
        if magnitude:
            scale = max(lower, upper)
            a, b = lower / scale, upper / scale
            fractions.append((a / (a + b), b / (a + b)))
    if fractions:
        below = max(fraction[0] for fraction in fractions)
        above = max(fraction[1] for fraction in fractions)
        zero_fraction = max(.05, min(.95, below / (below + above)))
    else:
        zero_fraction = .5
    result = {}
    for unit, (below, above) in extents.items():
        span = max(below / zero_fraction, above / (1 - zero_fraction))
        low, high = -zero_fraction * span, (1 - zero_fraction) * span
        if not math.isfinite(span) or not math.isfinite(high - low) or high <= low:
            raise ValueError('数值跨度过大，无法建立有限的绘图范围')
        result[unit] = low, high
    return result
