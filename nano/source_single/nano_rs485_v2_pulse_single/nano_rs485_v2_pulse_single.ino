// GENERATED FILE — Nano LCD 1.2.1 PULSE single-file edition.
// Canonical sources: firmware/nano_rs485_v2_pulse/{ControlCore.h,nano_rs485_v2_pulse.ino}
// D13 can output pulses after the full Modbus ARM/START/trigger sequence.

/*
 * Plasmic Nano RS485 v2 / pulse firmware 1.2.1 / protocol 1
 * old wiring maintained:D2=#RE, D3=DE, D12=UNO220 trigger, D13=pulse.
 * New protocol:address 2, 9600 8N1, Modbus FC03/FC16; don’t accept old ASCII START.
 * This version explicitly allows output: only new session, Parameters are read back completely and are valid heartbeat, 
 * ARM, START as well as D12 new LOW->HIGH After triggering,D13 only generates pulses.
 * Attention setup() former bootloader/reset Transient cannot be guaranteed by this program; disconnect first 24V/HV test.
 * Does not contain HSPY writing function;Nano LOW is not equal to high voltage power supply OFF or MOSFET has been measured to be turned off.
 * Detailed registers, units and verification methods: same directory README.md.
 */
#include <Arduino.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <avr/wdt.h>
#include <util/atomic.h>
#ifndef PLASMIC_CONTROL_CORE_H
#define PLASMIC_CONTROL_CORE_H

// and Arduino API Irrelevant control core: the same program can do status on the host/fault test.
// all time uint32_t,adopts difference operation processing rollover; Each period is less than 2^31 us.
#include <stdint.h>
#include <string.h>

namespace plasmic {
enum State : uint16_t { IDLE, ARMED, WAIT_TRIGGER, RUNNING, DONE, FAULT };
enum Fault : uint16_t { NO_FAULT, HEARTBEAT_LOST, TRIGGER_TIMEOUT, JOB_TIMEOUT };
enum Command : uint16_t { OPEN_SESSION=1, HEARTBEAT, ARM, START, STOP, CLEAR_FAULT, POWER_REPORT };
enum Result : uint16_t {
  OK=0, CONFIG_APPLIED=1, STOPPED=2,
  BAD_SESSION=10, BAD_SEQUENCE=11, SEQUENCE_CONFLICT=12, BAD_STATE=13,
  BAD_CONFIG=14, REVISION_MISMATCH=15, OUTPUT_INHIBITED=16,
  BAD_COMMAND=17, LEASE_EXPIRED=18, BAD_JOB=19
};
enum Phase : uint16_t { NO_PHASE, HIGH_PHASE, LOW_PHASE };
static const uint32_t TICK_US=100, LEASE_US=2000000UL;
static const uint32_t TRIGGER_WAIT_US=30000000UL, JOB_LIMIT_US=120000000UL;
static const uint32_t POWER_TTL_US=5000000UL;
static const uint16_t CONFIG_WORDS=18, STATUS_WORDS=40;

inline uint16_t read16(const uint8_t *p) { return (uint16_t(p[0])<<8)|p[1]; }
inline uint32_t read32(const uint8_t *p) { return (uint32_t(read16(p))<<16)|read16(p+2); }
inline void put16(uint8_t *p,uint16_t n) { p[0]=n>>8; p[1]=n; }
inline void put32(uint8_t *p,uint32_t n) { put16(p,n>>16); put16(p+2,n); }
inline uint16_t crc16(const uint8_t *p,uint16_t n) {
  uint16_t crc=0xffff;
  while(n--) { crc^=*p++; for(uint8_t i=0;i<8;i++) crc=(crc>>1)^((crc&1)?0xa001:0); }
  return crc;
}
// CRC32/ISO-HDLC,equivalent Python zlib.crc32: init/xorout ffffffff, reflection polynomial edb88320.
inline uint32_t crc32(const uint8_t *p,uint8_t n) {
  uint32_t crc=0xffffffffUL;
  while(n--) { crc^=*p++; for(uint8_t i=0;i<8;i++) crc=(crc>>1)^((crc&1)?0xedb88320UL:0); }
  return crc^0xffffffffUL;
}

struct Config {
  uint32_t revision,onUs,offUs,cycles,loops,integrationUs,intervalUs,voltageV,crc;
  void decode(const uint8_t *p) {
    revision=read32(p); onUs=read32(p+4); offUs=read32(p+8);
    cycles=read32(p+12); loops=read32(p+16); integrationUs=read32(p+20);
    intervalUs=read32(p+24); voltageV=read32(p+28); crc=read32(p+32);
  }
  void encode(uint8_t *p) const {
    put32(p,revision); put32(p+4,onUs); put32(p+8,offUs); put32(p+12,cycles);
    put32(p+16,loops); put32(p+20,integrationUs); put32(p+24,intervalUs);
    put32(p+28,voltageV); put32(p+32,crc);
  }
  bool valid(const uint8_t *p) const {
    if(!revision || crc32(p,32)!=crc) return false;
    // First version restrictions:1 ms-1 s,100 us scale. Cannot claim to support arbitrary microsecond pulses.
    if(onUs<1000 || onUs>1000000UL || offUs<1000 || offUs>1000000UL) return false;
    if(onUs%TICK_US || offUs%TICK_US || !cycles || !loops) return false;
    if(cycles>10000 || loops>1000 || uint64_t(cycles)*loops>10000) return false;
    if(uint64_t(onUs+offUs)*cycles*loops>JOB_LIMIT_US) return false;
    // The following three items are RPi/LCD of metadata,Nano does not apply to spectrometers or power supplies.
    if(!integrationUs || integrationUs>60000000UL || intervalUs<integrationUs) return false;
    if(intervalUs>60000000UL || voltageV>1000) return false;
    return true;
  }
};

class Controller {
 public:
  Config config;
  State state;
  Fault fault;
  Phase phase;
  Result result;
  bool configValid,outputHigh,trigger,seenLow,triggerSeen,leaseValid;
  const bool outputsAllowed;
  uint32_t session,lastSeq,job,pulses,loopsDone,aborted,statusSeq,lastEdgeUs;
  uint32_t heartbeatUs,jobStartUs,waitStartUs,edgeStartUs;
  // Display information, not output interlock,It does not mean that the terminal is no longer powered..0=?, 1=OFF, 2=ON.
  uint16_t powerReport;
  bool powerFresh;
  uint32_t powerReportUs,powerRequest;

