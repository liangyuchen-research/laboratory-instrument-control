/*
  Arduino.ino - Arduino Mega 2560

  Pin assignments and conversion factors are defined in config/hardware.yaml.
  Run python tools/gen_firmware_config.py after changing the configuration,
  then compile and upload this sketch together with the generated hw_config.h.

  Stepper drivers and relay selection:
    Group A, motors 2-5: PUL=D22, DIR=D23, ENA=D24, relays=D2-D5
    Group B, motors 6-9: PUL=D25, DIR=D26, ENA=D27, relays=D6-D9

  Syringe pump (fixed protocol settings):
    Serial1: TX1=D18, RX1=D19
    MAX13487 automatic direction control; no DE/RE control pin
    ASCII, 9600 baud, address 0, default syringe capacity 5000 uL

  Normally closed solenoid valves, via ULN2803A and LB2HN-24DS:
    Valve 1=D10, valve 2=D11

  RS485 weighing transmitter, Modbus RTU:
    Serial2: TX2=D16, RX2=D17
    Tie DE and RE to D36; automatic-direction adapters may omit this pin.
    Load cell: red=E+, black=E-, green=S+, white=S-. Supply: 12-24 V.

  USB serial commands (115200 baud):
    S,<motor>                 Select motor 2-9
    V,<motor>,<sps>           Continuous motion; 0 stops the selected motor
    P,<motor>,<steps>,<sps>   Finite move with linked-valve sequencing
    C                         Clean: water 30 s, both 45 s, outlet 45 s
    Y,DETECT                  Probe ASCII / 9600 baud / address 0
    Y,INIT                    Initialize the syringe pump
    Y,VALVE,<1-15>            Select the syringe valve position
    Y,ASPUL,<uL>              Aspirate the specified volume
    Y,DISPUL,<uL>             Dispense the specified volume
    Y,SPEED,<1-6000>          Set syringe pump speed
    Y,Q                       Query syringe pump position
    Y,ST                      Query syringe pump status
    Y,STOP                    Stop the syringe pump
    Y,SYR,<uL>                Set the full-stroke syringe capacity
    W,READ                    Read WEIGHT,<g>,RAW,<raw>,ST,<1|0>
    W,STREAM,<Hz>             Stream weights; default 10 Hz, 0 disables
    W,TARE / W,CAL,<grams>    Tare an empty container / calibrate a known mass
    W,SCAN / W,SNIFF / W,LOOP Diagnostic commands for missing scale readings
    W,SET,<addr>,<fc>,<reg>,<fmt>   Apply scale settings identified by W,SCAN
    W,BAUD / W,AVG / W,STABLE / W,DIV / W,OFFSET / W,RD / W,CFG / W,HELP
    X                         Stop motors, syringe pump, valves and weight stream
    ?                         Query system state

  Linked valves open 1 s before motion and close 1 s after motion stops.
*/

#include <AccelStepper.h>

// All pin assignments and conversion factors are generated from config/hardware.yaml.
// Edit the YAML and run python tools/gen_firmware_config.py; do not edit the header.
#include "hw_config.h"

// -------------------- Two normally closed solenoid valves --------------------
const uint8_t VALVE_PIN[NUM_VALVES] = VALVE_PIN_LIST;   // To ULN#1 IN5 and IN6
const uint8_t MOTOR_VALVE[LAST_MOTOR - FIRST_MOTOR + 1] = MOTOR_VALVE_LIST;
bool valveOpen[NUM_VALVES] = {false, false};
bool valveClosePending[NUM_VALVES] = {false, false};
unsigned long valveCloseAt[NUM_VALVES] = {0, 0};

AccelStepper drv[2] = {
  AccelStepper(AccelStepper::DRIVER, PUL1, DIR1),
  AccelStepper(AccelStepper::DRIVER, PUL2, DIR2)
};

const int ENA_PIN[2] = {ENA1, ENA2};
int cur[2] = {-1, -1};
bool running[2] = {false, false};
bool finiteMoveActive[2] = {false, false};
bool startPending[2] = {false, false};
bool startContinuous[2] = {false, false};
float queuedSps[2] = {0, 0};
long queuedSteps[2] = {0, 0};
unsigned long startAt[2] = {0, 0};
String serialBuffer;

// -------------------- Automatic cleaning schedule --------------------
bool cleaningActive = false;
bool cleaningOutStarted = false;
bool cleaningWaterStopped = false;
unsigned long cleaningOutStartAt = 0;
unsigned long cleaningWaterStopAt = 0;
unsigned long cleaningOutStopAt = 0;

void printState();

// -------------------- Syringe pump --------------------
long syringeUl = PUMP_DEFAULT_SYRINGE_UL;
bool pumpOnline = false;

int groupOf(int motor) { return motor < GROUP_B_FIRST ? 0 : 1; }
int groupFirst(int group) { return group == 0 ? FIRST_MOTOR : GROUP_B_FIRST; }

// DM542J wiring: ENA LOW enables the driver; HIGH releases it.
void driverEnable(int group, bool on) {
  digitalWrite(ENA_PIN[group], on ? LOW : HIGH);
}

void groupRelaysOff(int group) {
  for (int motor = groupFirst(group); motor < groupFirst(group) + 4; ++motor) {
    digitalWrite(motor, LOW);
  }
}

bool timeReached(unsigned long now, unsigned long target) {
  return (long)(now - target) >= 0;
}

int valveForMotor(int motor) {
  if (motor < FIRST_MOTOR || motor > LAST_MOTOR) return 0;
  return MOTOR_VALVE[motor - FIRST_MOTOR];
}

