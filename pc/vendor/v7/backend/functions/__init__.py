"""Load all function modules —— import Entering means registration is complete.

To add a new device(For example, future plasma spectra),Just put a new one in this folder .py,
is used in @function register,Then add the module name to the line below. No other files need to be touched.
"""
from . import motors, pump, scale  # noqa: F401

MODULES = (motors, pump, scale)