  explicit Controller(bool allow=false): outputsAllowed(allow) {
    memset(&config,0,sizeof(config)); state=IDLE; fault=NO_FAULT; phase=NO_PHASE; result=OK;
    configValid=outputHigh=trigger=seenLow=triggerSeen=leaseValid=false;
    session=lastSeq=job=pulses=loopsDone=aborted=statusSeq=lastEdgeUs=0;
    heartbeatUs=jobStartUs=waitStartUs=edgeStartUs=0;
    powerReport=0; powerFresh=false; powerReportUs=powerRequest=0;
    lastKind=lastLength=0; cachedResult=OK; memset(lastPayload,0,sizeof(lastPayload));
  }
  bool active() const { return state==ARMED || state==WAIT_TRIGGER || state==RUNNING; }
  bool fresh(uint32_t now) const { return session && leaseValid && uint32_t(now-heartbeatUs)<LEASE_US; }
  uint16_t displayedPower(uint32_t now) const {
    return powerFresh && uint32_t(now-powerReportUs)<POWER_TTL_US ? powerReport : 0;
  }
  void stop(uint32_t now) {
    if(outputHigh) { ++aborted; lastEdgeUs=now; }
    outputHigh=false; phase=NO_PHASE; seenLow=false;
    if(state!=FAULT) state=IDLE;
    ++statusSeq;
  }
  void fail(Fault f,uint32_t now) { stop(now); fault=f; state=FAULT; ++statusSeq; }

