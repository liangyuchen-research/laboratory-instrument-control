#include <Wire.h>
#include <Arduino.h>
#include <LiquidCrystal_I2C.h>

LiquidCrystal_I2C lcd(0x27,20,4);  // 20-column, 4-row LCD at I2C address 0x27
String from_RS485 = "";
String from_RS485_while_running = "";
bool shouldStop = false;
bool isRS485MessageComplete = false;
bool start = false;

// Define the MAX485 transceiver control pins
const int rePin = 2;  // Receiver Enable pin
const int dePin = 3;  // Driver Enable pin

// Arduino device address (01)
const String DEVICE_ADDRESS = "01";

struct Parameters {
  float on_time;
  float on_time_micro;
  float off_time;
  float off_time_micro;
  int it_time;
  int spectra_interval;
  int cycle;
  int loopnum;
  int voltage;
  int val;

  float on_time_previous;
  float on_time_micro_previous;
  float off_time_previous;
  float off_time_micro_previous;
  int it_time_previous;
  int spectra_interval_previous;
  int cycle_previous;
  int loopnum_previous;
  int voltage_previous;
  int val_previous;
};

Parameters params = {0.0, 0.0, 0.0, 0.0, 0, 0, 0, 0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0, 0, 0, 0, 0, 0};

bool checkParametersChanged() {
  bool changed = false;
  if (params.on_time != params.on_time_previous) {
    params.on_time_previous = params.on_time;
    changed = true;
  }
  if (params.off_time != params.off_time_previous) {
    params.off_time_previous = params.off_time;
    changed = true;
  }
  if (params.it_time != params.it_time_previous) {
    params.it_time_previous = params.it_time;
    changed = true;
  }
  if (params.spectra_interval != params.spectra_interval_previous) {
    params.spectra_interval_previous = params.spectra_interval;
    changed = true;
  }
  if (params.cycle != params.cycle_previous) {
    params.cycle_previous = params.cycle;
    changed = true;
  }

  if (params.loopnum != params.loopnum_previous) {
    params.loopnum_previous = params.loopnum;
    changed = true;
  }
  if (params.on_time_micro != params.on_time_micro_previous) {
    params.on_time_micro_previous = params.on_time_micro;
    changed = true;
  }
  if (params.off_time_micro != params.off_time_micro_previous) {
    params.off_time_micro_previous = params.off_time_micro;
    changed = true;
  }
  if (params.voltage != params.voltage_previous) {
    params.voltage_previous = params.voltage;
    changed = true;
  }
  return changed;
}

void updateLCD(){
  lcd.clear();
  lcd.setCursor(0, 1);
  lcd.print("OT:");
  lcd.setCursor(3, 1);
  String ontime = String(params.on_time + (params.on_time_micro / 1000));
  lcd.print(ontime);

  lcd.setCursor(0, 2);
  lcd.print("FT:");
  lcd.setCursor(3, 2);
  String offtime = String(params.off_time + (params.off_time_micro / 1000));
  lcd.print(offtime);

  lcd.setCursor(0, 3);
  lcd.print("CY:");
  lcd.setCursor(3, 3);
  lcd.print(params.cycle);

  lcd.setCursor(12, 0);
  lcd.print("IT:");
  lcd.setCursor(15, 0);
  lcd.print(params.it_time);

  lcd.setCursor(12, 1);
  lcd.print("SI:");
  lcd.setCursor(15, 1);
  lcd.print(params.spectra_interval);

  lcd.setCursor(12, 2);
  lcd.print("Vt:");
  lcd.setCursor(15, 2);
  lcd.print(params.voltage);

  lcd.setCursor(12, 3);
  lcd.print("LP:");
  lcd.setCursor(15, 3);
  lcd.print(params.loopnum);

  lcd.setCursor(0, 0);
  lcd.print("On:");
  lcd.setCursor(5, 0);
  lcd.print("Off:");
  lcd.setCursor(9, 0);
  lcd.write((char)255);
}

// Switch the RS485 transceiver to transmit mode
void rs485Transmit() {
  digitalWrite(rePin, HIGH);
  digitalWrite(dePin, HIGH);
  delay(1); // Small delay for signal stabilization
}

// Switch the RS485 transceiver to receive mode
void rs485Receive() {
  digitalWrite(rePin, LOW);
  digitalWrite(dePin, LOW);
  delay(1); // Small delay for signal stabilization
}

void setup()
{
  pinMode(13, OUTPUT);
  pinMode(12, INPUT);
  pinMode(rePin, OUTPUT);
  pinMode(dePin, OUTPUT);

  lcd.init();
  Serial.begin(9600);
  lcd.begin(20, 4);
  lcd.backlight();
  updateLCD();

  // Initialize RS485 in receive mode
  rs485Receive();
}

void loop() {
  if (Serial.available() > 0) {
    String temp_buffer = Serial.readStringUntil('\n');

    // Accept only messages addressed to this device
    if (temp_buffer.startsWith("ADDR:" + DEVICE_ADDRESS)) {
      from_RS485 = temp_buffer; // Store the complete received command
      from_RS485.replace("ADDR:" + DEVICE_ADDRESS + ":", "");

      // Process the command based on the message
      if (from_RS485.startsWith("condition")) {
        from_RS485.replace("condition", "");
        params.on_time = from_RS485.substring(0, 3).toFloat();
        params.on_time_micro = from_RS485.substring(3, 7).toFloat()/10;
        params.off_time = from_RS485.substring(7, 10).toFloat();
        params.off_time_micro = from_RS485.substring(10, 14).toFloat()/10;
        params.cycle = from_RS485.substring(14, 18).toFloat();
        params.voltage = from_RS485.substring(18, 22).toFloat();
        params.loopnum = from_RS485.substring(22, 26).toFloat();
        params.it_time = from_RS485.substring(26, 30).toFloat();
        params.spectra_interval = from_RS485.substring(30, 34).toFloat();

        start = true;
        }

      if (from_RS485.startsWith("end")) {
        start = false;
      }

      if (checkParametersChanged()) {
        updateLCD(); // Only update LCD when parameters change
      }
    }
  }

  if (start){
    params.val = digitalRead(12);

    if (params.val > 0) {
        lcd.setCursor(4, 0);
        lcd.write((char)255);
        lcd.setCursor(9, 0);
        lcd.print(" ");

        for (int j = 0; j < params.cycle; j++) {
          digitalWrite(13, HIGH);
          delay(params.on_time);
          delayMicroseconds(params.on_time_micro);
          digitalWrite(13, LOW);
          delay(params.off_time);
        }

        lcd.setCursor(9, 0);
        lcd.write((char)255);
        lcd.setCursor(4, 0);
        lcd.print(" ");
      }
    delayMicroseconds(100);
  }
}
