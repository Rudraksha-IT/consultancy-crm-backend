from __future__ import annotations


class DuplicateFeeTypeError(Exception):
    """Another Fee Type already has this name (in any case, spaces tidied)."""


class TooManyFeeTypesError(Exception):
    """The Consultancy already has the most Fee Types allowed."""


class FeeTypeInUseError(Exception):
    """A Fee or Payment uses this Fee Type, so it can only be hidden, never deleted."""


class OtherFeeTypeIsFixedError(Exception):
    """The Fee Type "Other" is always there: it can't be renamed, hidden or deleted."""


class FeeTypeOrderError(Exception):
    """The new order doesn't name every Fee Type exactly once."""