bool setValve(uint8_t valve, bool open) {
  if (valve < 1 || valve > NUM_VALVES) return false;
  uint8_t index = valve - 1;
  digitalWrite(VALVE_PIN[index], open ? HIGH : LOW);
  valveOpen[index] = open;
  return true;
}

void closeAllValves() {
  for (uint8_t i = 0; i < NUM_VALVES; ++i) {
    valveClosePending[i] = false;
    digitalWrite(VALVE_PIN[i], LOW);
    valveOpen[i] = false;
  }
}

void openValveForMotor(int motor) {
  int valve = valveForMotor(motor);
  if (valve <= 0) return;
  valveClosePending[valve - 1] = false;
  setValve((uint8_t)valve, true);
}

void scheduleValveCloseForMotor(int motor) {
  int valve = valveForMotor(motor);
  if (valve <= 0) return;
  uint8_t index = (uint8_t)(valve - 1);
  if (VALVE_CLOSE_AFTER_MS == 0) {
    setValve((uint8_t)valve, false);
    valveClosePending[index] = false;
  } else {
    valveCloseAt[index] = millis() + VALVE_CLOSE_AFTER_MS;
    valveClosePending[index] = true;
  }
}

void serviceValves() {
  unsigned long now = millis();
  bool changed = false;
  for (uint8_t i = 0; i < NUM_VALVES; ++i) {
    if (valveClosePending[i] && timeReached(now, valveCloseAt[i])) {
      valveClosePending[i] = false;
      setValve(i + 1, false);
      changed = true;
    }
  }
  if (changed) printState();
}

void stopGroupMotion(int group, bool closeLinkedValve) {
  bool wasActive = startPending[group] || running[group] || finiteMoveActive[group]
                   || drv[group].distanceToGo() != 0;
  int motor = cur[group];
  startPending[group] = false;
  running[group] = false;
  finiteMoveActive[group] = false;
  drv[group].moveTo(drv[group].currentPosition());
  drv[group].setSpeed(0);
  if (closeLinkedValve && wasActive && motor >= FIRST_MOTOR) {
    scheduleValveCloseForMotor(motor);
  }
}

void startMotorNow(int group, bool continuous, long steps, float sps) {
  startPending[group] = false;
  if (continuous) {
    finiteMoveActive[group] = false;
    running[group] = true;
    drv[group].setSpeed(sps);
  } else {
    running[group] = false;
    finiteMoveActive[group] = true;
    drv[group].setMaxSpeed(fabs(sps));
    drv[group].move(steps);
  }
}

void serviceMotors() {
  unsigned long now = millis();
  bool changed = false;
  for (int group = 0; group < 2; ++group) {
    if (startPending[group] && timeReached(now, startAt[group])) {
      startMotorNow(group, startContinuous[group], queuedSteps[group], queuedSps[group]);
      changed = true;
    }
    if (cur[group] < 0) continue;
    if (running[group]) {
      drv[group].runSpeed();
    } else {
      drv[group].run();
      if (finiteMoveActive[group] && drv[group].distanceToGo() == 0) {
        finiteMoveActive[group] = false;
        scheduleValveCloseForMotor(cur[group]);
        changed = true;
      }
    }
  }
  serviceValves();
  if (changed) printState();
}

void serviceDelay(unsigned long durationMs) {
  unsigned long started = millis();
  while (millis() - started < durationMs) serviceMotors();
}

void selectMotor(int motor) {
  int group = groupOf(motor);
  if (cur[group] == motor) return;

  stopGroupMotion(group, true);
  driverEnable(group, false);
  serviceDelay(5);

  groupRelaysOff(group);
  serviceDelay(SETTLE_MS);
  digitalWrite(motor, HIGH);
  serviceDelay(SETTLE_MS);

  driverEnable(group, true);
  cur[group] = motor;
}

void queueMotorStart(int motor, bool continuous, long steps, float sps) {
  int group = groupOf(motor);
  if (cur[group] == motor && running[group] && continuous && !startPending[group]) {
    drv[group].setSpeed(sps);       // Update gravimetric speed without restarting the valve delay.
    return;
  }
  if (cur[group] == motor && startPending[group] && continuous
      && startContinuous[group]) {
    queuedSps[group] = sps;        // Update the queued speed while waiting for the valve to open.
    return;
  }

  selectMotor(motor);
  stopGroupMotion(group, true);
  int valve = valveForMotor(motor);
  if (valve > 0) {
    openValveForMotor(motor);
    startPending[group] = true;
    startContinuous[group] = continuous;
    queuedSteps[group] = steps;
    queuedSps[group] = sps;
    startAt[group] = millis() + VALVE_OPEN_BEFORE_MS;
  } else {
    startMotorNow(group, continuous, steps, sps);
  }
}

void stopAllMotors() {
  for (int group = 0; group < 2; ++group) {
    stopGroupMotion(group, false);
    driverEnable(group, false);
    groupRelaysOff(group);
    cur[group] = -1;
  }
}

void cancelCleaningSchedule() {
  cleaningActive = false;
  cleaningOutStarted = false;
  cleaningWaterStopped = false;
}

void startCleaning() {
  cancelCleaningSchedule();
  stopAllMotors();
  closeAllValves();

  // Preselect outlet motor 8 on group B while stopped so it starts on schedule,
  // without losing pumping time to the relay switching delay.
  selectMotor(CLEAN_OUT_MOTOR);
  queueMotorStart(CLEAN_WATER_MOTOR, true, 0, CLEAN_WATER_SPS);

  int waterGroup = groupOf(CLEAN_WATER_MOTOR);
  unsigned long waterStarts = startPending[waterGroup] ? startAt[waterGroup] : millis();
  cleaningOutStartAt = waterStarts + CLEAN_WATER_LEAD_MS;
  cleaningWaterStopAt = cleaningOutStartAt + CLEAN_OVERLAP_MS;
  cleaningOutStopAt = cleaningWaterStopAt + CLEAN_OUT_TAIL_MS;
  cleaningActive = true;
}

