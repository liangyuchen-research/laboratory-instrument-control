/*
  PlasmicMega.ino - Arduino Mega 2560
   (Depend on mega\tools\make_firmware.py from independent laboratory v7 of Arduino.ino generates: Do not change this file manually.
    If you want to change, please change that program;v7 remain intact, and the added things are explained in the bottom paragraph) Everything in

  ★ The pin position and coefficient are not in this file, they are all in config/hardware.yaml.
    modified YAML After execution:python tools/gen_firmware_config.py
    It will regenerate the same folder hw_config.h,reuse Arduino IDE Burn this file.

  stepper motor:
    A group motor 2~5: PUL=D22, DIR=D23, ENA=D24, relay=D2~D5
    B group motor 6~9: PUL=D25, DIR=D26, ENA=D27, relay=D6~D9

  syringe pump (fixed setting):
    Serial1: TX1=D18, RX1=D19
    MAX13487 AutoDirection,No need DE/RE control foot
    ASCII, 9600 baud, Location/address 0, Preset syringe 5000 uL

  solenoid valve (normally closed type, ULN2803A + LB2HN-24DS) : 
    valve1=D10, valve2=D11

  weight module (RS485 weighing transmitter,Modbus RTU) : 
    Serial2: TX2=D16, RX2=D17
    DE and RE short circuit then connect D36 (automatic transceiver board can not be connected)
    load cell: red=E+, black=E-, green=S+, white=S-; transmitter power supply 12~24V

  USB sequence instructions (115200) : 
    S,<motor>                 Select motor 2~9
    V,<motor>,<sps>           Continuous rotation; the linkage channel will open the valve first 1 seconds,0=Delayed valve closing after stop
    P,<motor>,<steps>,<sps>   Quantitative steps; linkage channel automatically executes valve opening/pump/Valve closing sequence
    C                         Automatic cleaning: advanced water 30 Second, Same transfer 45 Second, OUT Draw again 45 Second
    Y,DETECT                  test ASCII / 9600 / address 0
    Y,INIT                    syringe pump initialization
    Y,VALVE,<1~15>            switching valve position
    Y,ASPUL,<uL>              Inhalation designation uL
    Y,DISPUL,<uL>             launch designated uL
    Y,SPEED,<1~6000>          Set syringe pump speed
    Y,Q                       Query the location of the syringe pump
    Y,ST                      Query syringe pump status
    Y,STOP                    Stop syringe pump
    Y,SYR,<uL>                Set the full stroke syringe capacity
    W,READ                    Read the weight once → WEIGHT,<g>,RAW,<raw>,ST,<1|0>
    W,STREAM,<Hz>             Automatic continuous reporting, preset at power on 10 Hz,0 = closure
    W,TARE / W,CAL,<grams>     Empty cup returns to zero / Weight correction
    W,SCAN / W,SNIFF / W,LOOP Three diagnostic instructions when the value cannot be read
    W,SET,<addr>,<fc>,<reg>,<fmt>   Apply W,SCAN Scanned settings
    W,BAUD / W,AVG / W,STABLE / W,DIV / W,OFFSET / W,RD / W,CFG / W,HELP
    X                         All stop (two sets of motors + syringe pump + solenoid valve + weight reading)
    ?                         Query the overall status

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
*/

#include <AccelStepper.h>

// All pins and coefficients are here, by config/hardware.yaml is generated.
// If you want to change the pin position, please change it. YAML,Run again python tools/gen_firmware_config.py,Don’t change it by hand.
#include "hw_config.h"

// -------------------- Two normally closed solenoid valves --------------------
const uint8_t VALVE_PIN[NUM_VALVES] = VALVE_PIN_LIST;   // → ULN#1 IN5, IN6
#define MOTOR_COUNT (LAST_MOTOR - FIRST_MOTOR + 1)
uint8_t MOTOR_VALVE[MOTOR_COUNT] = MOTOR_VALVE_LIST;      // Plasmic: boot is v7 linkage; VL Instructions can be changed
uint8_t cleanWaterMotor = CLEAN_WATER_MOTOR;              // Plasmic: boot is v7 two of; CM Instructions can be changed
uint8_t cleanOutMotor = CLEAN_OUT_MOTOR;
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

