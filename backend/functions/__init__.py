"""Import device modules to register their operations.

To add a device, create a Python module in this directory, register its
operations with @function, and include the module in the import list below.
"""
from . import motors, pump, scale  # noqa: F401

MODULES = (motors, pump, scale)
