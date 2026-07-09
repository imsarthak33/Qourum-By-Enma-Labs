"""Quant Core — the deterministic Decision Layer (07_QUANT_CORE).

Pure math, zero token cost, no provider dependency. LLMs never appear in this
package.
"""

from . import devils_advocate, fundamentalist, macro, risk, technician
from .calibration import CalibrationCurve, fit_isotonic
from .chairman import log_opinion_pool, synthesize, validate_levels
from .weights import hedge_update, log_loss, renormalise, uniform_weights

__all__ = [
    "technician",
    "fundamentalist",
    "macro",
    "devils_advocate",
    "risk",
    "CalibrationCurve",
    "fit_isotonic",
    "synthesize",
    "log_opinion_pool",
    "validate_levels",
    "hedge_update",
    "log_loss",
    "renormalise",
    "uniform_weights",
]
