# 2.0.0 integrated design (electronic control＋Arduino Mega pump control＋original UI Correction for＋Correction/trend) 

This folder is 2026-10-06 .**2026-10-06 I started writing the program according to it in the afternoon; see where I write it. `BUILD-STATUS.md`.**
after review-workspace The conversation needs to continue 2.0.0,Read here first, then read `design\AMENDMENTS.md` and `BUILD-STATUS.md`.

User request (2026-10-06,original text):

> It should be enough to take a picture of the background spectrum. Help me update it. There is no need to add ”Quick”. The process must be unified and there should be no redundant buttons. And this AfterUIshould be withpumpcontrol is integrated together,
> refer to the originalUI(on desktop)and unifying it all, the original ForUIis designed forPLCof,Now I switch to usingArduino megacontrol,
> Therefore, it is necessary to use independent laboratoryv7insidecodeshall prevail, butUIThe style should be similar to the original,And to be consistent with the originalUIcan also be setUIoperation password

On the afternoon of the same day, he answered the six questions left by the design (original text):

> 1. Turn on the high voltage before taking the background photo but do not discharge it(Only take one picture of the background, refer to the originalUIdata processing)
> 2. has repairs PINThat’s it, let’s set it now
> 3. There should be a simple step-by-step editor on the screen, no need for an additional ”measure once” button The action of
> 4. should also have the original UI ’s ”Calibration” and ”Trend” (concentration calculation) The spectral records of
> 5. The side of the pump24Vhas an emergency power-off switch.Megabeing pawned is very low. Should I transfer the amount directly to The source code ofmega(Whether it is fixed speed or fixed weight),letmegaDetermine when to stop, now the computer is also connectedmegacan directly inject the modified software
> 6. 2.0.0What is left that has not been completed yet? What stage are we at now?

then said: ”2.0.0All programs need to be completed.”

No. 1 point has been made 1.4.1 (RPi measurement process: first turn on the high voltage without discharging, and when the voltage reaches 1 background, then discharge). The rest is written in `design\AMENDMENTS.md`.
**No. 5 the second half of the point (change Mega allows ”fixed weight” also by Mega Stop by yourself) haven’t gotten his clear “want” yet**: I asked ”Do you want me to change Mega ? ”, his next sentence is
“2.0.0All programs need to be completed.” There was no direct answer. So 2.0.0 Use the current firmware to do: if there is time or amount of pump action, leave it to Mega Stop by yourself (AMENDMENTS M8) ,
Only ”weighing quantity” still applies V7 Check the scale by computer. The file of**I have to ask him again if I want to change the firmware.**

| folder/file | content |
|---|---|
| `design\constraints.md` | Fixed decision and confirmed facts (read this first) |
| `design\SPEC.md` | full specifications:Recipe Language, computer program structure, packaging layer of pump control board, interlocks, data, images and text on each page, test plan, risk |
| `design\API.md` | HTTP interface with `/api/state` (convention shared by front-end, back-end and test) The small light of |
| `design\WORKPACKAGES.md` | Eight work packages: which files each has (non-overlapping), sequence, and description of each package |
| **`design\AMENDMENTS.md`** | **2026-10-06 The revision in the afternoon, any difference from the above three revisions shall prevail.**: Measurement sequence (M1) , Remove the maintenance discharge (M3) , Recipe step by step editor (M4) , Correction/Trend/concentration (M5) , Changes and new additions to the work package WP9, WP10 (M6) , Temporary file name used when covering (M7) , The limited time pump operation is changed to the quantitative command that will stop automatically (M8) , Do not let the computer sleep during work (M9)  |
| **`BUILD-STATUS.md`** | **Where is the program written?**: The status of each work package, who owns which files, and what has not been done yet |
| `design\proposal-*.md` | Three plans before finalization (for future reference) |
| `maps\*.md` | Code reading records of three sets of programs: V7 (firmware/rear end/front end/integration points), original HMI (Server and PIN/process control/Analysis/Picture structure/Picture style/architecture summary), the electronic control at that time |
| `..\guide-source\shots\` | Current English interface screenshots captured in demo mode |
| `review-2026-10-06\` | Measurement program review results (53 item) and the principle of correction; 1.4.0 It has been repaired according to it |

Things to know: Which machine should

1. **When covering,1.4.1 Always be able to use it** (The shortcut on the user’s desktop is to open this folder). So 2.0.0 is temporarily called The time format of `pc\app2.py`, The new screen is temporarily placed on `pc\web2\`, 
   The transplanted test is temporarily called `tests\test_app2.py`; `pc\app.py`, `pc\web\index.html`, `app.js`, `style.css`, `tests\test_app.py` yes 1.4.1 , don’t move until everything is done.
   was finally changed by one person at a time (AMENDMENTS M7; steps in `BUILD-STATUS.md` of“Integrate”) .
2. Specification“Find loopholes”that round (three reviewers) At that time, it was not used up due to the usage limit..The current approach is: After each work package is written,,It’s being faulted by people who weren’t involved. (Especially the pump and high pressure interlock, stop) ,Repair again.
3. communication protocol is 2; `pc\app.py` has no ”sequence”;RPi of `hello` have `flow` and `flow_error`.
4. Historical source references under `scratchpad\understand` map to this folder's `maps\`; current English interface references are under `..\guide-source\shots\`.

Things you should never do (and `historical-development-notes.md` are the same, so write them again here): Write down the message

- Do not execute the original HMI,Do not use the included Python (`UI\UI\hmi_v1_1\offline_bundle\python-runtime`) runs anything: its module path is hard-coded to point to the original folder and will read and write the original `data\`.Do not open or list original HMI of `data\` folder.
- Arduino Mega is connected to this computer COM3.Used for both development and testing V7 Your own simulator, do not open it COM3 (will reset the board when turned on). Real pumps, syringe pumps, and valves must be activated by the user themselves on the screen.
- working time setting `PLASMIC_NO_SERIAL=1`; Don’t execute `Start.cmd`, Don’t add it `--demo` execution `pc\app.py` or `pc\app2.py`.