// ---- Plasmic electronic control part (implemented below ”Plasmic ”Electronic control added part” section)----
#define FW_TAG "p2"
enum { GD_IDLE = 0, GD_BASELINE, GD_PRIMING, GD_COARSE, GD_APPROACH, GD_TRIM, GD_SETTLE, GD_FINAL, GD_TOPUP };
enum { GR_NONE = 0, GR_DONE, GR_CANCELLED, GR_TIMEOUT, GR_NO_GAIN, GR_SCALE };
bool doseActive();
void doseSample(float grams, bool stable);
void doseEnd(uint8_t result);

// -------------------- syringe pump --------------------
long syringeUl = PUMP_DEFAULT_SYRINGE_UL;
bool pumpOnline = false;

int groupOf(int motor) { return motor < GROUP_B_FIRST ? 0 : 1; }
int groupFirst(int group) { return group == 0 ? FIRST_MOTOR : GROUP_B_FIRST; }

// DM542J Common connections: ENA input LOW=enable, HIGH=released.
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
    drv[group].setSpeed(sps);       // Weighing mode speed reduction/Fine processing: no interruption, no re-run and pre-start waiting
    return;
  }
  if (cur[group] == motor && startPending[group] && continuous
      && startContinuous[group]) {
    queuedSps[group] = sps;        // If a new speed is received during the pre-start period, only the speed to be started will be updated.
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

  // first OUT where B Group cold switch to motor 8; can start immediately when the startup time is reached.
  // will not rotate for a short period of time due to waiting for relay switching.
  selectMotor(cleanOutMotor);                    // Plasmic: Both are variables (CM) 
  queueMotorStart(cleanWaterMotor, true, 0, CLEAN_WATER_SPS);

  int waterGroup = groupOf(cleanWaterMotor);
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
    queueMotorStart(cleanOutMotor, true, 0, CLEAN_OUT_SPS);
    cleaningOutStarted = true;
    changed = true;
  }
  if (!cleaningWaterStopped && timeReached(now, cleaningWaterStopAt)) {
    stopGroupMotion(groupOf(cleanWaterMotor), true);
    cleaningWaterStopped = true;
    changed = true;
  }
  if (cleaningOutStarted && timeReached(now, cleaningOutStopAt)) {
    stopGroupMotion(groupOf(cleanOutMotor), true);
    cleaningActive = false;
    changed = true;
  }

  // only reports switching points in three stages, allowing the front end to update in real time without generating a large amount of sequence data.
  if (changed) printState();
}

const char* motorState(int group) {
  if (startPending[group]) return "wait";
  if (running[group]) return "run";
  return finiteMoveActive[group] || drv[group].distanceToGo() != 0 ? "move" : "idle";
}

// -------------------- syringe pump ASCII / MAX13487 --------------------
void clearPumpInput() {
  while (PUMP_SERIAL.available()) PUMP_SERIAL.read();
}

void sendAsciiBody(const String &body) {
  clearPumpInput();

  // ASCII protocol is '1' + address; address 0 therefore sent /1...
  PUMP_SERIAL.write('/');
  PUMP_SERIAL.write((uint8_t)('1' + PUMP_ADDR));
  for (unsigned int i = 0; i < body.length(); ++i) {
    PUMP_SERIAL.write((uint8_t)body.charAt(i));
  }
  PUMP_SERIAL.write('\r');
  PUMP_SERIAL.flush();
  // MAX13487 will automatically switch transmission/receiving direction, no control DE/RE.
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

    // Some pumps will be delivered first 0xFF; from '/' Resync.
    if (c == '/') {
      candidate = "/";
      continue;
    }

    if (c == '\r' || c == '\n' || (uint8_t)c == 0x03) {
      // address 0 effective ASCII replied with /0 . Starting with
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

// ================= weight module(RS485 weighing transmitter / Modbus RTU,Walk Serial2)=================
// ---------------- Communication settings(default value comes from hardware.yaml)----------------
long          scaleBaud   = SCALE_BAUD; // module baud rate
byte          scaleAddr   = SCALE_ADDR; // Modbus slave address
byte          scaleFunc   = SCALE_FUNC; // 3 = holding register, 4 = input register
unsigned int  scaleReg    = SCALE_REG;  // weight value register
byte          scaleFmt    = SCALE_FMT;  // 0=int16 1=int32High character first 2=int32low word first
                                  // 3=float(ABCD) 4=float(CDAB)
// ---------------- Scaling and filtering ----------------
float         scaleDiv    = 1.0;  // (Reading value − Peel) ÷ scaleDiv = grams
float         scaleOffset = 0;    // tare offset(original value)
int           avgN        = SCALE_AVG_SAMPLES; // Moving average sampling number 1~16
float         stableG     = SCALE_STABLE_G;    // Stability judgment threshold(grams)

// ---------------- streaming ----------------
int           wStreamHz  = SCALE_STREAM_HZ;  // Normal continuous return frequency(0 = Stop)
unsigned long wLastStream = 0;

// ---------------- history buffer ----------------
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

// Number of data bytes returned successfully,failed return 0
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
      // Modbus The length of the reply can be known from the header. Leave immediately after collecting everything, no more waiting around
      // 250 ms; This avoids weight polling slowing down the motor start/stop command.
      if (n >= 2 && (r[1] & 0x80)) expected = 5;
      else if (n >= 3) expected = 5 + r[2];
      if (expected > 0 && n >= expected) break;
    } else serviceMotors();                    // The motor cannot stop when reading the scale.
  }
  if (n < 5)         return 0;
  if (r[0] != addr)  return 0;
  if (r[1] & 0x80)   return 0;                 // Module report exception
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