void serviceCleaning() {
  if (!cleaningActive) return;
  unsigned long now = millis();
  bool changed = false;

  if (!cleaningOutStarted && timeReached(now, cleaningOutStartAt)) {
    queueMotorStart(CLEAN_OUT_MOTOR, true, 0, CLEAN_OUT_SPS);
    cleaningOutStarted = true;
    changed = true;
  }
  if (!cleaningWaterStopped && timeReached(now, cleaningWaterStopAt)) {
    stopGroupMotion(groupOf(CLEAN_WATER_MOTOR), true);
    cleaningWaterStopped = true;
    changed = true;
  }
  if (cleaningOutStarted && timeReached(now, cleaningOutStopAt)) {
    stopGroupMotion(groupOf(CLEAN_OUT_MOTOR), true);
    cleaningActive = false;
    changed = true;
  }

  // Report only the three phase transitions to keep serial traffic bounded.
  if (changed) printState();
}

const char* motorState(int group) {
  if (startPending[group]) return "wait";
  if (running[group]) return "run";
  return finiteMoveActive[group] || drv[group].distanceToGo() != 0 ? "move" : "idle";
}

// -------------------- Syringe pump ASCII / MAX13487 --------------------
void clearPumpInput() {
  while (PUMP_SERIAL.available()) PUMP_SERIAL.read();
}

void sendAsciiBody(const String &body) {
  clearPumpInput();

  // The ASCII request address is '1' + address; address 0 therefore sends /1...
  PUMP_SERIAL.write('/');
  PUMP_SERIAL.write((uint8_t)('1' + PUMP_ADDR));
  for (unsigned int i = 0; i < body.length(); ++i) {
    PUMP_SERIAL.write((uint8_t)body.charAt(i));
  }
  PUMP_SERIAL.write('\r');
  PUMP_SERIAL.flush();
  // The MAX13487 switches direction automatically; no DE/RE control is required.
}

void printHexByte(uint8_t value) {
  if (value < 0x10) Serial.print('0');
  Serial.print(value, HEX);
}

bool readAsciiReply(String &reply, unsigned long timeoutMs, bool reportErrors) {
  reply.remove(0);
  String candidate;
  candidate.reserve(80);

  unsigned long started = millis();
  bool receivedAnything = false;
  bool echoSeen = false;
  uint8_t raw[64];
  uint8_t rawLength = 0;

  while (millis() - started < timeoutMs) {
    serviceMotors();
    if (!PUMP_SERIAL.available()) continue;

    char c = (char)PUMP_SERIAL.read();
    receivedAnything = true;
    if (rawLength < sizeof(raw)) raw[rawLength++] = (uint8_t)c;

    // Some pumps prefix replies with 0xFF; resynchronize at '/'.
    if (c == '/') {
      candidate = "/";
      continue;
    }

    if (c == '\r' || c == '\n' || (uint8_t)c == 0x03) {
      // Valid ASCII replies from address 0 begin with /0.
      if (candidate.startsWith("/0") && candidate.length() >= 3) {
        reply = candidate;
        return true;
      }
      if (candidate.startsWith("/")) echoSeen = true;
      candidate.remove(0);
      continue;
    }

    if (candidate.startsWith("/") && (uint8_t)c >= 0x20 && candidate.length() < 80) {
      candidate += c;
    }
  }

  if (candidate.startsWith("/0") && candidate.length() >= 3) {
    reply = candidate;
    return true;
  }

  if (receivedAnything) {
    Serial.print(F("ASCIIRX,baud=9600,addr=0,count="));
    Serial.print(rawLength);
    Serial.print(F(",hex="));
    for (uint8_t i = 0; i < rawLength; ++i) {
      if (i) Serial.print(' ');
      printHexByte(raw[i]);
    }
    Serial.println();
  }

  if (reportErrors) {
    if (echoSeen) Serial.println(F("ERR,pump_echo_only"));
    else if (receivedAnything) Serial.println(F("ERR,pump_bad_reply"));
    else Serial.println(F("ERR,pump_timeout"));
  }
  return false;
}

bool asciiRequest(const String &body, unsigned long timeoutMs) {
  sendAsciiBody(body);
  String reply;
  reply.reserve(48);

  if (!readAsciiReply(reply, timeoutMs, true)) {
    pumpOnline = false;
    return false;
  }

  pumpOnline = true;
  uint8_t status = (uint8_t)reply.charAt(2);
  uint8_t errorCode = status & 0x0F;
  bool ready = (status & 0x20) != 0;

  Serial.print(F("PUMP,proto=ascii,ready="));
  Serial.print(ready ? 1 : 0);
  Serial.print(F(",error="));
  Serial.print(errorCode);
  if (reply.length() > 3) {
    Serial.print(F(",data="));
    Serial.print(reply.substring(3));
  }
  Serial.println();
  return errorCode == 0;
}

bool probePump() {
  sendAsciiBody("Q");
  String reply;
  reply.reserve(48);
  if (!readAsciiReply(reply, 500, false)) {
    pumpOnline = false;
    Serial.println(F("ERR,pump_not_found_ascii_9600_addr0"));
    return false;
  }

  pumpOnline = true;
  Serial.println(F("FOUND,proto=ascii,baud=9600,addr=0"));
  return true;
}

long ulToPumpSteps(long volumeUl) {
  if (syringeUl <= 0 || volumeUl <= 0) return 0;
  return (long)(((double)volumeUl * (double)PUMP_FULL_STROKE /
                 (double)syringeUl) + 0.5);
}

