Guide source files
==================

guide.src.html                   Everyday guide -> guide.html at the repository root.
                                 The Help button in the upper-right corner opens this guide.
installation-and-usage.src.html   Complete guide -> docs/detailed-guide.html: installation,
                                 wiring, detailed checks, troubleshooting, and references.
                                 {{IMG_...}} marks a screenshot; {{TEST_COUNT}} is the test count.
build_guide.py                   Builds both guides from the sources. It inserts neutral image
                                 placeholders until English screenshots are recaptured.
shots_app.py                     Runs the complete workflow in demo mode and captures each page.
split_guide.py                   Splits the complete guide into one page per section for
                                 layout inspection, with all details expanded.

Scope of the everyday guide
--------------------------
Keep routine information concise: getting started, safety, the first-use checklist, everyday
operation, troubleshooting, and verification status. Put other details in the complete guide
and link to them. Add information to the short guide when omitting it could cause a problem.

Editing the text
----------------
1. Edit the source files.
2. From the repository root, rebuild both guides:
       python -B docs/guide-source/build_guide.py
   Append the new test count when it changes.
3. Guides must not contain <script>. Help opens under a strict content security policy;
   build_guide.py checks this requirement.
4. Application messages refer to sections by name, such as "Step 3: Connect the network"
   and "Reference > Allowlist". Update those references when renaming headings.
   The builder checks that links to docs/detailed-guide.html#... have valid target IDs.
   The application opens the short guide at /guide; pc/httpd.py includes the complete
   guide route in GUIDES.
5. Button labels, messages, and alarms quoted in the guide must match the application.
   Update the guides whenever the interface changes.

Recapturing screenshots
-----------------------
Microsoft Edge is required. Run from the repository root (approximately three minutes):

    python -B docs/guide-source/shots_app.py

The URL includes ?fs=0 to suppress the tap-to-enter-fullscreen overlay.
The script launches --demo with simulated devices, never opens a serial port or connects to a
real RPi, and never reads or writes pc/data. It uses the same API requests as the interface:
select an operator, unlock maintenance, calibrate three concentrations, apply the calibration,
take measurements, run a custom schedule (Pump 1 and Pump 8 dose together, measure, then drain
with Pump 7), start a pump manually, stop, and calibrate one pump (dispense 10 mL, enter an
actual volume of 9.5 mL, then clear the calibration). Seven screenshots are captured.
The earlier screenshots were removed because they showed labels from the old
Traditional Chinese interface. The generated guides now use neutral image
placeholders until a complete set is recaptured from the English interface.

The script stops and explains any failed step, so it also checks the complete workflow.
Rebuild both guides after capture. It uses local ports 8794 and 8774; the ordinary application
does not need to be closed first.

Known limitations
-----------------
* Screenshots open pages by URL fragment. This capture script does not click interface buttons,
  so it does not capture maintenance tabs, calibration results/history, or operation events.
  Open demo mode in an interactive browser to capture those views.
* Demo status indicators identify the RPi and pump control board as simulated. When connected
  to hardware, they display the connection state and serial port instead.
* Historical 1.4.1 sources and screenshots were stored in
  docs/backup/1.4.1-before-2.0.0/guide-source/ in the original development environment.
