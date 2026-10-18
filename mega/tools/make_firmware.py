"""from independent laboratory v7 The firmware generation Plasmic . The concentration field of

    .\\pc\\runtime\\python\\python.exe -B .\\mega\\tools\\make_firmware.py

read: pc\\vendor\\v7\\firmware\\Arduino\\Arduino.ino, hw_config.h (copied bit by bit v7 original code, will not be changed).
Write: mega\\source\\PlasmicMega\\PlasmicMega.ino, hw_config.h.

v7 remains intact; every modification below must be found ”exactly once” in the source code. If not found, stop.
so v7 is revised, this program will tell you which part is inconsistent. There are only four things to add:

  G / GP / GN   The weighing and rationing is determined by the board itself. (Algorithm Law Photo v7 backend of motors.dose_by_weight) .The computer only gives instructions,
                See the results; Computer crashes or USB The line is disconnected, but the board still stops by itself when the weight is reached, timeout occurs, the weight no longer increases, and the scale does not read.
  K             The computer stopped running continuously without an endpoint without sending any instructions for too long (V) .The power-on default is off.v7 Your own programs are not affected.
  CM / VL       Two pumps for cleaning (water inlet,OUT) and ”which pump is connected to which solenoid valve” can be set by the computer (p2 onwards). Only numbers are stored, Starting from
                won’t make anything move. After the board restarts, it returns to v7 settings (hw_config.h) ,Therefore, the program does not recognize these two instructions.
                 (v7 own, old version Plasmic) What it saw was the same board it thought it was.
  STATE and READY More fw=, wd=, dose= (p2 There are still clean=, links=) ,lets the computer know which version of the board is on the board and the current settings.

Each line of instructions is very short (less than 60 characters) : Mega of USB Serial port only 64 byte receiving buffer, the main loop is reading the scale
will not be closed in those twenty milliseconds, and a line that is too long will lose its tail (2026-10-07 ). Therefore, the weighing and quantitative figures are sent in three lines.

is generated Arduino The compiler compiles (mega\\tools\\build_firmware.py) .
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STOCK = ROOT / 'pc' / 'vendor' / 'v7' / 'firmware' / 'Arduino'
TARGET = ROOT / 'mega' / 'source' / 'PlasmicMega'
FW_TAG = 'p2'

HEADER = '''    X                         All stop (two sets of motors + syringe pump + solenoid valve + weight reading)
    ?                         Query the overall status
'''
HEADER_ADDED = '''
  ── Plasmic electronically controlled (independent laboratory v7 does not exist; the above instructions have not changed a word) Every instruction of──
    GP,<quicksps>,<slowsps>,<Refinedsps>,<minimum incrementmg>,<Let standms>,<No incrementms>,<timeoutms>
                              Weighing and quantification speed and time (send once after starting up)→ OK,dose_profile
    GN,<Base number of pens>,<Final number of transactions>,<Single transaction timeoutms>
                              Number of sampling pens (if not sent, just 5, 7, 4000) → OK,dose_samples
    G,<motor>,<Targetmg>,<deceleration zonemg>,<finishing areamg>,<allowedmg>
                              Weighing and quantification: Check the scale by yourself and stop when the amount is reached. → OK,dose_started
                              Actively report during the process DOSE,phase=…; at the end DOSE,end,result=…
    G,?                       Query weighing and quantification → GSTAT,active=…,phase=…,result=…,gain=…
    K,<ms>                    The computer did not send any instructions for more than this time and stopped continuous operation (V) ; 0 = Off (power-on default)
    CM,<water inlet motor>,<OUTmotor>    automatic cleaning (C) Which two should be used (one should be in 2~5, One in 6~9) → OK,clean=<Water ingress>:<OUT>
    VL,<v2>,<v3>,…,<v9>       motor 2~9 Which solenoid valve is linked to each (0 = No; one valve can only be used for one motor)→ OK,links=<8 numbers>
                              CM, VL only stores numbers and will not cause the pump or valve to operate; when starting up, hw_config.h settings
'''

DECLARATIONS = '''
// ---- Plasmic electronic control part (implemented below ”Plasmic ”Electronic control added part” section)----
#define FW_TAG "%s"
enum { GD_IDLE = 0, GD_BASELINE, GD_PRIMING, GD_COARSE, GD_APPROACH, GD_TRIM, GD_SETTLE, GD_FINAL, GD_TOPUP };
enum { GR_NONE = 0, GR_DONE, GR_CANCELLED, GR_TIMEOUT, GR_NO_GAIN, GR_SCALE };
bool doseActive();
void doseSample(float grams, bool stable);
void doseEnd(uint8_t result);
''' % FW_TAG

BLOCK = r'''// =====================================================================
//  Plasmic electronically controlled part (independent laboratory v7 is not available)
//
//  1. The weighing quantity is determined by the board itself (GP, GN, G) .algorithm photo v7 backend of motors.dose_by_weight: 
//     Take the base weight first (median)→ fast forward → Decrease speed when ”deceleration zone” remains → Fine addition when the ”finishing area” is left →
//     has reached the lower limit (target − allowable amount), stop the pump → Let stand → Get the final weight (median)→ If it’s not enough, use fine acceleration to make up for it..
//     will stop on its own: the amount is reached, the overall timeout occurs, the weight has not increased for too long, the scale has no new reading,X, V,<This one>,0.
//     The computer is only responsible for giving instructions and seeing the results: the computer crashes,USB If the line is disconnected, the above will still happen.
//  2. The computer stopped running continuously without an endpoint without sending any instructions for too long (K) .The power-on default is off.
//  3. Two pumps for automatic cleaning (CM) Linkage with pump and solenoid valve (VL) can be set by computer. When booting up, it is hw_config.h of
//      (v7 ) settings; these two instructions only store numbers, will not cause the pump or valve to operate, and will only close when nothing is moving.
//
//  Messages sent unsolicited shall not be OK/ERR/DONE (v7 ’s computer program will treat those three types as The firmware of
//  “Reply to the previous command”): Use Do it on DOSE, and WATCHDOG, .
//  Each line of instructions is missing 60 characters:USB The receive buffer of the serial port is only 64 byte,Main cycle reading scale
//  Those twenty milliseconds will not be collected, and a line that is too long will lose its tail.
// =====================================================================
bool scaleCalibrated = false;          // Received after power on W,DIV or W,CAL counts: the divisor default is 1,The reading at that time was not grams.

unsigned long wdMs = 0;                // 0 = closure
unsigned long wdLastRx = 0;            // The time when the last instruction was processed

// Setting of weighing quantity (GP, GN) .Please receive it first after turning on the machine GP,G will be accepted.
bool gpSet = false;
float gdSps[3] = {0, 0, 0};            // Fast rotation, speed reduction, fine processing
float gdMinGain = 0;
unsigned long gdSettleMs = 0, gdNoGainMs = 0, gdTimeoutMs = 0, gdSampleMs = 4000;
uint8_t gdBaseN = 5, gdFinalN = 7;

// This time the quantitative
uint8_t gdPhase = GD_IDLE;
uint8_t gdResult = GR_NONE;
int gdMotor = -1;
float gdTarget = 0, gdLower = 0, gdSlowZone = 0, gdTrimZone = 0;
float gdBaseline = 0, gdPeak = 0, gdProgress = 0, gdDispensed = 0, gdLastGain = 0;
float gdBuf[9];                        // benchmark/The final reading value; the increment of the last three transactions is being transmitted.
uint8_t gdBufN = 0;
uint8_t gdTopups = 0;
bool gdStable = true;
unsigned long gdStartedAt = 0, gdPumpStartedAt = 0, gdLastProgressAt = 0;
unsigned long gdLastSampleAt = 0, gdSettleUntil = 0, gdEndedAt = 0;

const char* dosePhaseName(uint8_t phase) {
  switch (phase) {
    case GD_BASELINE: return "baseline";
    case GD_PRIMING:  return "priming";
    case GD_COARSE:   return "coarse";
    case GD_APPROACH: return "approach";
    case GD_TRIM:     return "trim";
    case GD_SETTLE:   return "settle";
    case GD_FINAL:    return "final";
    case GD_TOPUP:    return "topup";
  }
  return "idle";
}

const char* doseResultName(uint8_t result) {
  switch (result) {
    case GR_DONE:      return "done";
    case GR_CANCELLED: return "cancelled";
    case GR_TIMEOUT:   return "timeout";
    case GR_NO_GAIN:   return "no_gain";
    case GR_SCALE:     return "scale";
  }
  return "none";
}

bool doseActive() { return gdPhase != GD_IDLE; }

bool dosePumping() {
  return gdPhase == GD_PRIMING || gdPhase == GD_COARSE || gdPhase == GD_APPROACH
         || gdPhase == GD_TRIM || gdPhase == GD_TOPUP;
}

// and Python of statistics.median Same: even-numbered pens are averaged from the middle two pens. Same ascount most 9.
float doseMedian(const float *values, uint8_t count) {
  float sorted[9];
  if (count == 0) return 0;
  if (count > 9) count = 9;
  for (uint8_t i = 0; i < count; ++i) {
    float v = values[i];
    int8_t j = (int8_t)i - 1;
    while (j >= 0 && sorted[j] > v) {
      sorted[j + 1] = sorted[j];
      --j;
    }
    sorted[j + 1] = v;
  }
  if (count & 1) return sorted[count / 2];
  return (sorted[count / 2 - 1] + sorted[count / 2]) / 2.0;
}

// Say something at each stage and attach STATE (The status of the motor has probably changed accordingly).
void dosePrintPhase() {
  Serial.print(F("DOSE,phase="));
  Serial.print(dosePhaseName(gdPhase));
  Serial.print(F(",gain="));
  Serial.println(gdLastGain, 3);
  printState();
}

void dosePrintStatus() {
  Serial.print(F("GSTAT,active="));
  Serial.print(doseActive() ? 1 : 0);
  Serial.print(F(",phase="));
  Serial.print(dosePhaseName(gdPhase));
  Serial.print(F(",result="));
  Serial.print(doseResultName(gdResult));
  Serial.print(F(",motor="));
  Serial.print(gdMotor);
  Serial.print(F(",target="));
  Serial.print(gdTarget, 3);
  Serial.print(F(",gain="));
  Serial.print((!doseActive() && gdResult == GR_DONE) ? gdDispensed : gdLastGain, 3);
  Serial.print(F(",baseline="));
  Serial.print(gdBaseline, 3);
  Serial.print(F(",topups="));
  Serial.print(gdTopups);
  Serial.print(F(",stable="));
  Serial.print(gdStable ? 1 : 0);
  Serial.print(F(",ms="));
  Serial.println((doseActive() ? millis() : gdEndedAt) - gdStartedAt);
}

// Start, or change the speed: only change the speed if it is already turning, do not restart and wait for the valve to open (and V instruction takes the same path).
void doseRun(float sps) {
  queueMotorStart(gdMotor, true, 0, sps);
}

void doseStopPump() {
  if (gdMotor >= FIRST_MOTOR && gdMotor <= LAST_MOTOR) stopGroupMotion(groupOf(gdMotor), true);
}

// End (regardless of the reason): Be sure to stop the pump first (the linkage valve will close automatically after delay), and then talk about the result. Not printed STATE.
void doseFinish(uint8_t result) {
  if (gdPhase == GD_IDLE) return;
  doseStopPump();
  gdResult = result;
  gdEndedAt = millis();
  gdPhase = GD_IDLE;
  Serial.print(F("DOSE,end,result="));
  Serial.print(doseResultName(result));
  Serial.print(F(",gain="));
  Serial.print(result == GR_DONE ? gdDispensed : gdLastGain, 3);
  Serial.print(F(",target="));
  Serial.print(gdTarget, 3);
  Serial.print(F(",topups="));
  Serial.print(gdTopups);
  Serial.print(F(",ms="));
  Serial.println(gdEndedAt - gdStartedAt);
}

void doseEnd(uint8_t result) {
  if (gdPhase == GD_IDLE) return;
  doseFinish(result);
  printState();
}

// reaches the lower limit: stop the pump, let it sit, and then take the final weight.
void doseSettle(unsigned long now) {
  doseStopPump();
  gdSettleUntil = now + gdSettleMs;
  gdPhase = GD_SETTLE;
  gdBufN = 0;
  dosePrintPhase();
}

// Every successful weight reading will go here (scaleReport) .
void doseSample(float grams, bool stable) {
  if (gdPhase == GD_IDLE) return;
  unsigned long now = millis();
  gdLastSampleAt = now;

  if (gdPhase == GD_BASELINE) {
    if (gdBufN < 9) gdBuf[gdBufN++] = grams;
    if (gdBufN >= gdBaseN) {
      gdBaseline = doseMedian(gdBuf, gdBufN);
      gdBufN = 0;
      gdLastGain = 0;
      // quantitative step instruction (P) will reduce the maximum speed of the drive, and subsequent continuous operation will be limited by it.
      drv[groupOf(gdMotor)].setMaxSpeed(MAX_SPS);
      doseRun(gdSps[0]);
      gdPumpStartedAt = millis();
      gdLastProgressAt = gdPumpStartedAt;
      gdLastSampleAt = gdPumpStartedAt;
      gdPhase = GD_PRIMING;
      dosePrintPhase();
    }
    return;
  }

  if (gdPhase == GD_SETTLE) return;              // The reading value while standing is not used.

  if (gdPhase == GD_FINAL) {
    if (!stable) gdStable = false;
    if (gdBufN < 9) gdBuf[gdBufN++] = grams;
    if (gdBufN >= gdFinalN) {
      gdDispensed = doseMedian(gdBuf, gdBufN) - gdBaseline;
      gdLastGain = gdDispensed;
      gdBufN = 0;
      if (gdDispensed >= gdLower) {
        doseEnd(GR_DONE);
        return;
      }
      if (now - gdPumpStartedAt >= gdTimeoutMs) {
        doseEnd(GR_TIMEOUT);
        return;
      }
      // After stopping the pump, the reading value falls below the lower limit: Use fine acceleration to compensate for the lower limit. For those spectra in
      if (gdTopups < 255) ++gdTopups;
      gdProgress = gdDispensed;
      doseRun(gdSps[2]);
      gdLastProgressAt = millis();
      gdLastSampleAt = gdLastProgressAt;
      gdPhase = GD_TOPUP;
      dosePrintPhase();
    }
    return;
  }

  float gain = grams - gdBaseline;

  if (gdPhase == GD_TOPUP) {
    gdLastGain = gain;
    // The pump has not started rotating at the second when the linkage valve is pre-opened: the reading value that jumps up at this time is not the liquid that has been added, and it will decide whether to stop or not.
    if (startPending[groupOf(gdMotor)]) return;
    if (gain >= gdLower) {
      doseSettle(now);
      return;
    }
    if (gain >= gdProgress + gdMinGain) {
      gdProgress = gain;
      gdLastProgressAt = now;
    }
    if (now - gdLastProgressAt >= gdNoGainMs) doseEnd(GR_NO_GAIN);
    return;
  }

  // PRIMING / COARSE / APPROACH / TRIM: is judged by the median of the last three increments.
  if (gdBufN < 3) {
    gdBuf[gdBufN++] = gain;
  } else {
    gdBuf[0] = gdBuf[1];
    gdBuf[1] = gdBuf[2];
    gdBuf[2] = gain;
  }
  float measured = doseMedian(gdBuf, gdBufN);
  gdLastGain = measured;
  if (measured > gdPeak) gdPeak = measured;
  if (gdPhase == GD_PRIMING && gdPeak >= gdMinGain) {
    gdPhase = GD_COARSE;                         // The liquid has arrived in the container
    dosePrintPhase();
  }
  if (measured >= gdProgress + gdMinGain) {
    gdProgress = measured;
    gdLastProgressAt = now;
  }
  if (measured >= gdLower) {
    doseSettle(now);
    return;
  }
  if (now - gdLastProgressAt >= gdNoGainMs) {
    doseEnd(GR_NO_GAIN);
    return;
  }
  float remaining = gdTarget - measured;
  if (remaining <= gdTrimZone && gdPhase != GD_TRIM) {
    doseRun(gdSps[2]);
    gdPhase = GD_TRIM;
    dosePrintPhase();
  } else if (remaining <= gdSlowZone && gdPhase == GD_COARSE) {
    doseRun(gdSps[1]);
    gdPhase = GD_APPROACH;
    dosePrintPhase();
  }
}

// loop() Every lap comes: timeout, end of rest, no new reading on the scale.
void serviceDose() {
  if (gdPhase == GD_IDLE) return;
  unsigned long now = millis();
  if (dosePumping() && now - gdPumpStartedAt >= gdTimeoutMs) {
    doseEnd(GR_TIMEOUT);
    return;
  }
  if (gdPhase == GD_SETTLE) {
    if (timeReached(now, gdSettleUntil)) {
      gdPhase = GD_FINAL;
      gdBufN = 0;
      gdStable = true;
      gdLastSampleAt = now;
    }
    return;
  }
  if (now - gdLastSampleAt >= gdSampleMs) doseEnd(GR_SCALE);
}

// Strict integer: only numbers (can be signed), up to 9 digits, so overflow will not wrap around.
bool doseLong(String text, long &value) {
  text.trim();
  unsigned int digits = text.length();
  if (digits > 0 && (text.charAt(0) == '-' || text.charAt(0) == '+')) --digits;
  if (digits < 1 || digits > 9) return false;
  return parseLongStrict(text, value);
}

// Comma separated integers. Return the number; one cell is not an integer or exceeds most will be sent back 0.
uint8_t doseParse(const String &text, long *values, uint8_t most) {
  uint8_t count = 0;
  int start = 0;
  int length = text.length();
  while (true) {
    int comma = text.indexOf(',', start);
    int end = comma < 0 ? length : comma;
    long value = 0;
    if (count >= most || !doseLong(text.substring(start, end), value)) return 0;
    values[count++] = value;
    if (comma < 0) return count;
    start = comma + 1;
  }
}

// GP, GN, G (are already capitalized).
void handleDoseCommand(String command) {
  int comma = command.indexOf(',');
  String key = command.substring(0, comma);
  String rest = command.substring(comma + 1);
  rest.trim();
  long v[7];

  if (key == "G" && rest == "?") {
    dosePrintStatus();
    return;
  }

  if (key == "GP") {                             // quick, slow, Refined sps; minimum increment mg; Quiet, no increment, timeout ms
    if (doseParse(rest, v, 7) != 7
        || v[2] < 1 || v[1] < v[2] || v[0] < v[1] || v[0] > (long)MAX_SPS   // 0 < finishing ≤ slow down ≤ fast forward ≤ upper limit
        || v[3] < 1 || v[3] > 100000L
        || v[4] < 0 || v[4] > 60000L
        || v[5] < 2000 || v[5] > 600000L                                      // It is necessary to pre-open the linkage valve 1 Second
        || v[6] < 2000 || v[6] > 1800000L) {
      Serial.println(F("ERR,arg"));
      return;
    }
    gdSps[0] = v[0];
    gdSps[1] = v[1];
    gdSps[2] = v[2];
    gdMinGain = v[3] / 1000.0;
    gdSettleMs = v[4];
    gdNoGainMs = v[5];
    gdTimeoutMs = v[6];
    gpSet = true;
    Serial.println(F("OK,dose_profile"));
    return;
  }

  if (key == "GN") {                             // Baseline number of pens, final number of pens, single pen timeout ms
    if (doseParse(rest, v, 3) != 3 || v[0] < 1 || v[0] > 9 || v[1] < 1 || v[1] > 9
        || v[2] < 500 || v[2] > 30000L) {
      Serial.println(F("ERR,arg"));
      return;
    }
    gdBaseN = (uint8_t)v[0];
    gdFinalN = (uint8_t)v[1];
    gdSampleMs = v[2];
    Serial.println(F("OK,dose_samples"));
    return;
  }

  // G,<motor>,<Targetmg>,<deceleration zonemg>,<finishing areamg>,<allowedmg>
  if (key != "G" || doseParse(rest, v, 5) != 5
      || v[0] < FIRST_MOTOR || v[0] > LAST_MOTOR
      || v[1] < 10 || v[1] > 1000000L                           // Target 0.01 g ~ 1000 g
      || v[3] < 0 || v[2] < v[3] || v[2] > 1000000L             // deceleration zone ≥ finishing area ≥ 0
      || v[4] < 0 || v[4] >= v[1]) {                            // The allowable amount is smaller than the target
    Serial.println(F("ERR,arg"));
    return;
  }
  if (!gpSet) {
    Serial.println(F("ERR,dose_profile"));
    return;
  }
  if (cleaningActive) {
    Serial.println(F("ERR,cleaning_active"));
    return;
  }
  for (int group = 0; group < 2; ++group) {
    if (startPending[group] || running[group] || finiteMoveActive[group]
        || drv[group].distanceToGo() != 0) {
      Serial.println(F("ERR,busy"));
      return;
    }
  }
  if (!scaleCalibrated) {
    Serial.println(F("ERR,scale_uncalibrated"));
    return;
  }
  float raw;
  if (!scaleReadRaw(raw)) {
    Serial.println(F("ERR,scale_timeout"));
    return;
  }

  gdMotor = (int)v[0];
  gdTarget = v[1] / 1000.0;
  gdSlowZone = v[2] / 1000.0;
  gdTrimZone = v[3] / 1000.0;
  gdLower = (v[1] - v[4]) / 1000.0;
  gdBufN = 0;
  gdTopups = 0;
  gdStable = true;
  gdBaseline = 0;
  gdPeak = 0;
  gdProgress = 0;
  gdDispensed = 0;
  gdLastGain = 0;
  gdResult = GR_NONE;
  gdStartedAt = millis();
  gdLastSampleAt = gdStartedAt;
  gdPumpStartedAt = gdStartedAt;
  if (wStreamHz < SCALE_STREAM_HZ) {             // Weighing and quantification relies on continuous readings
    wStreamHz = SCALE_STREAM_HZ;
    wLastStream = 0;
  }
  wFailCount = 0;
  gdPhase = GD_BASELINE;
  Serial.println(F("OK,dose_started"));
  printState();
}

// The commands that can be received when weighing and quantification are in progress: query, watchdog, read weight, and any kind of ”stop”.
bool doseAllows(const String &command) {
  String up = command;
  up.toUpperCase();
  if (up == "?" || up == "X" || up == "STOP" || up == "G,?" || up == "Y,STOP" || up.startsWith("K,")
      || up == "W,READ" || up == "W,CFG") {
    return true;
  }
  if (up.startsWith("V,")) {                     // speed 0=Stop: Stopping this one means canceling, stopping other ones has no action.
    int c2 = up.lastIndexOf(',');
    return c2 > 1 && up.substring(c2 + 1).toFloat() == 0;
  }
  return false;
}

// CM, VL (are already capitalized). Store numbers only: Will not activate any pumps or valves.
void handleSetupCommand(String command) {
  int comma = command.indexOf(',');
  String key = command.substring(0, comma);
  String rest = command.substring(comma + 1);
  rest.trim();
  long v[MOTOR_COUNT];

  if (cleaningActive) {
    Serial.println(F("ERR,cleaning_active"));
    return;
  }

  if (key == "CM") {                             // Water ingress,OUT: The two will rotate together during cleaning, so they need to be on different drives.
    if (doseParse(rest, v, 2) != 2
        || v[0] < FIRST_MOTOR || v[0] > LAST_MOTOR || v[1] < FIRST_MOTOR || v[1] > LAST_MOTOR
        || groupOf((int)v[0]) == groupOf((int)v[1])) {
      Serial.println(F("ERR,arg"));
      return;
    }
    cleanWaterMotor = (uint8_t)v[0];
    cleanOutMotor = (uint8_t)v[1];
    Serial.print(F("OK,clean="));
    Serial.print(cleanWaterMotor);
    Serial.print(':');
    Serial.println(cleanOutMotor);
    return;
  }

  // VL,<motor 2 valve>,…,<motor 9 valve>: 0 = No. One valve can only feed one motor (otherwise if one stops, the other’s valve will be turned off).
  uint8_t used[NUM_VALVES];
  for (uint8_t i = 0; i < NUM_VALVES; ++i) used[i] = 0;
  bool good = doseParse(rest, v, MOTOR_COUNT) == MOTOR_COUNT;
  for (uint8_t i = 0; good && i < MOTOR_COUNT; ++i) {
    if (v[i] < 0 || v[i] > NUM_VALVES) good = false;
    else if (v[i] > 0 && ++used[v[i] - 1] > 1) good = false;
  }
  if (!good) {
    Serial.println(F("ERR,arg"));
    return;
  }
  // If there is a pump running or waiting to be rotated, or a valve open or waiting to be closed: do not change it (after changing it, no one will close the valve).
  for (int group = 0; group < 2; ++group) {
    if (startPending[group] || running[group] || finiteMoveActive[group]
        || drv[group].distanceToGo() != 0) {
      Serial.println(F("ERR,busy"));
      return;
    }
  }
  for (uint8_t i = 0; i < NUM_VALVES; ++i) {
    if (valveOpen[i] || valveClosePending[i]) {
      Serial.println(F("ERR,busy"));
      return;
    }
  }
  for (uint8_t i = 0; i < MOTOR_COUNT; ++i) MOTOR_VALVE[i] = (uint8_t)v[i];
  Serial.print(F("OK,links="));
  for (uint8_t i = 0; i < MOTOR_COUNT; ++i) Serial.print(MOTOR_VALVE[i]);
  Serial.println();
}

// The computer has no sound for too long: stop the continuous operation without end (V) .Quantitative steps (P) , Cleaning, weighing and quantification are available
// Your own end point is not controlled by this place, nor will it be interrupted.
void serviceWatchdog() {
  if (wdMs == 0 || cleaningActive || doseActive()) return;
  if (millis() - wdLastRx < wdMs) return;
  bool stopped = false;
  for (int group = 0; group < 2; ++group) {
    if (running[group] || (startPending[group] && startContinuous[group])) {
      stopGroupMotion(group, true);
      stopped = true;
    }
  }
  if (!stopped) return;
  Serial.print(F("WATCHDOG,host_silent,ms="));
  Serial.println(wdMs);
  printState();
}

'''

SWAPS = [
    # the header comment
    (HEADER + '*/', HEADER + HEADER_ADDED + '*/'),
    # which valve opens with which motor, and the two pumps of the cleaning: what hw_config.h says at power-on,
    # changed by VL and CM
    ('const uint8_t MOTOR_VALVE[LAST_MOTOR - FIRST_MOTOR + 1] = MOTOR_VALVE_LIST;\n',
     '#define MOTOR_COUNT (LAST_MOTOR - FIRST_MOTOR + 1)\n'
     'uint8_t MOTOR_VALVE[MOTOR_COUNT] = MOTOR_VALVE_LIST;      // Plasmic: boot is v7 linkage; VL Instructions can be changed\n'
     'uint8_t cleanWaterMotor = CLEAN_WATER_MOTOR;              // Plasmic: boot is v7 two of; CM Instructions can be changed\n'
     'uint8_t cleanOutMotor = CLEAN_OUT_MOTOR;\n'),
    ('''  selectMotor(CLEAN_OUT_MOTOR);
  queueMotorStart(CLEAN_WATER_MOTOR, true, 0, CLEAN_WATER_SPS);

  int waterGroup = groupOf(CLEAN_WATER_MOTOR);
''', '''  selectMotor(cleanOutMotor);                    // Plasmic: Both are variables (CM) 
  queueMotorStart(cleanWaterMotor, true, 0, CLEAN_WATER_SPS);

  int waterGroup = groupOf(cleanWaterMotor);
'''),
    ('    queueMotorStart(CLEAN_OUT_MOTOR, true, 0, CLEAN_OUT_SPS);\n',
     '    queueMotorStart(cleanOutMotor, true, 0, CLEAN_OUT_SPS);\n'),
    ('    stopGroupMotion(groupOf(CLEAN_WATER_MOTOR), true);\n',
     '    stopGroupMotion(groupOf(cleanWaterMotor), true);\n'),
    ('    stopGroupMotion(groupOf(CLEAN_OUT_MOTOR), true);\n',
     '    stopGroupMotion(groupOf(cleanOutMotor), true);\n'),
    # declarations the scale code needs before the block itself
    ('void printState();\n', 'void printState();\n' + DECLARATIONS),
    # the scale stream stopped by itself during a dose: stop the pump at once
    ('''      wStreamHz = 0; wFailCount = 0;
      Serial.println(F("ERR,stream_stopped_no_reply"));
    }
    return;
''', '''      wStreamHz = 0; wFailCount = 0;
      Serial.println(F("ERR,stream_stopped_no_reply"));
      if (doseActive()) doseEnd(GR_SCALE);       // Plasmic: The scale does not respond, and the pump will stop immediately if the weighing quantity is determined.
    }
    return;
'''),
    # every good reading goes to the dose
    ('''  Serial.print(",ST,");    Serial.println(histStable() ? 1 : 0);
}
''', '''  bool stableNow = histStable();
  Serial.print(",ST,");    Serial.println(stableNow ? 1 : 0);
  doseSample(g, stableNow);                      // Plasmic: The weighing quantity follows each reading.
}
'''),
    # the block itself, before the scale's command handler
    ('void handleWeightCommand(String s) {                   // weight module\n',
     BLOCK + 'void handleWeightCommand(String s) {                   // weight module\n'),
    # the calibration counts from here on
    ('                              scaleDiv = d / g;\n',
     '                              scaleDiv = d / g; scaleCalibrated = true;\n'),
    ('                              scaleDiv = f; Serial.print("OK,div="); Serial.println(scaleDiv, 6); }',
     '                              scaleDiv = f; scaleCalibrated = true;\n'
     '                              Serial.print("OK,div="); Serial.println(scaleDiv, 6); }'),
    # STATE
    ('''  Serial.print(F(",cleaning="));
  Serial.print(cleaningActive ? 1 : 0);
  Serial.println();
''', '''  Serial.print(F(",cleaning="));
  Serial.print(cleaningActive ? 1 : 0);
  Serial.print(F(",fw=" FW_TAG ",wd="));           // Plasmic: firmware version, watchdog, weighing ration
  Serial.print(wdMs);
  Serial.print(F(",dose="));
  Serial.print(doseActive() ? 1 : 0);
  Serial.print(F(",clean="));                      // Plasmic p2: Cleaning water inlet:OUT; motor 2~9 Each linked valve (0 = No)
  Serial.print(cleanWaterMotor);
  Serial.print(':');
  Serial.print(cleanOutMotor);
  Serial.print(F(",links="));
  for (uint8_t i = 0; i < MOTOR_COUNT; ++i) Serial.print(MOTOR_VALVE[i]);
  Serial.println();
'''),
    # the command handler: what a running dose lets through, then G / GP / GN and K
    ('''  if (command.length() == 0) return;
  uppercaseKey(command);
''', '''  if (command.length() == 0) return;
  uppercaseKey(command);

  // ---- Plasmic ----
  if (doseActive()) {
    if (!doseAllows(command)) {
      Serial.println(F("ERR,dose_active"));
      return;
    }
    if (command.startsWith("Y,")) {              // can only be Y,STOP: Just send it out, don’t stop and wait for the syringe pump to reply.
      sendPumpStopNoWait();
      Serial.println(F("OK,pump_stop_sent"));
      return;
    }
  }
  if (command.startsWith("G,") || command.startsWith("GP,") || command.startsWith("GN,")) {
    handleDoseCommand(command);
    return;
  }
  if (command.startsWith("K,")) {
    long ms = 0;
    if (!doseLong(command.substring(2), ms) || (ms != 0 && (ms < 2000 || ms > 60000L))) {
      Serial.println(F("ERR,arg"));
      return;
    }
    wdMs = (unsigned long)ms;
    Serial.print(F("OK,watchdog_ms="));
    Serial.println(wdMs);
    return;
  }
  if (command.startsWith("CM,") || command.startsWith("VL,")) {
    handleSetupCommand(command);
    return;
  }
  // ---- v7 ----
'''),
    # V,<motor>,0 on the dosing motor cancels the dose
    ('''    int group = groupOf(motor);
    if (sps == 0) {
''', '''    int group = groupOf(motor);
    if (sps == 0) {
      if (doseActive() && motor == gdMotor) doseEnd(GR_CANCELLED);   // Plasmic: Stop this one=Cancel weighing and rationing
'''),
    # X cancels it too (the state is printed by nobody here: everything is switched off right after)
    ('''  if (command == "X" || command == "STOP") {
    cancelCleaningSchedule();
''', '''  if (command == "X" || command == "STOP") {
    doseFinish(GR_CANCELLED);                    // Plasmic (does nothing during dosing)
    cancelCleaningSchedule();
'''),
    # the banner
    ('  Serial.println(F("READY,arduino+pump+2valves+scale,proto=ascii,baud=9600,addr=0"));\n',
     '  Serial.println(F("READY,arduino+pump+2valves+scale,proto=ascii,baud=9600,addr=0,fw=" FW_TAG));\n'),
    # the main loop: the watchdog counts from the end of the last command (one that kept the board busy for
    # seconds must not make it fire the moment it returns)
    ('''    if (c == '\\n') {
      handleCommand(serialBuffer);
''', '''    if (c == '\\n') {
      handleCommand(serialBuffer);
      wdLastRx = millis();                       // Plasmic: The computer is still there
'''),
    ('''  serviceCleaning();
  serviceMotors();
}
''', '''  serviceCleaning();
  serviceMotors();
  serviceDose();                                 // Plasmic
  serviceWatchdog();                             // Plasmic
}
'''),
    ('  Arduino.ino - Arduino Mega 2560\n',
     '  PlasmicMega.ino - Arduino Mega 2560\n'
     '   (Depend on mega\\tools\\make_firmware.py from independent laboratory v7 of Arduino.ino generates: Do not change this file manually.\n'
     '    If you want to change, please change that program;v7 remain intact, and the added things are explained in the bottom paragraph) Everything in\n'),
]


def generate():
    """(the firmware's source as text, v7's source as bytes). Writes nothing."""
    source = (STOCK / 'Arduino.ino').read_bytes()
    assert b'\r' not in source, 'Arduino.ino should be LF'
    text = source.decode('utf-8')
    for old, new in SWAPS:
        count = text.count(old)
        if count != 1:
            raise SystemExit('does not match (found %d times, it should be exactly 1 times):\n%s' % (count, old[:200]))
        text = text.replace(old, new)
    return text, source


def main():
    text, source = generate()
    TARGET.mkdir(parents=True, exist_ok=True)
    (TARGET / 'PlasmicMega.ino').write_bytes(text.encode('utf-8'))
    (TARGET / 'hw_config.h').write_bytes((STOCK / 'hw_config.h').read_bytes())
    print('is written:', TARGET / 'PlasmicMega.ino', ' (%d OK; v7 original file %d OK) ' % (text.count('\n'), source.count(b'\n')))


if __name__ == '__main__':
    main()