String pumpBody(char command, long value, bool appendRun) {
  String body;
  body.reserve(18);
  body += command;
  body += value;
  if (appendRun) body += 'R';
  return body;
}

void sendPumpStopNoWait() {
  sendAsciiBody("T");
}

bool parseLongStrict(String text, long &value) {
  text.trim();
  if (text.length() == 0) return false;
  unsigned int i = 0;
  if (text.charAt(0) == '-' || text.charAt(0) == '+') {
    if (text.length() == 1) return false;
    i = 1;
  }
  for (; i < text.length(); ++i) {
    char c = text.charAt(i);
    if (c < '0' || c > '9') return false;
  }
  value = text.toInt();
  return true;
}

void uppercaseKey(String &text) {
  int end = text.indexOf(',');
  if (end < 0) end = text.length();
  for (int i = 0; i < end; ++i) {
    char c = text.charAt(i);
    if (c >= 'a' && c <= 'z') text.setCharAt(i, c - 32);
  }
}

// ================= Scale (RS485 weighing transmitter / Modbus RTU on Serial2)=================
// ---------------- Communication settings (defaults from hardware.yaml)----------------
long          scaleBaud   = SCALE_BAUD; // Transmitter baud rate
byte          scaleAddr   = SCALE_ADDR; // Modbus device address
byte          scaleFunc   = SCALE_FUNC; // 3 = holding registers, 4 = input registers
unsigned int  scaleReg    = SCALE_REG;  // Weight data register
byte          scaleFmt    = SCALE_FMT;  // 0=int16, 1=int32 high word first, 2=int32 low word first
                                  // 3=float(ABCD) 4=float(CDAB)
// ---------------- Conversion and filtering ----------------
float         scaleDiv    = 1.0;  // (raw value - tare offset) / scaleDiv = grams
float         scaleOffset = 0;    // Tare offset in raw units
int           avgN        = SCALE_AVG_SAMPLES; // Moving-average window: 1-16 samples
float         stableG     = SCALE_STABLE_G;    // Stability threshold in grams

// ---------------- Streaming ----------------
int           wStreamHz  = SCALE_STREAM_HZ;  // Weight stream frequency (0 disables)
unsigned long wLastStream = 0;

// ---------------- History buffer ----------------
float rawHist[16];
int   histIdx = 0, histCnt = 0;

// ================= RS485 =================
void scaleTx(bool on) {
  if (on) { digitalWrite(SCALE_DE_PIN, HIGH); delayMicroseconds(60); }
  else    { delayMicroseconds(60); digitalWrite(SCALE_DE_PIN, LOW); }
}

// ================= Modbus RTU =================
unsigned int mbCrc(const byte *b, int n) {
  unsigned int crc = 0xFFFF;
  for (int i = 0; i < n; i++) {
    crc ^= b[i];
    for (int j = 0; j < 8; j++)
      crc = (crc & 1) ? ((crc >> 1) ^ 0xA001) : (crc >> 1);
  }
  return crc;
}

void scaleSend(byte *f, int n) {
  unsigned int c = mbCrc(f, n);
  f[n]     = (byte)(c & 0xFF);
  f[n + 1] = (byte)(c >> 8);
  while (SCALE_SERIAL.available()) SCALE_SERIAL.read();
  scaleTx(true);
  SCALE_SERIAL.write(f, n + 2);
  SCALE_SERIAL.flush();
  scaleTx(false);
}

// Return the number of data bytes on success, or 0 on failure.
int scaleReadRegs(byte addr, byte fc, unsigned int reg, unsigned int cnt,
                  byte *out, int outMax, unsigned long to) {
  byte f[8];
  f[0] = addr; f[1] = fc;
  f[2] = (byte)(reg >> 8); f[3] = (byte)(reg & 0xFF);
  f[4] = (byte)(cnt >> 8); f[5] = (byte)(cnt & 0xFF);
  scaleSend(f, 6);

  byte r[64];
  int  n = 0;
  int  expected = -1;
  unsigned long t0 = millis();
  while (millis() - t0 < to && n < 64) {
    if (SCALE_SERIAL.available()) {
      r[n++] = (byte)SCALE_SERIAL.read();
      // Determine the Modbus reply length from its header and return as soon as
      // the frame is complete instead of waiting for the full 250 ms timeout.
      if (n >= 2 && (r[1] & 0x80)) expected = 5;
      else if (n >= 3) expected = 5 + r[2];
      if (expected > 0 && n >= expected) break;
    } else serviceMotors();                    // Continue servicing motors during scale reads.
  }
  if (n < 5)         return 0;
  if (r[0] != addr)  return 0;
  if (r[1] & 0x80)   return 0;                 // Transmitter exception response
  int bc = r[2];
  if (bc <= 0 || bc + 5 > n) return 0;
  unsigned int crc = mbCrc(r, 3 + bc);
  if (r[3 + bc] != (byte)(crc & 0xFF) || r[4 + bc] != (byte)(crc >> 8)) return 0;
  int copy = (bc < outMax) ? bc : outMax;
  for (int i = 0; i < copy; i++) out[i] = r[3 + i];
  return copy;
}

float scaleDecode(const byte *d) {
  union { unsigned long u; float f; } cv;
  long iv = 0;
  switch (scaleFmt) {
    case 0: iv = (long)(int)(((unsigned int)d[0] << 8) | d[1]); break;
    case 1: iv = ((long)d[0] << 24) | ((long)d[1] << 16) | ((long)d[2] << 8) | d[3]; break;
    case 2: iv = ((long)d[2] << 24) | ((long)d[3] << 16) | ((long)d[0] << 8) | d[1]; break;
    case 3: cv.u = ((unsigned long)d[0] << 24) | ((unsigned long)d[1] << 16)
                 | ((unsigned long)d[2] << 8)  | d[3]; return cv.f;
    case 4: cv.u = ((unsigned long)d[2] << 24) | ((unsigned long)d[3] << 16)
                 | ((unsigned long)d[0] << 8)  | d[1]; return cv.f;
  }
  return (float)iv;
}