  // Every 100 us Depend on Timer1 ISR call; no serial port,I2C, LCD, Configuration verification.
  void tick(uint32_t now,bool input) {
    trigger=input;
    if(powerFresh && uint32_t(now-powerReportUs)>=POWER_TTL_US) powerFresh=false;
    // even though IDLE also latch expires; avoid micros 71 minutes rollover Houjiu lease resurrection.
    if(leaseValid && uint32_t(now-heartbeatUs)>=LEASE_US) leaseValid=false;
    if(!active()) return;
    if(!fresh(now)) { fail(HEARTBEAT_LOST,now); return; }
    if(state==ARMED) return;
    if(uint32_t(now-jobStartUs)>=JOB_LIMIT_US) { fail(JOB_TIMEOUT,now); return; }
    if(state==WAIT_TRIGGER) {
      if(uint32_t(now-waitStartUs)>=TRIGGER_WAIT_US) { fail(TRIGGER_TIMEOUT,now); return; }
      if(!input) seenLow=true;
      if(input && seenLow) {
        seenLow=false; triggerSeen=true; outputHigh=outputsAllowed;
        phase=HIGH_PHASE; state=RUNNING; edgeStartUs=lastEdgeUs=now; ++statusSeq;
      }
      return;
    }
    if(phase==HIGH_PHASE && uint32_t(now-edgeStartUs)>=config.onUs) {
      outputHigh=false; ++pulses; phase=LOW_PHASE; edgeStartUs=lastEdgeUs=now; ++statusSeq;
    } else if(phase==LOW_PHASE && uint32_t(now-edgeStartUs)>=config.offUs) {
      // Not used ISR Inside uint32 division/remainder, using count per loop.
      ++cycleDone;
      if(cycleDone>=config.cycles) {
        cycleDone=0; ++loopsDone; phase=NO_PHASE;
        if(loopsDone>=config.loops) state=DONE;
        else { state=WAIT_TRIGGER; waitStartUs=now; seenLow=false; }
        ++statusSeq;
      } else {
        outputHigh=outputsAllowed; phase=HIGH_PHASE; edgeStartUs=lastEdgeUs=now; ++statusSeq;
      }
    }
  }

  // payload: session32, request32, Config(36 bytes). candidateValid in Africa ISR area is calculated first.
  Result setConfig(const uint8_t *p,const Config &candidate,bool candidateValid,uint32_t now) {
    (void)now;
    if(!checkSequence(p,44,1)) return result;
    Result r=CONFIG_APPLIED;
    if(state!=IDLE && state!=DONE) r=BAD_STATE;
    else if(!candidateValid) r=BAD_CONFIG;
    else if(configValid && candidate.revision<=config.revision) r=REVISION_MISMATCH;
    else { config=candidate; configValid=true; state=IDLE; ++statusSeq; }
    return remember(p,44,1,r);
  }

  // payload: session32, request32, command16, job32, expected_revision32.
  Result command(const uint8_t *p,uint32_t now) {
    const uint32_t s=read32(p), seq=read32(p+4), j=read32(p+10), rev=read32(p+14);
    const uint16_t op=read16(p+8);
    // STOP is always executed first, not required session/increments the serial number and does not clear the deduplication record.
    // so old START replay cannot be in STOP ; AfterSTOP If the reply is lost, it can be safely resent.
    if(op==STOP) { stop(now); return result=STOPPED; }
    if(op==OPEN_SESSION && s && seq && s!=session) {
      if(active()) return result=BAD_STATE;
      if(j || rev) return result=BAD_COMMAND;
      session=s; lastSeq=0; lastLength=0; configValid=false;
      memset(&config,0,sizeof(config)); heartbeatUs=now; leaseValid=true; job=0;
      powerReport=0; powerFresh=false; powerReportUs=powerRequest=0;
      // new session cannot be cleared by itself FAULT.
      if(state!=FAULT) state=IDLE;
      ++statusSeq;
    }
    if(!checkSequence(p,18,2)) return result;
    Result r=OK;
    switch(op) {
      case OPEN_SESSION:
        // same session can only resend the same OPEN,does not accept it for extension lease.
        if(lastSeq || j || rev) r=BAD_COMMAND;
        break;
      case HEARTBEAT:
        if(j || rev) r=BAD_COMMAND; else { heartbeatUs=now; leaseValid=true; }
        break;
      case POWER_REPORT:
        // RPi must be read successfully first HSPY,will report again; if the reading fails, send 0.Can’t use it to continue heartbeat.
        // continues to be used session/request Deduplication and resending old packets will not extend the data validity period.
        if(j>2 || rev) r=BAD_COMMAND;
        else { powerReport=j; powerFresh=(j!=0); powerReportUs=now; powerRequest=seq; ++statusSeq; }
        break;
      case ARM:
        if(!outputsAllowed) r=OUTPUT_INHIBITED;
        else if(state!=IDLE && state!=DONE) r=BAD_STATE;
        else if(!configValid || rev!=config.revision) r=REVISION_MISMATCH;
        else if(!j) r=BAD_JOB;
        else if(!fresh(now)) r=LEASE_EXPIRED;
        else { state=ARMED; job=j; pulses=loopsDone=aborted=cycleDone=0; triggerSeen=false; ++statusSeq; }
        break;
      case START:
        if(!outputsAllowed) r=OUTPUT_INHIBITED;
        else if(state!=ARMED) r=BAD_STATE;
        else if(!j || j!=job) r=BAD_JOB;
        else if(rev!=config.revision) r=REVISION_MISMATCH;
        else if(!fresh(now)) r=LEASE_EXPIRED;
        else { state=WAIT_TRIGGER; seenLow=false; jobStartUs=waitStartUs=now; ++statusSeq; }
        break;
      case CLEAR_FAULT:
        if(active() || j || rev) r=BAD_STATE;
        else if(!fresh(now)) r=LEASE_EXPIRED;
        else { stop(now); fault=NO_FAULT; state=IDLE; ++statusSeq; }
        break;
      default: r=BAD_COMMAND;
    }
    return remember(p,18,2,r);
  }

