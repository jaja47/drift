"""Write targets. AdpTarget edits state/adp_current.csv, a copy — never the source export."""

from drift.target.adp_target import AdpTarget, TargetError

__all__ = ["AdpTarget", "TargetError"]