bool scaleReadRaw(float &raw) {
  byte d[8];
  unsigned int cnt = (scaleFmt == 0) ? 1 : 2;
  int n = scaleReadRegs(scaleAddr, scaleFunc, scaleReg, cnt, d, 8, 250);
  if (n < (int)(cnt * 2)) return false;
  raw = scaleDecode(d);
  return true;
}

// ================= Filtering and stability detection =================
void histPush(float v) {
  rawHist[histIdx] = v;
  histIdx = (histIdx + 1) % 16;
  if (histCnt < 16) histCnt++;
}

float histAvg(int n) {                        // Mean of the latest n samples
  if (histCnt == 0) return 0;
  if (n > histCnt) n = histCnt;
  float s = 0;
  for (int i = 0; i < n; i++) {
    int k = (histIdx - 1 - i + 32) % 16;
    s += rawHist[k];
  }
  return s / n;
}

bool histStable() {                           // Check the latest six samples against the stability threshold.
  int n = (histCnt < 6) ? histCnt : 6;
  if (n < 3) return false;
  float mn = 1e30, mx = -1e30;
  for (int i = 0; i < n; i++) {
    int k = (histIdx - 1 - i + 32) % 16;
    if (rawHist[k] < mn) mn = rawHist[k];
    if (rawHist[k] > mx) mx = rawHist[k];
  }
  float span = (scaleDiv != 0) ? (mx - mn) / scaleDiv : (mx - mn);
  if (span < 0) span = -span;
  return span <= stableG;
}

byte wFailCount = 0;                          // Consecutive streaming failures

void scaleReport() {
  float raw;
  if (!scaleReadRaw(raw)) {
    Serial.println(F("ERR,timeout(no_module_reply)"));
    // Disable streaming after three consecutive failures to avoid repeated timeouts.
    if (wStreamHz > 0 && ++wFailCount >= 3) {
      wStreamHz = 0; wFailCount = 0;
      Serial.println(F("ERR,stream_stopped_no_reply"));
    }
    return;
  }
  wFailCount = 0;
  histPush(raw);
  float f = histAvg(avgN);
  float g = (scaleDiv != 0) ? (f - scaleOffset) / scaleDiv : 0;
  Serial.print("WEIGHT,"); Serial.print(g, 3);
  Serial.print(",RAW,");   Serial.print(raw, 1);
  Serial.print(",ST,");    Serial.println(histStable() ? 1 : 0);
}

// ================= Diagnostics =================
void scaleSniff(unsigned long ms) {
  while (SCALE_SERIAL.available()) SCALE_SERIAL.read();
  Serial.print("SNIFF,LISTENING "); Serial.print(ms / 1000); Serial.println(" s...");
  unsigned long t0 = millis();
  int n = 0;
  String asc = "";
  while (millis() - t0 < ms) {
    if (SCALE_SERIAL.available()) {
      byte v = (byte)SCALE_SERIAL.read();
      if (n % 16 == 0) { Serial.println(); Serial.print("  "); }
      if (v < 16) Serial.print('0');
      Serial.print(v, HEX); Serial.print(' ');
      asc += (v >= 32 && v < 127) ? (char)v : '.';
      n++;
    } else serviceMotors();
  }
  Serial.println();
  Serial.print("SNIFF,TOTAL "); Serial.print(n); Serial.println(" bytes");
  if (n) { Serial.print("SNIFF,TEXT="); Serial.println(asc);
           Serial.println("HINT,Unsolicited data detected: the transmitter streams continuously."); }
  else   { Serial.println("HINT,No unsolicited data: try SCAN for a Modbus request-response device."); }
}

void scaleScan() {
  const long bauds[4] = {9600, 19200, 38400, 4800};
  const unsigned int regs[6] = {0, 1, 2, 4, 8, 16};
  Serial.println("SCAN,START (approximately 40 s)...");
  int found = 0;
  byte d[8];
  for (int b = 0; b < 4; b++) {
    SCALE_SERIAL.end(); SCALE_SERIAL.begin(bauds[b]); delay(40);
    Serial.print("SCAN,baud="); Serial.println(bauds[b]);
    for (int a = 1; a <= 16; a++)
      for (int fc = 3; fc <= 4; fc++)
        for (int r = 0; r < 6; r++) {
          int n = scaleReadRegs((byte)a, (byte)fc, regs[r], 2, d, 8, 70);
          if (n >= 4) {
            Serial.print("  FOUND,baud="); Serial.print(bauds[b]);
            Serial.print(" addr=");        Serial.print(a);
            Serial.print(" fc=");      Serial.print(fc);
            Serial.print(" reg=");      Serial.print(regs[r]);
            Serial.print(" data=");
            for (int i = 0; i < n; i++) { if (d[i] < 16) Serial.print('0');
                                          Serial.print(d[i], HEX); Serial.print(' '); }
            Serial.println();
            found++;
          }
        }
  }
  SCALE_SERIAL.end(); SCALE_SERIAL.begin(scaleBaud); delay(20);
  Serial.print("SCAN,DONE,FOUND "); Serial.println(found);
  if (found) Serial.println("HINT,Apply with W,SET,<addr>,<fc>,<reg>,<fmt>.");
  else {
    Serial.println("HINT,No responses. Check the following:");
    Serial.println("HINT,1. Verify A/B polarity. 2. Verify power and a common ground.");
    Serial.println("HINT,3. Connect DE/RE to D36. 4. Test Mega Serial2 with W,LOOP.");
  }
}

