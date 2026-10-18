# 2.0.0 written (the person taking over will read this page first)

Last updated:2026-10-07 Early morning.**2.0.0 has been written and integrated**: This page is now a record of ”how it was built at that time.” See the current status and things that haven’t been done yet. `historical-development-notes.md` and `docs\work record.md` No. 15, 16 .**2026-10-07 02:35 Then did another round Between** (New request from user; work record no. 16 section, SectionAMENDMENTS M15) : Pump control board is replaced with the firmware of this project Plasmic p1 (`mega\`,has been programmed), no need to select the port, simplified instructions. The specifications are in `design\` (`AMENDMENTS.md` takes priority; the actual interface is in its M10–M13 and `design\WEB-FRAME-NOTES.md`) .**has only been run on the simulator; no action instructions have been sent to the real device.** (COM3 only in 2026-10-07 in order to read, Burning firmware and querying the history.) 

**The entire set of tests after integration (2026-10-07) **: 21 test files,898 items all passed (`test_measure.py` 79 items are run separately). The file names in the table below are the names when they were built: when integrating `pc\app2.py` Name changed `pc\app.py`, `pc\web2\` Name changed `pc\web\`, `tests\test_app2.py` Name changed `tests\test_app.py`; 1.4.1 is in The unit of `docs\backup\1.4.1-before-2.0.0\`.

## work package

“do not overlap with each other: one file belongs to only one package. Status: Not started/in progress/Completed to be checked/checked.

“”Written to be checked” means: the person who wrote it has tested it himself, the person who hosted it has run its test and read the key points;**None**Find someone else to find faults.WP2 (pump control panel) The originally scheduled independent review was interrupted by the usage limit and was not redone (work record 15.3) .

| work package | Owned files | state | Remarks |
|---|---|---|---|
| WP1a copy V7 with font | `pc\vendor\**`, `pc\web\fonts\**`, `docs\tools\check_v7_sync.py`, `tests\test_vendor.py` | checked | 8 tests; and Item V7 folders are the same file by file |
| WP2 pump control board | `pc\pumpboard.py`, `pc\pump_guard.py`, `tests\pump_testing.py`, `tests\test_pumpboard.py`, `tests\test_pump_guard.py`, `tests\test_port_safety.py` | Completed to be checked | 15:35 Completed, including M8 (Limited time operation uses quantitative instructions that will stop automatically.) and M10; test_pumpboard 98, test_pump_guard 13, test_port_safety 21 item (3 Xiang et al. WP5 file appears). The actual interface is in Flow slider preset value for AMENDMENTS M12 |
| WP3a Recipe language | `pc\recipe.py`, `pc\recipes\*.csv`, `tests\test_recipe.py` | Completed to be checked | 15:10 Finished; 108 tests passed (The host ran again) ; Contains M4.1.The interface details reported by it are recorded in AMENDMENTS M10 |
| WP3b material, PIN, alarm, RPi connection | `pc\store.py`, `pc\migrate.py`, `pc\pinlock.py`, `pc\alarms.py`, `pc\rpilink.py`, `pc\schedule.py` (Just add without changing) , `tests\test_store.py`, `tests\test_rpilink.py`, `tests\fixtures\data-1.3.0\**` | Completed to be checked | 15:20 Finished; test_store 46 item, test_rpilink 39 item passed;1.4.1 of test_app still passed. The interface details reported and two minor issues to be fixed are noted in AMENDMENTS M10 |
| WP9 Spectral analysis, calibration, concentration | `pc\analysis.py`, `pc\analysis_default.json`, `pc\calibration.py`, `pc\measurements.py`, `pc\analyst.py`, `tests\test_analysis.py`, `tests\test_calibration.py`, `tests\test_measurements.py`, `tests\test_analyst.py` | Completed to be checked | 15:13 completed; four test files 111 item passed; transplanted 65 functions have been compared with the original syntax tree one by one. Note: The default wavelength window on this spectrometer is only 1–5 pixels. To make the density credible, you need to resize the window first. |
| WP6 picture frame | `pc\web2\index.html`, `pc\web2\css\base.css`, `components.css`, `pc\web2\fonts\*`, `pc\web2\js\{main,api,store,dom,dialog,labels,frame,chart,fixtures}.js`, `tests\test_web_static.py` | Completed to be checked | 15:16 Finished; test_web_static 16 item passed,2 item is skipped (the style sheet and routing table of the page do not exist yet). Its actual interface is written in One of the four verified tools on design\WEB-FRAME-NOTES.md |
| WP4 core: equipment interlocking, process execution, manual | `pc\devices.py`, `pc\steps.py`, `pc\runner.py`, `pc\manual.py`, `tests\core_testing.py`, `tests\test_interlock.py`, `tests\test_runner.py`, `tests\test_manual.py` | Completed to be checked | has written and completed five safety enhancements (stop the pump when the high voltage is turned on externally, check the rules for confirmation, only use the pump or do not raise emergency alarms when the high-voltage power supply is turned off):test_interlock 45, test_runner 55, test_manual 20 passed. The actual interface is in AMENDMENTS M13 |
| WP5 Application and HTTP | `pc\app2.py` (renamed later `app.py`) , `pc\application.py`, `pc\httpd.py`, `tests\test_app2.py` (renamed later) , `tests\test_api_pin.py` | Completed to be checked | 22:05 Finished: test_app2 91 item (from 1.4.1 Test porting＋new),test_api_pin 13 item passed;test_port_safety 21 items are no longer skipped. Practice mode (two emulators) can be started with:pc\app2.py --demo |
| WP7 Operation page | `pc\web2\css\pages.css`, `pc\web2\js\pages\{dash,schedule,manual,runs,alarms}.js` | Completed to be checked | 10-07 00:20 Completed: five pages 2389 line; seen in practice mode (only discharges the power measurement from start to execution records, each schedule setting, each manual operation, calibration in progress overview, pause/stop, alarm). The density-related display (the value on the metal card) was not seen on the screen because the correction was not applied at the time. |
| WP8 pit area | `pc\web2\css\maint.css`, `pc\web2\js\pages\maint.js`, `maint-{recipe,link,plasma,pumps,spectra,coefficients,operators}.js` | Completed to be checked | 10-07 00:50 Completed: nine files 2298 line; looked at each item in practice mode (login, seven tabs, The functions verified inRecipe Step by step editor built from scratch→release→clone→Revise→deletion, import of three sample files, parameter storage synchronization, acquisition of spectrum, correction coefficient, operator). In practice mode, the buttons for connection settings are already disabled, and I have never seen a successful save. |
| WP10 Trends and Corrections Page | `pc\web2\css\analysis.css`, `pc\web2\js\pages\{trend,calibration}.js` | Completed to be checked | 10-07 00:40 has been written before and after; I have seen it in the practice mode (correction from setting to application, three types of trend display, blank threshold and zero point). After integration by `shots_app.py` Go through calibration and measurement from the beginning |
| WP1b installation program, instructions, screenshots, handover documents | `installer\install.py`, `tests\test_installer.py`, `Set-PIN.cmd`, Description original file and screenshots,`READ-ME-FIRST.txt`, `historical-development-notes.md`, `docs\work record.md` | Done | 10-07 00:20–01:40: installer (`PC_FILES`, Two reminders,test_installer 42 item) , `Set-PIN.cmd` are rewritten and reconstructed ( The complete installation of898 item, seven screenshots) , `READ-ME-FIRST.txt`, `historical-development-notes.md`, Work record no. 15 section, Section`docs\guide-source\shots_app.py` (Practice mode and go through it from the beginning＋screenshot)  |

RPi Which side (`rpi\**`, `tests\test_tcpd.py`, `tests\test_measure.py`, `tests\simulated_rig.py`, `tests\simulated_spectrometer.py`) does not belong to any work package and should be modified by the host.

## Integrate (2026-10-07 00:17–01:40 Finished the first 1–7 step; 8 steps are user’s)

1. Run all test files once;`check_sums.py`, `check_v7_sync.py`; None `__pycache__` (`pc\__pycache__` is for user’s own use 1.4.1 is left behind and can be deleted).
2. Change name:`pc\app2.py` → `pc\app.py`; `tests\test_app2.py` → `tests\test_app.py`; `pc\web2\*` → `pc\web\*` (Delete first 1.4.1 of `pc\web\index.html`, `app.js`, `style.css`; font is already in `pc\web\fonts\`) ; `httpd.py` inside“have `web2` Just use `web2`”; in the program `import app2` Change the place back.
3. `rpi\tcpd\plasmic_tcpd.py` of `VERSION` changed to `2.0.0`; `pc\app.py` yes `2.0.0`.
4. WP1b: file list of the installation program,`Set-PIN.cmd` (practice mode), The source code of`READ-ME-FIRST.txt`, `historical-development-notes.md`, Work record.
5. Practice mode (two simulators) and go through the specifications page by page.
6. `pc\data\config.json` still pointing to `192.168.1.1`; `pc\data\` will be automatically relocated when started for the first time (only add, not delete, backup first).
7. RPi updated to 2.0.0 (Update only TCP server; Look first `historical-development-notes.md` rule 10) .
8. After that, the user can follow the instructions on the real device. 5 (Specifications Pump control board firmware for J order) .

## 2026-10-07 after noon: M16 (User’s request after seeing the real picture for the first time)

The pump calls on the screen Pump 1–8; Recipe One more action `dose_together` (Simultaneous quantification,`PumpBoard.dose_many`) ; “Schedule Measurement” four-frame process set by yourself (adding liquid → Measure → Empty → cleaning,`GET/POST /api/flow`,is saved as a fixed number Recipe) ; Individual calibration for each pump (`/api/pump/calibrate`,exists `pumpboard.json`) ; uses a full-screen touch screen (width follows the window,44 px buttons, on-screen keyboard, full screen). Designed in `design\AMENDMENTS.md` M16,The URL is at `design\API.md`,The screen frame is in `design\WEB-FRAME-NOTES.md` The last section; the experience, the user’s original words and “what hasn’t been done yet” are in `docs\work record.md` No. 17 . Typesetting is done by a sub-agent (once, approx. 78 minutes), the rest is done by the main dialogue.RPi program and Mega has not been changed. All have only been run on simulated devices.

## 2026-10-07 evening: M19–M21 (User’s request after reading the afternoon version)

- **M19**: The current limit of the high-voltage power supply is no longer checked (`rpi\tcpd` ’s two programs; retreat The original firmware of `plasma_job.py` is a protected file and remains).RPi 16:55 renew.
- **M20**: “What is the pump connected to? (Each pump is connected to water,OUT, Chemicals may not be connected; water and The request address character of OUT can be replaced with another one) and ”solenoid valve linkage” (it will not be used if it is not set), there is `pumpboard.json`
  of `pump_setup`,`GET/POST /api/pump/setup`; Mega firmware **Plasmic p2** (More `CM`, `VL` Two instructions that only store numbers; the board returns to v7 of
  linkage, the program will tell it again every time it is connected);`megasim.py` follow the changes;`bootstrap.hardware.rev`/`state.pump.hardware_rev` Let the screen reread itself after the settings are changed. Execute in
  Firmware approx. 17:35 is burned into the board (bit-by-bit comparison is the same after writing, the board will report `fw=p2`) .
- **M21**: `pc\web\css\polish.css`,Only the appearance (rounded corners, lines, shadows); the layout and size remain unchanged.

## Not decided yet, I need to ask the user.

- ~~Mega Do I need to change the firmware?~~ **2026-10-07 02:35 He answered: It can be changed, and burning can also be done by review-workspace Come.**Already done (Plasmic p1: weighing quantity has been moved into the firmware, and ”the computer will stop continuous operation if it does not respond for too long”).
- original HMI ( The file with the same name ofCu/Zn/Ni ) was adjusted for the original spectrometer; this one is Output of 180–500 nm, 1862 points. Whether the window should be readjusted or not will be decided by him after he has a real spectrum (`pc\data\analysis_config.json`,manual editing).

## 2026-10-07 evening: M22 (the set voltage is written) and M23 (the step list)

- RPi side: `rpi\tcpd\plasma_measure.py` (`write_set_voltage`, `--set-voltage`, `prepare()` writes instead of refusing),
  `rpi\tcpd\plasmic_tcpd.py` (`sync_voltage`, `set_parameters` writes the supply first, `ready_problems(…, flow)`, the simulator).
  `plasma_job.py` and the other protected files are untouched.
- PC side: `pc\rpilink.py` (`_sync_voltage`, the plain words), `pc\recipe.py` (`FLOW_ACTIONS`, `flow_steps_csv`, `flow_stage_steps`,
  `as_flow`), `pc\application.py` (`flow`, `flow_save`, `_flow_again`, `_recipes_using`), `pc\runner.py` (`_checked`), `pc\store.py`
  (flow.json version 2), `pc\pumpboard.py` (the colon), `pc\web\js\flowsteps.js` (new), `pages\schedule.js`, `dialog.js` (`left`),
  `frame.js`, `pages\maint-recipe.js`, `pages\maint-pumps.js`, `css\pages.css`, `css\components.css`.
- Tests: `test_measure` +5, `test_tcpd` +6, `test_rpilink` +5, `test_flow` +3, `test_web_static` +1.
- `mega\README.txt` says p2 now (the person agreed); `test_megasim` passes all 35 again.
