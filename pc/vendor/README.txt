pc\vendor: Programs written by others are placed here intact (2026-10-06 clone) 
================================================================

The files in this folder are ”bit-for-bit copied”. Don’t change anything here:
The functions to be supplemented and the behaviors to be blocked are all written in pc\pumpboard.py (2.0.0 Pump control board packaging layer) ,Not to change here.
The hash of each file is recorded in MANIFEST.sha256; Check whether there is any passive:

    .\pc\runtime\python\python.exe -B .\docs\tools\check_v7_sync.py

v7\        Arduino Mega Pump control board program from C:\Users\PlasmaLab\Desktop\independent laboratoryv7
            (Its own version string is backend.__version__ = "5.0.0",The folder name is v7) 
  backend\__init__.py, config.py, registry.py, transport.py, simulator.py
  backend\functions\__init__.py, motors.py, pump.py, scale.py
           will be used when executing. no copy backend\main.py (that is FastAPI web server, not used here). The pulse of
  config\hardware.yaml            Settings for pumps, valves, syringe pumps, and scales (V7 The only source of settings for) .read only,Do not rewrite.
  config\scale_calibration.json   Calibration value of the scale (copy the copy at that time; when executing, use pc\data\ copy below)
  firmware\Arduino\, tools\       Firmware source code and generation hw_config.h ’s tool: only for comparison, will not execute or burn.

site\      V7 of .venv , only pure The screenshots in Python (nothing downloaded)
  serial\  pyserial 3.5 (28 indivual .py) 
  yaml\    PyYAML 6.0.2 (17 indivual .py; not copied _yaml of .pyd,That’s for another version Python compiled,
           Missing it is just yaml.__with_libyaml__ for False,has the same function)

licenses\  The above two packages are in V7 of .venv License attached/Help file
  PyYAML-6.0.2\LICENSE (MIT) , METADATA
  pyserial-3.5\METADATA, DESCRIPTION.rst
           Note:pyserial of dist-info (only the two above,METADATA Write
           “License: BSD”,source code has each file header SPDX-License-Identifier: BSD-3-Clause) .
           There is no authorization clause to be filled in from memory; if you want to fill in the full text, you need to download it, so please ask the user first. The pump control board will restart when

font (put in pc\web\fonts\,Also remember MANIFEST.sha256) 
           Barlow 400/500/700, Barlow Condensed 400/600,from the original HMI of
           UI\UI\hmi_v1_1\web\vendor\ (The file name is written there fonts.css) ; Authorization is SIL OFL 1.1,
           Two authorization documents Barlow-OFL.txt, BarlowCondensed-OFL.txt put together pc\web\fonts\.

V7 Keep up after the revision
-----------------
1. Confirm first V7 The firmware and hardware.yaml is the version requested by the user.
2. Copy the files listed above again (binary copy, do not use tools that change lines).
3. .\pc\runtime\python\python.exe -B .\docs\tools\check_v7_sync.py --write-manifest
4. run tests\test_vendor.py and pump control board testing.
I usually want to know here and V7 folder: Execute in
    .\pc\runtime\python\python.exe -B .\docs\tools\check_v7_sync.py --v7