void scaleLoopTest() {                              // Connect D16 to D17 before running this test.
  while (SCALE_SERIAL.available()) SCALE_SERIAL.read();
  scaleTx(true);
  SCALE_SERIAL.print("LOOPTEST");
  SCALE_SERIAL.flush();
  scaleTx(false);
  String r = "";
  unsigned long t0 = millis();
  while (millis() - t0 < 300) {
    if (SCALE_SERIAL.available()) { r += (char)SCALE_SERIAL.read(); t0 = millis(); }
    else serviceMotors();
  }
  Serial.print("LOOP,RECEIVED=\""); Serial.print(r); Serial.println("\"");
  if (r.indexOf("LOOPTEST") >= 0)
    Serial.println("HINT,Mega Serial2 passed. Check the RS485 adapter and downstream wiring.");
  else
    Serial.println("HINT,No loopback received. Connect D16 directly to D17 and retry.");
}


void scalePrintCfg() {
  Serial.print("CFG,baud=");   Serial.print(scaleBaud);
  Serial.print(",addr=");      Serial.print(scaleAddr);
  Serial.print(",fc=");        Serial.print(scaleFunc);
  Serial.print(",reg=");       Serial.print(scaleReg);
  Serial.print(",fmt=");       Serial.print(scaleFmt);
  Serial.print(",div=");       Serial.print(scaleDiv, 6);
  Serial.print(",offset=");    Serial.print(scaleOffset, 3);
  Serial.print(",avg=");       Serial.print(avgN);
  Serial.print(",stable_g=");  Serial.print(stableG, 3);
  Serial.print(",stream_hz="); Serial.println(wStreamHz);
}

void scalePrintHelp() {
  Serial.println(F("-- Scale commands (all begin with W,) --"));
  Serial.println(F("  W,READ                Read one weight sample"));
  Serial.println(F("  W,STREAM,<Hz>         Stream samples; 0 disables"));
  Serial.println(F("  W,TARE                Tare with an empty container"));
  Serial.println(F("  W,CAL,<grams>         Calibrate with a known mass"));
  Serial.println(F("  W,DIV,<divisor>       Set the conversion divisor"));
  Serial.println(F("  W,OFFSET,<raw>        Set the tare offset"));
  Serial.println(F("  W,AVG,<1-16>          Moving-average sample count"));
  Serial.println(F("  W,STABLE,<grams>      Set the stability threshold"));
  Serial.println(F("  W,SET,<addr>,<fc>,<reg>,<fmt>"));
  Serial.println(F("  W,RD,<addr>,<fc>,<reg>,<count>   Read registers"));
  Serial.println(F("  W,BAUD,<baud>         Set the transmitter baud rate"));
  Serial.println(F("  W,SNIFF[,seconds]     Listen for unsolicited transmitter data"));
  Serial.println(F("  W,SCAN                Scan baud rates, addresses, function codes and registers"));
  Serial.println(F("  W,LOOP                Loopback test (connect D16 to D17 first)"));
  Serial.println(F("  W,CFG / W,HELP        Show settings / this help"));
}

void handleWeightCommand(String s) {                   // Scale commands
  String rest = s.substring(2);
  uppercaseKey(rest);
  int c = rest.indexOf(',');
  String key = (c < 0) ? rest : rest.substring(0, c);
  String arg = (c < 0) ? String("") : rest.substring(c + 1);
  long   v   = arg.toInt();

  if      (key == "READ")   { scaleReport(); }
  else if (key == "HELP")   { scalePrintHelp(); }
  else if (key == "CFG")    { scalePrintCfg(); }
  else if (key == "LOOP")   { scaleLoopTest(); }
  else if (key == "SNIFF")  { scaleSniff(v > 0 ? (unsigned long)v * 1000UL : 3000UL); }
  else if (key == "SCAN")   { scaleScan(); }
  else if (key == "TARE")   { float raw;
                              if (!scaleReadRaw(raw)) { Serial.println("ERR,scale_timeout"); return; }
                              histPush(raw); scaleOffset = histAvg(avgN);
                              histCnt = 0; histIdx = 0;
                              Serial.print("OK,tare_raw="); Serial.println(scaleOffset, 1); }
  else if (key == "CAL")    { float g = arg.toFloat();
                              if (g <= 0) { Serial.println("ERR,arg"); return; }
                              float raw;
                              if (!scaleReadRaw(raw)) { Serial.println("ERR,scale_timeout"); return; }
                              histPush(raw);
                              float d = histAvg(avgN) - scaleOffset;
                              if (d == 0) { Serial.println("ERR,no_change"); return; }
                              scaleDiv = d / g;
                              Serial.print("OK,div="); Serial.print(scaleDiv, 6);
                              Serial.print(",offset="); Serial.println(scaleOffset, 3); }
  else if (key == "DIV")    { float f = arg.toFloat();
                              if (f == 0) { Serial.println("ERR,arg"); return; }
                              scaleDiv = f; Serial.print("OK,div="); Serial.println(scaleDiv, 6); }
  else if (key == "OFFSET") { if (arg.length() == 0) { Serial.println("ERR,arg"); return; }
                              scaleOffset = arg.toFloat(); histCnt = 0; histIdx = 0;
                              Serial.print("OK,offset="); Serial.println(scaleOffset, 3); }
  else if (key == "AVG")    { if (v < 1 || v > 16) { Serial.println("ERR,arg"); return; }
                              avgN = (int)v; Serial.print("OK,avg="); Serial.println(avgN); }
  else if (key == "STABLE") { float f = arg.toFloat();
                              if (f <= 0) { Serial.println("ERR,arg"); return; }
                              stableG = f; Serial.print("OK,stable_g="); Serial.println(stableG, 3); }
  else if (key == "STREAM") { if (v < 0 || v > 20) { Serial.println("ERR,arg"); return; }
                              wStreamHz = (int)v; wLastStream = 0;
                              Serial.print("OK,stream_hz="); Serial.println(wStreamHz); }
  else if (key == "BAUD")   { if (v < 1200 || v > 115200) { Serial.println("ERR,arg"); return; }
                              scaleBaud = v; SCALE_SERIAL.end(); SCALE_SERIAL.begin(scaleBaud);
                              delay(20); Serial.print("OK,baud="); Serial.println(scaleBaud); }
  else if (key == "SET")    { int p1 = arg.indexOf(','), p2 = arg.indexOf(',', p1 + 1),
                                  p3 = arg.lastIndexOf(',');
                              if (p1 < 0 || p2 <= p1 || p3 <= p2) { Serial.println("ERR,arg"); return; }
                              scaleAddr = (byte)arg.substring(0, p1).toInt();
                              scaleFunc = (byte)arg.substring(p1 + 1, p2).toInt();
                              scaleReg  = (unsigned int)arg.substring(p2 + 1, p3).toInt();
                              scaleFmt  = (byte)arg.substring(p3 + 1).toInt();
                              histCnt = 0; histIdx = 0; scalePrintCfg(); }
  else if (key == "RD")     { int p1 = arg.indexOf(','), p2 = arg.indexOf(',', p1 + 1),
                                  p3 = arg.lastIndexOf(',');
                              if (p1 < 0 || p2 <= p1 || p3 <= p2) { Serial.println("ERR,arg"); return; }
                              byte d[16];
                              int n = scaleReadRegs((byte)arg.substring(0, p1).toInt(),
                                                    (byte)arg.substring(p1 + 1, p2).toInt(),
                                                    (unsigned int)arg.substring(p2 + 1, p3).toInt(),
                                                    (unsigned int)arg.substring(p3 + 1).toInt(),
                                                    d, 16, 300);
                              if (n <= 0) { Serial.println("ERR,scale_timeout"); return; }
                              Serial.print("REGS,");
                              for (int i = 0; i < n; i++) { if (d[i] < 16) Serial.print('0');
                                                            Serial.print(d[i], HEX); Serial.print(' '); }
                              Serial.println(); }
  else                      { Serial.println("ERR,bad_scale_cmd"); }
}

