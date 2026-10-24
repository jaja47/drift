"""Business constants shared by the data generator and Drift."""

from datetime import date

PAYROLL_CUTOFF = date(2026, 10, 9)
"""The last payroll cutoff before the exports were taken. A change dated after it could not have
reached that pay run yet."""
