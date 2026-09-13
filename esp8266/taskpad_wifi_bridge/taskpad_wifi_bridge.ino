/*
 * TaskPad51 WiFi UART tunnel + SSD1306 task detail OLED
 * UART 9600 <-> TCP :8870 ; OLED| lines stay on ESP (not forwarded to STC)
 * OLED I2C: SDA=D2, SCL=D1, VDD=3V3
 */
#include <ESP8266WiFi.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include "secrets.h"

#define SCREEN_W 128
#define SCREEN_H 64
#define OLED_ADDR 0x3C

Adafruit_SSD1306 display(SCREEN_W, SCREEN_H, &Wire, -1);

WiFiClient client;
unsigned long lastReconnectMs = 0;
unsigned long lastStatusMs = 0;
unsigned long lastUiMs = 0;

String oDir = "-";
String oModel = "-";
String oCtx = "-";
String oStatus = "boot";

char tcpLine[160];
size_t tcpLen = 0;

void oledDraw() {
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 0);
  display.println(F("TaskPad detail"));
  display.print(F("dir "));
  display.println(oDir);
  display.print(F("mdl "));
  display.println(oModel);
  display.print(F("ctx "));
  display.println(oCtx);
  display.print(F("st  "));
  display.println(oStatus);
  if (WiFi.status() == WL_CONNECTED) {
    display.print(F("ip  "));
    display.println(WiFi.localIP());
  } else {
    display.println(F("ip  (no wifi)"));
  }
  display.display();
}

void setStatus(const char *s) {
  oStatus = s;
  oledDraw();
}

void connectWifi() {
  if (WiFi.status() == WL_CONNECTED) return;
  setStatus("wifi...");
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 20000) {
    delay(250);
  }
  if (WiFi.status() == WL_CONNECTED) {
    setStatus("wifi ok");
  } else {
    setStatus("wifi fail");
  }
}

bool connectBridge() {
  if (client.connected()) return true;
  if (millis() - lastReconnectMs < 1500) return false;
  lastReconnectMs = millis();
  client.stop();
  setStatus("brdg...");
  if (!client.connect(BRIDGE_HOST, BRIDGE_PORT)) {
    setStatus("brdg fail");
    return false;
  }
  client.setNoDelay(true);
  client.print("WIFI|up\n");
  setStatus("brdg ok");
  return true;
}

void handleOledLine(const char *line) {
  // OLED|dir|text  OLED|mdl|text  OLED|ctx|text  OLED|st|text
  if (strncmp(line, "OLED|dir|", 9) == 0) {
    oDir = String(line + 9);
    if (oDir.length() > 18) oDir = oDir.substring(oDir.length() - 18);
  } else if (strncmp(line, "OLED|mdl|", 9) == 0) {
    oModel = String(line + 9);
    if (oModel.length() > 18) oModel = oModel.substring(0, 18);
  } else if (strncmp(line, "OLED|ctx|", 9) == 0) {
    oCtx = String(line + 9);
    if (oCtx.length() > 18) oCtx = oCtx.substring(0, 18);
  } else if (strncmp(line, "OLED|st|", 8) == 0) {
    oStatus = String(line + 8);
    if (oStatus.length() > 18) oStatus = oStatus.substring(0, 18);
  } else {
    return;
  }
  oledDraw();
}

void onTcpByte(char c) {
  if (c == '\r') return;
  if (c == '\n') {
    tcpLine[tcpLen] = 0;
    if (tcpLen > 0) {
      if (strncmp(tcpLine, "OLED|", 5) == 0) {
        handleOledLine(tcpLine);
      } else {
        Serial.print(tcpLine);
        Serial.print('\n');
      }
    }
    tcpLen = 0;
    return;
  }
  if (tcpLen + 1 < sizeof(tcpLine)) {
    tcpLine[tcpLen++] = c;
  } else {
    tcpLen = 0; // overflow: drop line
  }
}

void setup() {
  Serial.begin(UART_BAUD);
  delay(200);
  Wire.begin(D2, D1); // SDA, SCL
  if (!display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR)) {
    // continue without OLED
  } else {
    display.clearDisplay();
    display.display();
    setStatus("boot");
  }
  connectWifi();
}

void loop() {
  connectWifi();
  if (WiFi.status() != WL_CONNECTED) {
    delay(400);
    return;
  }

  if (!connectBridge()) {
    delay(200);
    return;
  }

  while (Serial.available() > 0 && client.connected()) {
    client.write((uint8_t)Serial.read());
  }
  while (client.available() > 0) {
    onTcpByte((char)client.read());
  }

  if (millis() - lastStatusMs > 10000) {
    lastStatusMs = millis();
    if (client.connected()) {
      client.print("WIFI|ping\n");
    }
  }

  if (millis() - lastUiMs > 5000) {
    lastUiMs = millis();
    oledDraw();
  }
}