void handlePumpCommand(String command) {
  String rest = command.substring(2);
  uppercaseKey(rest);
  int comma = rest.indexOf(',');
  String key = comma < 0 ? rest : rest.substring(0, comma);
  String argument = comma < 0 ? String("") : rest.substring(comma + 1);
  key.trim();

  if (key == "DETECT" || key == "SCAN") {
    probePump();
    return;
  }

  if (key == "INIT") {
    asciiRequest("ZR", 2500);
    return;
  }
  if (key == "Q") {
    asciiRequest("?", 1500);
    return;
  }
  if (key == "ST") {
    asciiRequest("Q", 1500);
    return;
  }
  if (key == "STOP") {
    asciiRequest("T", 1500);
    return;
  }

  long value = 0;
  if (!parseLongStrict(argument, value)) {
    Serial.println(F("ERR,arg"));
    return;
  }

  if (key == "ADDR") {
    if (value != PUMP_ADDR) Serial.println(F("ERR,fixed_addr_0"));
    else Serial.println(F("OK,addr=0"));
  } else if (key == "BAUD") {
    if (value != (long)PUMP_BAUD) Serial.println(F("ERR,fixed_baud_9600"));
    else Serial.println(F("OK,pump_baud=9600"));
  } else if (key == "SYR") {
    if (value < 1 || value > 100000L) {
      Serial.println(F("ERR,arg"));
      return;
    }
    syringeUl = value;
    Serial.print(F("OK,syringe_uL="));
    Serial.println(syringeUl);
  } else if (key == "VALVE") {
    if (value < 1 || value > 15) {
      Serial.println(F("ERR,arg"));
      return;
    }
    asciiRequest(pumpBody('I', value, true), 1500);
  } else if (key == "ASP" || key == "DISP") {
    if (value < 1 || value > PUMP_FULL_STROKE) {
      Serial.println(F("ERR,arg"));
      return;
    }
    asciiRequest(pumpBody(key == "ASP" ? 'P' : 'D', value, true), 1500);
  } else if (key == "ASPUL" || key == "DISPUL") {
    long steps = ulToPumpSteps(value);
    if (steps < 1 || steps > PUMP_FULL_STROKE) {
      Serial.println(F("ERR,arg"));
      return;
    }
    Serial.print(F("INFO,pump_steps="));
    Serial.println(steps);
    asciiRequest(pumpBody(key == "ASPUL" ? 'P' : 'D', steps, true), 1500);
  } else if (key == "POS") {
    if (value < 0 || value > PUMP_FULL_STROKE) {
      Serial.println(F("ERR,arg"));
      return;
    }
    asciiRequest(pumpBody('A', value, true), 1500);
  } else if (key == "SPEED") {
    if (value < 1 || value > 6000L) {
      Serial.println(F("ERR,arg"));
      return;
    }
    asciiRequest(pumpBody('V', value, true), 1500);
  } else {
    Serial.println(F("ERR,bad_pump_cmd"));
  }
}