 private:
  uint32_t cycleDone=0;
  uint8_t lastPayload[44],lastKind,lastLength;
  Result cachedResult;
  bool checkSequence(const uint8_t *p,uint8_t length,uint8_t kind) {
    const uint32_t s=read32(p), seq=read32(p+4);
    if(!session || s!=session) { result=BAD_SESSION; return false; }
    if(!seq || seq<lastSeq) { result=BAD_SEQUENCE; return false; }
    if(seq==lastSeq) {
      result=(kind==lastKind && length==lastLength && !memcmp(p,lastPayload,length))
        ? cachedResult : SEQUENCE_CONFLICT;
      return false;
    }
    return true;
  }
  Result remember(const uint8_t *p,uint8_t length,uint8_t kind,Result r) {
    memcpy(lastPayload,p,length); lastLength=length; lastKind=kind;
    lastSeq=read32(p+4); cachedResult=result=r; return r;
  }
};
} // namespace plasmic
#endif


#if !defined(__AVR_ATmega328P__) || F_CPU != 16000000UL
#error "This firmware is only for classic ATmega328P Nano at 16 MHz."
#endif
#if defined(HARD_OUTPUT_ENABLE) && HARD_OUTPUT_ENABLE != 1
#error "Pulse firmware requires HARD_OUTPUT_ENABLE=1; use the SAFE sketch for locked output."
#endif
#define HARD_OUTPUT_ENABLE 1

using namespace plasmic;
const uint8_t ADDRESS=2, RE_PIN=2, DE_PIN=3, TRIGGER_PIN=12, PULSE_PIN=13;
const uint32_t FRAME_GAP_US=5000, TX_LIMIT_US=200000UL;
Controller control(HARD_OUTPUT_ENABLE==1);
LiquidCrystal_I2C lcd(0x27,20,4);
volatile uint32_t timerUs=0;
uint8_t resetCause __attribute__((section(".noinit")));
#ifndef PLASMIC_HOST_TEST
void captureReset(void) __attribute__((naked,section(".init3")));
void captureReset(void) { resetCause=MCUSR; MCUSR=0; wdt_disable(); }
#endif

// fixed size buffer: maximum native FC16=53 bytes,status reply=85 bytes.
uint8_t rx[64],tx[85],rxLength=0,txLength=0,txIndex=0;
bool rxBad=false,txActive=false,txStarted=false;
uint32_t lastRxUs=0,txBeginUs=0;
uint16_t crcErrors=0; // is for local diagnosis and does not affect transactions at other addresses.
char lcdTarget[4][21],lcdShown[4][20];
uint8_t lcdPosition=80;
uint32_t lcdFrameMs=0;