// ================= Filtering and Stability Determination =================
void histPush(float v) {
  rawHist[histIdx] = v;
  histIdx = (histIdx + 1) % 16;
  if (histCnt < 16) histCnt++;
}

float histAvg(int n) {                        // recent n pen average
  if (histCnt == 0) return 0;
  if (n > histCnt) n = histCnt;
  float s = 0;
  for (int i = 0; i < n; i++) {
    int k = (histIdx - 1 - i + 32) % 16;
    s += rawHist[k];
  }
  return s / n;
}

bool histStable() {                           // recent 6 Is the pen swing smaller than the threshold?
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

byte wFailCount = 0;                          // Number of consecutive failures during streaming This correction was introduced after

void scaleReport() {
  float raw;
  if (!scaleReadRaw(raw)) {
    Serial.println(F("ERR,timeout(module did not respond)"));
    // Streaming continuously 3 will automatically close after reading less than once to avoid reading every time. 200ms Main loop stuck
    if (wStreamHz > 0 && ++wFailCount >= 3) {
      wStreamHz = 0; wFailCount = 0;
      Serial.println(F("ERR,stream_stopped_no_reply"));
      if (doseActive()) doseEnd(GR_SCALE);       // Plasmic: The scale does not respond, and the pump will stop immediately if the weighing quantity is determined.
    }
    return;
  }
  wFailCount = 0;
  histPush(raw);
  float f = histAvg(avgN);
  float g = (scaleDiv != 0) ? (f - scaleOffset) / scaleDiv : 0;
  Serial.print("WEIGHT,"); Serial.print(g, 3);
  Serial.print(",RAW,");   Serial.print(raw, 1);
  bool stableNow = histStable();
  Serial.print(",ST,");    Serial.println(stableNow ? 1 : 0);
  doseSample(g, stableNow);                      // Plasmic: The weighing quantity follows each reading.
}

// ================= diagnosis =================
void scaleSniff(unsigned long ms) {
  while (SCALE_SERIAL.available()) SCALE_SERIAL.read();
  Serial.print("SNIFF,monitor "); Serial.print(ms / 1000); Serial.println(" Second…");
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
  Serial.print("SNIFF,common "); Serial.print(n); Serial.println(" byte");
  if (n) { Serial.print("SNIFF,text="); Serial.println(asc);
           Serial.println("HINT,module will automatically send data The optocoupler of the → continuous output type"); }
  else   { Serial.println("HINT,has no active output → Modbus Q&A type,Please execute SCAN"); }
}

void scaleScan() {
  const long bauds[4] = {9600, 19200, 38400, 4800};
  const unsigned int regs[6] = {0, 1, 2, 4, 8, 16};
  Serial.println("SCAN,Start scanning(about 40 Second)…");
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
            Serial.print(" address=");        Serial.print(a);
            Serial.print(" function code=");      Serial.print(fc);
            Serial.print(" scratchpad=");      Serial.print(regs[r]);
            Serial.print(" information Starting from=");
            for (int i = 0; i < n; i++) { if (d[i] < 16) Serial.print('0');
                                          Serial.print(d[i], HEX); Serial.print(' '); }
            Serial.println();
            found++;
          }
        }
  }
  SCALE_SERIAL.end(); SCALE_SERIAL.begin(scaleBaud); delay(20);
  Serial.print("SCAN,Complete,found "); Serial.println(found);
  if (found) Serial.println("HINT,use SET,<address>,<function code>,<scratchpad>,<Format> Apply");
  else {
    Serial.println("HINT,None,Check in sequence:");
    Serial.println("HINT,① A, B Debugging try  ② module power supply and GND common connection");
    Serial.println("HINT,③ DE, RE Short-circuit and then receive D36  ④ Use first LOOP test Mega This end");
  }
}

