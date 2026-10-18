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