ISR(TIMER1_COMPA_vect) {
  timerUs=micros(); // uses the actual elapsed time, not due to one compare delays and accumulates software clock drift. When creating
  control.tick(timerUs,(PINB & _BV(PB4))!=0); // Nano D12 = PB4
  if(HARD_OUTPUT_ENABLE && control.outputHigh) PORTB|=_BV(PB5);
  else PORTB&=~_BV(PB5);                     // Nano D13 = PB5
}

void applyLowImmediately() {
  // command processing is located in atomic district,STOP No need to wait for the next one tick Caila LOW.
  if(!control.outputHigh || !HARD_OUTPUT_ENABLE) PORTB&=~_BV(PB5);
}
void startReply(uint8_t length) {
  const uint16_t crc=crc16(tx,length);
  tx[length]=crc; tx[length+1]=crc>>8; txLength=length+2; txIndex=0;
  digitalWrite(RE_PIN,HIGH); digitalWrite(DE_PIN,HIGH);
  txBeginUs=micros(); txActive=true; txStarted=false;
}
void exceptionReply(uint8_t function,uint8_t code) {
  tx[0]=ADDRESS; tx[1]=function|0x80; tx[2]=code; startReply(3);
}
void serviceTx() {
  if(!txActive) return;
  const uint32_t age=micros()-txBeginUs;
  if(age>=TX_LIMIT_US) {
    // Don’t let UART The fault permanently occupies the bus. Disable energy pulse first, then deliver watchdog reset.
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { control.stop(timerUs); applyLowImmediately(); }
    digitalWrite(DE_PIN,LOW); digitalWrite(RE_PIN,LOW);
    wdt_enable(WDTO_15MS); while(true) {} // Deliberately only in TX Fatal fault stop,D13 already LOW.
  }
  if(age<100) return; // DE settle,non-blocking.
  // Maximum playback time 8 bytes,Timer1 ISR continues to run during transmission.
  for(uint8_t i=0;i<8 && txIndex<txLength && Serial.availableForWrite()>0;i++) {
    Serial.write(tx[txIndex++]); txStarted=true;
  }
  // AVR core of write Will clear TXC0; Waiting for the end stop bit is released only after completion DE.
  if(txStarted && txIndex==txLength && !(UCSR0B&_BV(UDRIE0)) && (UCSR0A&_BV(TXC0))) {
    digitalWrite(DE_PIN,LOW); digitalWrite(RE_PIN,LOW); txActive=false;
    lastRxUs=micros();
  }
}

void statusBytes(uint8_t *out) {
  uint16_t words[STATUS_WORDS];
  uint32_t values[11];
  memset(words,0,sizeof(words));
  // consistent snapshot; Not here critical section Do CRC, string or serial port.
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    const uint32_t now=timerUs;
    words[0]=0x504e; words[1]=1; words[2]=0x0121;
    words[3]=(HARD_OUTPUT_ENABLE?1:0)|(control.configValid?2:0)|(control.session?4:0)
      |(control.fresh(now)?8:0)|(control.triggerSeen?16:0);
    words[4]=control.state; words[5]=control.fault; words[6]=control.outputHigh;
    words[7]=(PINB&_BV(PB5))?1:0; words[8]=(PINB&_BV(PB4))?1:0; words[9]=control.result;
    values[0]=control.session; values[1]=control.lastSeq; values[2]=control.config.revision;
    values[3]=control.config.crc; values[4]=control.job; values[5]=control.pulses;
    values[6]=control.loopsDone; values[7]=now;
    values[8]=control.session ? uint32_t(now-control.heartbeatUs) : 0xffffffffUL;
    values[9]=(control.state==WAIT_TRIGGER || control.state==RUNNING) ? uint32_t(now-control.jobStartUs) : 0;
    values[10]=control.statusSeq;
    words[32]=resetCause; words[33]=control.phase;
    words[34]=control.aborted>>16; words[35]=control.aborted;
    words[36]=control.lastEdgeUs>>16; words[37]=control.lastEdgeUs;
    words[38]=TICK_US; words[39]=(control.state==RUNNING)?1:0;
  }
  // AVR 32-bit Division is done outside of interrupt, shortened Timer1 The time when it was obscured.
  values[7]/=1000; if(values[8]!=0xffffffffUL) values[8]/=1000; values[9]/=1000;
  for(uint8_t i=0;i<11;i++) { words[10+i*2]=values[i]>>16; words[11+i*2]=values[i]; }
  for(uint8_t i=0;i<STATUS_WORDS;i++) put16(out+2*i,words[i]);
}