void scaleLoopTest() {                              // first D16 and D17 short circuit
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
  Serial.print("LOOP,received=\""); Serial.print(r); Serial.println("\"");
  if (r.indexOf("LOOPTEST") >= 0)
    Serial.println("HINT,Mega of Serial2 normal → The problem is 485 module or subsequent lines");
  else
    Serial.println("HINT,not received. Please put it first D16 and D17 Use a Dupont line to short-circuit directly and try again.");
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
  Serial.println(F("-- weight module command(All with W, beginning)--"));
  Serial.println(F("  W,READ                Read the weight once"));
  Serial.println(F("  W,STREAM,<Hz>         automatic continuous return,0 = Stop"));
  Serial.println(F("  W,TARE                tare and return to zero(Do it when the cup is empty)"));
  Serial.println(F("  W,CAL,<grams>          Calibrate after placing known weight"));
  Serial.println(F("  W,DIV,<divisor>          Directly set the conversion divisor"));
  Serial.println(F("  W,OFFSET,<original value>     Directly set tare offset"));
  Serial.println(F("  W,AVG,<1-16>          Moving average sampling number"));
  Serial.println(F("  W,STABLE,<grams>       Stability judgment threshold"));
  Serial.println(F("  W,SET,<address>,<function code>,<scratchpad>,<Format>"));
  Serial.println(F("  W,RD,<address>,<function code>,<scratchpad>,<quantity>   Manually read temporary register"));
  Serial.println(F("  W,BAUD,<Bao rate>         Change module communication baud rate"));
  Serial.println(F("  W,SNIFF[,Second]          Whether the monitoring module actively outputs"));
  Serial.println(F("  W,SCAN                scanning baud ratexaddressxfunction codexscratchpad"));
  Serial.println(F("  W,LOOP                loop test(Short circuit first D16, D17)"));
  Serial.println(F("  W,CFG / W,HELP        display settings / This instruction"));
}

// =====================================================================
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

void handleWeightCommand(String s) {                   // weight module
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
                              scaleDiv = d / g; scaleCalibrated = true;
                              Serial.print("OK,div="); Serial.print(scaleDiv, 6);
                              Serial.print(",offset="); Serial.println(scaleOffset, 3); }
  else if (key == "DIV")    { float f = arg.toFloat();
                              if (f == 0) { Serial.println("ERR,arg"); return; }
                              scaleDiv = f; scaleCalibrated = true;
                              Serial.print("OK,div="); Serial.println(scaleDiv, 6); }
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
}

// -------------------- USB command analysis --------------------
void handleCommand(String command) {
  command.trim();
  if (command.length() == 0) return;
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
      if (doseActive() && motor == gdMotor) doseEnd(GR_CANCELLED);   // Plasmic: Stop this one=Cancel weighing and rationing
      // Stopping the unselected motor should have no action. The relay cannot be switched for this purpose, otherwise it will
      // Unexpectedly interrupted another motor in the same group that was actually running.
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
    doseFinish(GR_CANCELLED);                    // Plasmic (does nothing during dosing)
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
  digitalWrite(SCALE_DE_PIN, LOW);       // RS485 Default reception

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
    // TB6600 module DM542J much slower,3us may be eaten and cause missing steps.
    // Depend on hardware.yaml of motors.min_pulse_us decision(TB6600 use 5us).
#ifndef MIN_PULSE_US
#define MIN_PULSE_US 3
#endif
    drv[group].setMinPulseWidth(MIN_PULSE_US);
  }

  Serial.println(F("READY,arduino+pump+2valves+scale,proto=ascii,baud=9600,addr=0,fw=" FW_TAG));
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      handleCommand(serialBuffer);
      wdLastRx = millis();                       // Plasmic: The computer is still there
      serialBuffer = "";
    } else if (c != '\r' && serialBuffer.length() < 95) {
      serialBuffer += c;
    }
  }

  // Automatic weight return
  if (wStreamHz > 0 && millis() - wLastStream >= (unsigned long)(1000 / wStreamHz)) {
    wLastStream = millis();
    scaleReport();
  }

  serviceCleaning();
  serviceMotors();
  serviceDose();                                 // Plasmic
  serviceWatchdog();                             // Plasmic
}
