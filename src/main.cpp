/**
 * ESP32S Knob Controller → B-G431B-ESC1 (PWM)
 *
 * ESP32S 讀取 AS5600 旋鈕角度，輸出 PWM 給 B-G431B-ESC1 PWM 腳。
 *
 * 接線：
 *   GPIO21 → AS5600 SDA
 *   GPIO22 → AS5600 SCL
 *   GPIO17 → ESC PWM 腳
 *   GND    → ESC GND（共地）
 *
 * PWM 規格：
 *   頻率：400Hz（週期 2500us）
 *   1000us pulse → 0 rad  (0°)
 *   2000us pulse → 2π rad (360°)
 */

#include <Arduino.h>
#include <Wire.h>

// ── 腳位定義 ─────────────────────────────────────────────────
#define SDA_PIN     21
#define SCL_PIN     22
#define PWM_PIN     17   // → ESC PWM 腳

// ── PWM 參數 ──────────────────────────────────────────────────
#define PWM_CHANNEL  0
#define PWM_FREQ     400    // Hz，週期 = 2500us
#define PWM_RES_BITS 16     // 16-bit → 0~65535
// 週期 2500us，1us = 65535/2500 = 26.214 counts
#define PW_MIN_US    1000   // 0 rad
#define PW_MAX_US    2000   // 2π rad

// ── AS5600 暫存器 ─────────────────────────────────────────────
#define AS5600_ADDR     0x36
#define REG_STATUS      0x0B
#define REG_ANGLE_H     0x0E

// ── 參數 ─────────────────────────────────────────────────────
#define READ_INTERVAL_MS  10     // 100Hz
#define I2C_CLOCK         400000

// ── 全域狀態 ─────────────────────────────────────────────────
static bool     as5600Ok  = false;
static uint32_t lastMs    = 0;

// ── AS5600 ────────────────────────────────────────────────────

uint16_t as5600ReadReg(uint8_t reg) {
  Wire.beginTransmission(AS5600_ADDR);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return 0xFFFF;
  Wire.requestFrom((uint8_t)AS5600_ADDR, (uint8_t)2);
  if (Wire.available() < 2) return 0xFFFF;
  return ((uint16_t)Wire.read() << 8 | Wire.read()) & 0x0FFF;
}

uint8_t as5600ReadByte(uint8_t reg) {
  Wire.beginTransmission(AS5600_ADDR);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return 0xFF;
  Wire.requestFrom((uint8_t)AS5600_ADDR, (uint8_t)1);
  return Wire.available() ? Wire.read() : 0xFF;
}

bool as5600Init() {
  Wire.beginTransmission(AS5600_ADDR);
  if (Wire.endTransmission() != 0) {
    Serial.println("[AS5600] I2C not found (0x36)");
    return false;
  }
  uint8_t st = as5600ReadByte(REG_STATUS);
  bool md = (st >> 3) & 1;
  bool ml = (st >> 4) & 1;
  bool mh = (st >> 5) & 1;
  Serial.printf("[AS5600] STATUS=0x%02X  MD=%d ML=%d MH=%d\r\n", st, md, ml, mh);
  if (!md) Serial.println("[AS5600] WARN: no magnet detected");
  if (ml)  Serial.println("[AS5600] WARN: magnet too weak");
  if (mh)  Serial.println("[AS5600] WARN: magnet too strong");
  return true;
}

// ── PWM ───────────────────────────────────────────────────────

/**
 * 弧度 → LEDC duty
 *   rad 0~2π → pulse 1000~2000us → duty 0~65535
 */
uint32_t radToDuty(float rad) {
  if (rad < 0.0f)    rad = 0.0f;
  if (rad > TWO_PI)  rad = TWO_PI;
  float pw_us   = PW_MIN_US + (rad / TWO_PI) * (PW_MAX_US - PW_MIN_US);
  float period  = 1000000.0f / PWM_FREQ;           // 2500us
  uint32_t duty = (uint32_t)(pw_us / period * 65535.0f);
  return duty;
}

// ── setup ─────────────────────────────────────────────────────

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("\r\n=== ESP32S Knob Controller (PWM) ===");

  // LEDC PWM
  ledcSetup(PWM_CHANNEL, PWM_FREQ, PWM_RES_BITS);
  ledcAttachPin(PWM_PIN, PWM_CHANNEL);
  ledcWrite(PWM_CHANNEL, radToDuty(0));   // 初始 1000us（0°）
  Serial.printf("PWM  started (GPIO%d) @ %dHz  1000~2000us\r\n", PWM_PIN, PWM_FREQ);

  // I2C
  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(I2C_CLOCK);
  Serial.printf("I2C  started (SDA=GPIO%d SCL=GPIO%d) @ %dkHz\r\n",
                SDA_PIN, SCL_PIN, I2C_CLOCK / 1000);

  as5600Ok = as5600Init();
  if (!as5600Ok) Serial.println("[ERROR] AS5600 init failed, retrying...");

  Serial.println("Mode: single-turn PWM, 100Hz");
}

// ── loop ──────────────────────────────────────────────────────

void loop() {
  uint32_t now = millis();

  // 重試 AS5600
  if (!as5600Ok && (now % 2000 < 20)) {
    as5600Ok = as5600Init();
  }

  if (as5600Ok && (now - lastMs >= READ_INTERVAL_MS)) {
    lastMs = now;

    uint16_t raw = as5600ReadReg(REG_ANGLE_H);
    if (raw == 0xFFFF) {
      Serial.println("[AS5600] read error");
      as5600Ok = false;
      return;
    }

    float rad  = raw * (TWO_PI / 4096.0f);
    float deg  = raw * (360.0f  / 4096.0f);
    float pw   = PW_MIN_US + (rad / TWO_PI) * (PW_MAX_US - PW_MIN_US);

    ledcWrite(PWM_CHANNEL, radToDuty(rad));

    // 除錯輸出 5Hz
    static uint32_t lastPrint = 0;
    if (now - lastPrint >= 200) {
      lastPrint = now;
      Serial.printf("Raw=%4d | %6.2f deg | %6.4f rad | PW=%7.2f us\r\n",
                    raw, deg, rad, pw);
    }
  }
}