void handleFrame() {
  if(rxBad || rxLength<4 || rx[0]!=ADDRESS) return; // Include broadcast=0: will not act at all.
  if(crc16(rx,rxLength)!=0) { ++crcErrors; return; }
  const uint8_t fn=rx[1];
  if(fn==3) {
    if(rxLength!=8) { exceptionReply(fn,3); return; }
    const uint16_t start=read16(rx+2), count=read16(rx+4);
    if(!count || count>STATUS_WORDS) { exceptionReply(fn,3); return; }
    tx[0]=ADDRESS; tx[1]=3; tx[2]=count*2;
    if(start<STATUS_WORDS && uint32_t(start)+count<=STATUS_WORDS) {
      statusBytes(tx+3);
      memmove(tx+3,tx+3+start*2,count*2);
    } else if(start>=0x100 && uint32_t(start)+count<=0x100+CONFIG_WORDS) {
      uint8_t data[36];
      ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { control.config.encode(data); }
      memcpy(tx+3,data+(start-0x100)*2,count*2);
    } else if(start>=0x300 && uint32_t(start)+count<=0x308) {
      uint16_t words[8]; uint32_t age;
      ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
        words[0]=control.powerReport; words[1]=control.displayedPower(timerUs);
        age=control.powerFresh ? uint32_t(timerUs-control.powerReportUs) : 0xffffffffUL;
        words[4]=control.powerRequest>>16; words[5]=control.powerRequest;
        words[6]=control.active(); words[7]=(lcdPosition==80);
      }
      if(age!=0xffffffffUL) age/=1000;
      words[2]=age>>16; words[3]=age;
      for(uint8_t i=0;i<count;i++) put16(tx+3+i*2,words[start-0x300+i]);
    } else { exceptionReply(fn,2); return; }
    startReply(3+count*2); return;
  }
  if(fn!=16) { exceptionReply(fn,1); return; }
  if(rxLength<9) { exceptionReply(fn,3); return; }
  const uint16_t start=read16(rx+2), count=read16(rx+4);
  if(count>22 || rx[6]!=count*2 || rxLength!=9+count*2) { exceptionReply(fn,3); return; }
  if(start==0x100 && count==22) {
    Config candidate; candidate.decode(rx+15);
    const bool valid=candidate.valid(rx+15); // expensive CRC32/64-bit Verify that interrupts are not turned off.
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { control.setConfig(rx+7,candidate,valid,timerUs); applyLowImmediately(); }
  } else if(start==0x200 && count==9) {
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { control.command(rx+7,timerUs); applyLowImmediately(); }
  } else { exceptionReply(fn,2); return; }
  // FC16 ACK only indicates complete receipt. You need to read whether to apply it. status.result and config.
  memcpy(tx,rx,6); startReply(6);
}

void serviceRx() {
  if(txActive) return;
  const uint32_t now=micros();
  if((rxLength || rxBad) && uint32_t(now-lastRxUs)>=FRAME_GAP_US) {
    handleFrame(); rxLength=0; rxBad=false;
    if(txActive) return;
  }
  // Bounded consumption, too long packets are discarded until the next complete idle gap,does not look for addresses in the middle.
  for(uint8_t i=0;i<32 && Serial.available()>0;i++) {
    const uint8_t b=Serial.read();
    if(rxLength<sizeof(rx) && !rxBad) rx[rxLength++]=b; else rxBad=true;
    lastRxUs=micros();
  }
}

