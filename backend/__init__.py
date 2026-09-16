"""Laboratory control backend.

Modules:
    config.py       Loads the shared hardware configuration.
    transport.py    Owns the serial connection, state cache, and event stream.
    simulator.py    Implements the Arduino protocol without physical hardware.
    registry.py     Registers callable device operations and parameter schemas.
    functions/      Implements peristaltic pump, syringe pump, and scale operations.
    main.py         Exposes the HTTP API and browser interface.
"""
__version__ = "6.0.0"
