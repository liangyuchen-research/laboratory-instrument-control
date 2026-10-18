Plasmic electronic control - pump control panel (Arduino Mega 2560) firmware
====================================================

The current content on the board is:Plasmic p2
  burning time:2026-10-07 about 17:35 (COM3,serial number 0353637333235121B163) 
  Size:35,404 byte
  hex file SHA-256: c9863b173cae45dad0ee193454433002c08f2cae26f99f71bde3f749ea47ad07

  previous p1 (2026-10-07 03:48 burned,34,538 byte) stay backup\plasmic-mega-p1.hex
  SHA-256: 4b8c19962afadd99285d589b6ebc29aa68197c1c1c58703b2dbab0c7215a8930

It is an independent laboratory v7 , plus four things.v7 The original instruction has not changed a word.
independent laboratory v7 Your own programs can still use this board.

  1. The weighing quantity is determined by the board itself (command GP, GN, G) .
     When the weight is reached, the time is exceeded, the weight no longer increases, and the scale does not read: the board will stop the pump by itself.
     The computer crashed or USB The line is loose, it does not affect when it stops.
  2. The computer has not sent instructions for a long time, so it stops the continuous operation without an end point (commands K) .The power-on default is off.
  3.  (p2) The computer can tell the board which two pumps are used for cleaning and which solenoid valve each pump is connected to:
     CM,<water pump>,<OUT pump>           return OK,clean=2:8
     VL,<Solenoid valves for each of the eight pumps 0/1/2>   return OK,links=00000000
     These two instructions only store numbers and will not move anything. Every time the board is turned on, it returns v7 original connection method
      (water 2, OUT 8; solenoid valve 1 with pump 2, solenoid valve 2 with pump 9) ,The operating program will resend every time it is connected.
     “Maintenance › Pumps and Scales. When the settings have not been saved: both solenoid valves are not used.
  4. More status fw=p2, wd=, dose=, clean=, links= These fields let the computer know which version is on the board.
     Which connection method should I follow now?


What’s in this folder?
------------------
  Flash-Mega.cmd                    Double-click: Re-burn (required only when replacing the board). Close the operation screen first.
                                    To return v7 Original firmware: Execute in command window  Flash-Mega.cmd --stock
  hex\plasmic-mega.hex              The current firmware in the board (p2) 
  hex\v7-stock.hex                  independent laboratory v7 original firmware
                                     (and 2026-10-07 The content read from the board before burning is the same bit by bit)
  backup\board-flash-20261007.hex   The entire flash memory read from the board before burning for the first time
  backup\plasmic-mega-p1.hex        Previous edition p1
  source\PlasmicMega\               source code. by tools\make_firmware.py produce,Don’t change it by hand
  tools\make_firmware.py            from v7 generates the above source code (change the firmware here) The original firmware of
  tools\build_firmware.py           compilation (requires this computer to be installed Arduino IDE 2.x; does not download anything)
  tools\flash_mega.py               Burn and confirm. add --check Just ask which version the board is, don’t burn it


Need to change firmware
--------
  1. change tools\make_firmware.py,execute it
  2. execution tools\build_firmware.py
  3. double click Flash-Mega.cmd
  needs to be changed at the same time pc\megasim.py (board model for practice mode) and tests\test_megasim.py.


Where to verify (tell the truth)
--------------------
  2026-10-07 Confirmed on the real board:
  * p2 bit by bit comparison is the same after writing; boot message and status report fw=p2; The motors are stopped and the solenoid valves are closed;
    clean=2:8, links=10000002 (When booting, it is v7 original connection method).
  * 19:49 operating program connects to this board:CM,2,8 return OK,clean=2:8; VL,0,0,0,0,0,0,0,0 return
    OK,links=00000000 (Both solenoid valves are not used). The scale then resets to zero twice.
  * p1 (that afternoon): The maximum ”reference” flow rate ofK, GP, GN, G,? The answer is correct; the user has manually started and stopped At which address Pump 1-4.

  has not been done yet: use p2 allows the pump to rotate, weigh and quantify, the watchdog actually stops the pump, the solenoid valve is linked, and the water or OUT Change to another one
  . These have only been tested on computer models.
  Please follow the instructions for the first time.html”, only use water.24 V Keep the power-off switch handy.