// follows the original flash.ino of fixed 20x4 Arrangement: Left OT/FT/CY,right IT/SI/Vt/LP.
// All values come from applied and readable config; Displayed when not synchronized --,Old snapshots are not used.
void formatLcd(char lines[4][21],const Config &c,bool valid,State state,Fault fault,uint16_t power) {
  char left[4][13],right[4][9];
  const char *powerText=power==2?"ON":power==1?"OFF":"?";
  snprintf(left[0],13,"POWER %s*%s",powerText,fault!=NO_FAULT?" F":state==RUNNING?" R":"");
  if(valid) {
    snprintf(left[1],13,"OT:%lu.%lu",(unsigned long)(c.onUs/1000),(unsigned long)((c.onUs%1000)/100));
    snprintf(left[2],13,"FT:%lu.%lu",(unsigned long)(c.offUs/1000),(unsigned long)((c.offUs%1000)/100));
    snprintf(left[3],13,"CY:%lu",(unsigned long)c.cycles);
    snprintf(right[0],9,"IT:%lu",(unsigned long)(c.integrationUs/1000));
    snprintf(right[1],9,"SI:%lu",(unsigned long)(c.intervalUs/1000));
    snprintf(right[2],9,"Vt:%lu",(unsigned long)c.voltageV);
    snprintf(right[3],9,"LP:%lu",(unsigned long)c.loops);
  } else {
    snprintf(left[1],13,"OT:--"); snprintf(left[2],13,"FT:--");
    snprintf(left[3],13,"CY:--"); snprintf(right[0],9,"IT:--");
    snprintf(right[1],9,"SI:--"); snprintf(right[2],9,"Vt:--");
    snprintf(right[3],9,"LP:--");
  }
  for(uint8_t row=0;row<4;row++) {
    for(uint8_t col=0;col<20;col++) lines[row][col]=' ';
    lines[row][20]=0;
    for(uint8_t col=0;col<12 && left[row][col];col++) lines[row][col]=left[row][col];
    for(uint8_t col=0;col<8 && right[row][col];col++) lines[row][12+col]=right[row][col];
  }
}

void refreshLcd() {
  // Not performed during the actual pulse train I2C; ARMED/WAIT_TRIGGER can be refreshed HSPY power return.
  // RPi needs to be confirmed first LCD Shown POWER ON,will be sent again D12 trigger.
  State stateSnapshot;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { stateSnapshot=control.state; }
  if(stateSnapshot==RUNNING || rxLength || rxBad || txActive || Serial.available() || micros()-lastRxUs<20000) return;
  if(lcdPosition==80) {
    if(uint32_t(millis()-lcdFrameMs)<250) return;
    Config c; bool valid; State state; Fault fault; uint16_t power;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
      c=control.config; valid=control.configValid; state=control.state; fault=control.fault;
      power=control.displayedPower(timerUs);
    }
    formatLcd(lcdTarget,c,valid,state,fault,power);
    lcdFrameMs=millis(); lcdPosition=0;
  }
  // every time main loop Only one word can be changed at most, and the difference update avoids the whole page. clear flashing.
  const uint8_t row=lcdPosition/20,col=lcdPosition%20;
  if(lcdShown[row][col]!=lcdTarget[row][col]) {
    lcd.setCursor(col,row); lcd.write(lcdTarget[row][col]);
    lcdShown[row][col]=lcdTarget[row][col];
  }
  ++lcdPosition;
}

void setup() {
  digitalWrite(PULSE_PIN,LOW); pinMode(PULSE_PIN,OUTPUT);
  digitalWrite(DE_PIN,LOW); pinMode(DE_PIN,OUTPUT);
  digitalWrite(RE_PIN,LOW); pinMode(RE_PIN,OUTPUT);
  pinMode(TRIGGER_PIN,INPUT); // Must have appropriate external pull-down; not used INPUT_PULLUP.
  Wire.begin(); Wire.setWireTimeout(2000,true);
  lcd.init(); lcd.backlight(); lcd.setCursor(0,0); lcd.print(F("NANO2 FW1.2.1 PULSE"));
  Serial.begin(9600,SERIAL_8N1);
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    TCCR1A=0; TCCR1B=0; TCNT1=0; OCR1A=199; // 16 MHz /8 /200 = 10 kHz
    TIFR1=_BV(OCF1A); TCCR1B=_BV(WGM12)|_BV(CS11); TIMSK1=_BV(OCIE1A);
  }
  wdt_enable(WDTO_2S);
}

void loop() {
  serviceTx(); serviceRx(); refreshLcd();
  wdt_reset(); // Not here Timer ISR feed the dog,Avoid main loop deadlock but continue to feed the dog.
}
