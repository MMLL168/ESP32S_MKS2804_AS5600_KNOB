# ESP32S AS5600 旋鈕控制器 — 開發日誌

---

## 2026-04-08 16:00 — 初版重寫：src/main.cpp

### 原因
原始代碼為最小可運行的骨架，存在以下問題：
1. 無 AS5600 初始化與磁鐵狀態檢查，感測器異常時無從診斷
2. 角度以整數（`angle * 10`）發送，精度不足，且與 SimpleFOC Commander 預期的浮點弧度格式不符
3. 無 I2C 錯誤處理，Wire 讀取失敗時程式繼續執行，產生錯誤角度值
4. 無多圈角度追蹤，旋鈕跨越 0°/360° 時目標角度會跳變
5. ESC 回應未讀取，無法確認指令是否被接收

### 處理方式
| 項目 | 做法 |
|------|------|
| AS5600 初始化 | 讀取 STATUS 暫存器（0x0B），檢查 MD/ML/MH 位元，印出磁場狀態警告 |
| 發送格式 | 改為 `T<弧度 float, 4位小數>\n`，符合 SimpleFOC Commander 標準 |
| I2C 錯誤處理 | `Wire.endTransmission()` 及 `Wire.available()` 回傳值檢查，失敗標記 `as5600Ok=false` 並每 2 秒重試 |
| 多圈追蹤 | 以前後 raw 差值偵測跨越（差值 > 2048 視為反向跨越），累積弧度；以 `#define MULTI_TURN` 切換 |
| ESC 回應 | `while (Serial2.available())` 透傳到 USB Serial |
| 雙向透傳 | USB Serial 輸入也透傳到 ESC，方便手動發 SimpleFOC 指令除錯 |
| 除錯輸出頻率 | 發送仍為 100Hz，但 Serial 印出限制 5Hz（200ms），避免刷屏影響時序 |

### 相關設定說明
- `CMD_PREFIX "T"`：ESC 端若用 `commander.add('M', ...)` 需改為 `"MT"`
- `MULTI_TURN false`：預設單圈；改為 `true` 可追蹤超過一圈的累積位置
- ESC 端必須設定 `motor.controller = MotionControlType::angle`

---

## 2026-04-08 16:30 — platformio.ini 加入 COM32

### 原因
預設未指定上傳/監控 port，PlatformIO 自動偵測有時會選錯 port。

### 處理方式
新增三行：
- `upload_port = COM32`
- `monitor_port = COM32`
- `monitor_speed = 115200`（與 `Serial.begin(115200)` 一致）

---

## 2026-04-08 18:00 — 改為 PWM 輸出（src/main.cpp 全改）

### 原因
B-G431B-ESC1 的 UART2 與 STLink 虛擬串口皆被佔用，無法使用 UART 傳送角度目標。改用 PWM 訊號控制。

### 處理方式
- 移除 UART2 (Serial2) 相關所有程式碼
- 加入 ESP32 LEDC PWM 輸出，GPIO17 → ESC PWM 腳
- 頻率 400Hz（週期 2500us），16-bit 解析度
- 角度映射：0 rad → 1000us，2π rad → 2000us
- 除錯輸出格式：`Raw=XXXX | XXX.XX deg | X.XXXX rad | PW=XXXX.XX us`

---

## 2026-04-08 17:30 — sendAngle 改用 printf 明確格式

### 原因
原用 `Serial2.print()` + `Serial2.println()` 隱式換行，格式不明確；B-G431B-ESC1 接收端要求嚴格的 `T%.4f\r\n` 格式。

### 處理方式
- `sendAngle()` 改為 `Serial2.printf("T%.4f\r\n", rad)`，格式完全符合規格
- 加入範圍 clamp：`0.0f` ~ `TWO_PI`，防止超出 0~6.2832 範圍
- 移除不再使用的 `#define CMD_PREFIX`

---

## 2026-04-08 17:00 — 修正 intelhex 缺失，燒錄成功

### 原因
esptool 4.9.0 在 PlatformIO 虛擬環境缺少 `intelhex` Python 套件，導致 bootloader 打包步驟失敗。程式碼本身無誤，編譯已正常進行。

### 處理方式
執行：`C:\Users\marlonwu\.platformio\penv\Scripts\pip.exe install intelhex`  
安裝 intelhex 2.3.0 後重新 Upload，燒錄成功。

### 燒錄結果
- 晶片：ESP32-D0WD-V3 @ 240MHz，MAC `78:42:1c:22:c5:90`
- Flash：22%（288KB），RAM：6.7%（21KB）

---

## 2026-04-08 16:45 — 修正編譯錯誤：Serial/Serial2 not declared

### 原因
PlatformIO 不像 Arduino IDE 會自動注入 `Arduino.h`，導致 `Serial`、`Serial2`、`delay` 等 Arduino 內建符號在編譯時找不到宣告。  
錯誤訊息：`error: 'Serial' was not declared in this scope`

### 處理方式
在 `src/main.cpp` 第一行加入 `#include <Arduino.h>`，讓所有 Arduino 內建 API 正確引入。

---