void printState() {
  Serial.print(F("STATE,a="));
  Serial.print(cur[0]);
  Serial.print(F(",sa="));
  Serial.print(motorState(0));
  Serial.print(F(",b="));
  Serial.print(cur[1]);
  Serial.print(F(",sb="));
  Serial.print(motorState(1));
  Serial.print(F(",pump="));
  Serial.print(pumpOnline ? F("online") : F("unknown"));
  Serial.print(F(",proto=ascii,baud=9600,addr=0,syringe_uL="));
  Serial.print(syringeUl);
  Serial.print(F(",valves="));
  for (uint8_t i = 0; i < NUM_VALVES; ++i) Serial.print(valveOpen[i] ? '1' : '0');
  Serial.print(F(",cal_pulses_per_ml="));
  Serial.print(PULSES_PER_ML);
  Serial.print(F(",max_flow_uL_min="));
  Serial.print(MAX_FLOW_UL_MIN);
  Serial.print(F(",w_stream_hz="));
  Serial.print(wStreamHz);
  Serial.print(F(",cleaning="));
  Serial.print(cleaningActive ? 1 : 0);
  Serial.println();
}

// -------------------- USB command parser --------------------
void handleCommand(String command) {
  command.trim();
  if (command.length() == 0) return;
  uppercaseKey(command);

  if (command.startsWith("Y,")) {
    handlePumpCommand(command);
    return;
  }

  if (command.startsWith("W,")) {
    handleWeightCommand(command);
    return;
  }

  if (command == "C") {
    if (cleaningActive) {
      Serial.println(F("ERR,cleaning_active"));
      return;
    }
    startCleaning();
    Serial.println(F("OK,cleaning_started"));
    return;
  }

  if (cleaningActive && (command.startsWith("S,")
      || command.startsWith("P,") || command.startsWith("V,"))) {
    Serial.println(F("ERR,cleaning_active"));
    return;
  }

  if (command.startsWith("S,")) {
    int motor = command.substring(2).toInt();
    if (motor < FIRST_MOTOR || motor > LAST_MOTOR) {
      Serial.println(F("ERR,idx(2~9)"));
      return;
    }
    selectMotor(motor);
    Serial.println(F("DONE,S"));
    return;
  }

  if (command.startsWith("P,")) {
    int c1 = command.indexOf(',');
    int c2 = command.indexOf(',', c1 + 1);
    int c3 = command.lastIndexOf(',');
    if (c2 < 0 || c3 <= c2) {
      Serial.println(F("ERR,arg"));
      return;
    }

    int motor = command.substring(c1 + 1, c2).toInt();
    long steps = command.substring(c2 + 1, c3).toInt();
    float sps = command.substring(c3 + 1).toFloat();
    if (motor < FIRST_MOTOR || motor > LAST_MOTOR || sps <= 0 || sps > MAX_SPS) {
      Serial.println(F("ERR,arg"));
      return;
    }

    queueMotorStart(motor, false, steps, sps);
    Serial.println(F("OK"));
    return;
  }

  if (command.startsWith("V,")) {
    int c1 = command.indexOf(',');
    int c2 = command.lastIndexOf(',');
    if (c2 <= c1) {
      Serial.println(F("ERR,arg"));
      return;
    }

    int motor = command.substring(c1 + 1, c2).toInt();
    float sps = command.substring(c2 + 1).toFloat();
    if (motor < FIRST_MOTOR || motor > LAST_MOTOR || fabs(sps) > MAX_SPS) {
      Serial.println(F("ERR,arg"));
      return;
    }

    int group = groupOf(motor);
    if (sps == 0) {
      // Stopping an unselected motor must not switch relays, as that would
      // interrupt another motor currently running in the same group.
      if (cur[group] == motor) {
        stopGroupMotion(group, true);
      }
    } else {
      queueMotorStart(motor, true, 0, sps);
    }
    Serial.println(F("OK"));
    return;
  }

  if (command == "X" || command == "STOP") {
    cancelCleaningSchedule();
    stopAllMotors();
    closeAllValves();
    sendPumpStopNoWait();
    wStreamHz = 0;
    wLastStream = 0;
    wFailCount = 0;
    Serial.println(F("OK,all_stopped"));
    return;
  }

  if (command == "?") {
    printState();
    return;
  }

  Serial.println(F("ERR,bad_cmd"));
}

void setup() {
  Serial.begin(115200);
  PUMP_SERIAL.begin(PUMP_BAUD);
  SCALE_SERIAL.begin(scaleBaud);
  pinMode(SCALE_DE_PIN, OUTPUT);
  digitalWrite(SCALE_DE_PIN, LOW);       // Default RS485 direction: receive

  serialBuffer.reserve(96);
  for (int motor = FIRST_MOTOR; motor <= LAST_MOTOR; ++motor) {
    pinMode(motor, OUTPUT);
    digitalWrite(motor, LOW);
  }

  for (uint8_t i = 0; i < NUM_VALVES; ++i) {
    pinMode(VALVE_PIN[i], OUTPUT);
    digitalWrite(VALVE_PIN[i], LOW);
  }

  for (int group = 0; group < 2; ++group) {
    pinMode(ENA_PIN[group], OUTPUT);
    driverEnable(group, false);
    drv[group].setMaxSpeed(MAX_SPS);
    drv[group].setAcceleration(4 * MAX_SPS);
    drv[group].setMinPulseWidth(3);
  }

  Serial.println(F("READY,arduino+pump+2valves+scale,proto=ascii,baud=9600,addr=0"));
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      handleCommand(serialBuffer);
      serialBuffer = "";
    } else if (c != '\r' && serialBuffer.length() < 95) {
      serialBuffer += c;
    }
  }

  // Automatic weight streaming
  if (wStreamHz > 0 && millis() - wLastStream >= (unsigned long)(1000 / wStreamHz)) {
    wLastStream = millis();
    scaleReport();
  }

  serviceCleaning();
  serviceMotors();
}
