"""independent laboratory v5 rear end.

backend/
├── config.py      read config/hardware.yaml(The only source of truth)
├── transport.py   Serial port single owner After + state cache + event broadcast
├── simulator.py   Used when there is no hardware Arduino
├── registry.py    FaaS Registry
├── functions/     four function modules,independent of each other
└── main.py        FastAPI(very thin,No device knowledge When)
"""
__version__ = "5.0.0"
