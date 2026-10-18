// Прошивка автополива: интерфейс в стиле e-paper (ESP32-S3 + ST7735 1.77", 160x128)
// Библиотеки (Менеджер библиотек): LovyanGFX, Adafruit AHTX0
// Adafruit ST7735 и Adafruit GFX для этой прошивки больше не нужны.

#define LGFX_USE_V1
#include <LovyanGFX.hpp>
#include <WiFi.h>
#include <HTTPClient.h>
#include <Wire.h>
#include <Adafruit_AHTX0.h>
#include <utility>

// ===== Wi-Fi и сервер =====
// WIFI_SSID, WIFI_PASS, SERVER, API_TOKEN — в secrets.h (не в репозитории).
// Скопируйте secrets.example.h в secrets.h и впишите свои значения.
#if __has_include("secrets.h")
#include "secrets.h"
#else
#warning "secrets.h не найден, используются значения из secrets.example.h"
#include "secrets.example.h"
#endif
const char* DEVICE_ID  = "plant1";
const unsigned long SEND_INTERVAL = 30000;

// ===== Пины =====
#define PIN_MIC     1     // Out микрофона (Gain -> 3V3)
#define PIN_SOIL_D  2     // D0 датчика почвы
#define PIN_SOIL    4     // A0 датчика почвы
#define PIN_RELAY   5     // IN реле
#define RELAY_ON    LOW   // если реле работает наоборот — поменяйте на HIGH
#define RELAY_OFF   (!RELAY_ON)
// Дисплей: CS 10, SDA 11, SCK 12, RS 13, RES 14 — задаются в классе LGFX ниже

// ===== Микрофон =====
int CLAP_THRESHOLD = 2000;
const unsigned long CLAP_GAP    = 150;
const unsigned long CLAP_WAIT   = 600;
const unsigned long CLAP_MAX_MS = 150;

// ===== Полив =====
const unsigned long WATER_MS    = 3000;
const unsigned long COOLDOWN_MS = 60000;
const bool AUTO_WATER = true;

int soilDryCal = 4000;
int soilWetCal = 1150;
const int SOIL_LOW_PERCENT = 30;

// ===== Экран =====
const unsigned long MIC_INTERVAL   = 100;      // точка графика микрофона каждые 100 мс
const int MIC_N = 48;                          // 48 точек ≈ 5 секунд (график в шапке)
const int MIC_SCALE = 3000;                    // верх шкалы графика

// ---------- Настройка дисплея ST7735S 128x160 ----------
class LGFX : public lgfx::LGFX_Device {
  lgfx::Panel_ST7735S _panel;
  lgfx::Bus_SPI       _bus;
public:
  LGFX() {
    {
      auto cfg = _bus.config();
      cfg.spi_host   = SPI2_HOST;
      cfg.spi_mode   = 0;
      cfg.freq_write = 27000000;   // если на экране мусор — уменьшите до 16000000
      cfg.freq_read  = 16000000;
      cfg.spi_3wire  = true;
      cfg.use_lock   = true;
      cfg.dma_channel = SPI_DMA_CH_AUTO;
      cfg.pin_sclk = 12;
      cfg.pin_mosi = 11;
      cfg.pin_miso = -1;
      cfg.pin_dc   = 13;
      _bus.config(cfg);
      _panel.setBus(&_bus);
    }
    {
      auto cfg = _panel.config();
      cfg.pin_cs   = 10;
      cfg.pin_rst  = 14;
      cfg.pin_busy = -1;
      cfg.panel_width   = 128;
      cfg.panel_height  = 160;
      cfg.memory_width  = 128;     // размер памяти контроллера = размеру экрана,
      cfg.memory_height = 160;     // иначе картинка «съезжает» по диагонали
      cfg.offset_x = 0;            // если по краю полоса мусора — попробуйте 2
      cfg.offset_y = 0;            // ... и 1
      cfg.offset_rotation = 0;
      cfg.readable  = false;
      cfg.invert    = false;       // если белое и чёрное поменялись местами — true
      cfg.rgb_order = false;       // если красный и синий поменялись местами — true
      cfg.dlen_16bit = false;
      cfg.bus_shared = false;
      _panel.config(cfg);
    }
    setPanel(&_panel);
  }
};

LGFX lcd;
LGFX_Sprite canvas(&lcd);   // кадр рисуется в памяти и выводится целиком — без мерцания

uint16_t C_BG, C_INK, C_MUTED, C_FAINT;

Adafruit_AHTX0 aht;
bool ahtOk = false;

SemaphoreHandle_t adcMutex;

volatile int clapCount = 0;
volatile unsigned long lastClap = 0;
volatile int micPeak = 0;

unsigned long lastWater = 0;
bool everWatered = false;

float curTemp = NAN, curHum = NAN;
int   curSoil = -1;
int   soilRaw = 0, soilSpread = 0;
bool  soilDry = false;

bool wifiOk = false, serverOk = false;


String toastText;
unsigned long toastUntil = 0;

uint16_t micHist[MIC_N];
int micCount = 0, micHead = 0;

// ===== История уровня микрофона для графика =====
void micHistPush(int v) {
  micHist[micHead] = v;
  micHead = (micHead + 1) % MIC_N;
  if (micCount < MIC_N) micCount++;
}

int micHistGet(int i) {   // i = 0 — самая старая точка
  int start = (micHead - micCount + MIC_N) % MIC_N;
  return micHist[(start + i) % MIC_N];
}

// ===== Микрофон =====
int micLevel(int ms) {
  int mn = 4095, mx = 0;
  xSemaphoreTake(adcMutex, portMAX_DELAY);
  unsigned long t = millis();
  while (millis() - t < ms) {
    int v = analogRead(PIN_MIC);
    if (v < mn) mn = v;
    if (v > mx) mx = v;
  }
  xSemaphoreGive(adcMutex);
  return mx - mn;
}

void micTask(void *) {
  bool loud = false;
  unsigned long loudStart = 0;
  for (;;) {
    int lvl = micLevel(10);
    if (lvl > micPeak) micPeak = lvl;
    unsigned long now = millis();

    if (!loud && lvl > CLAP_THRESHOLD) {
      loud = true;
      loudStart = now;
    } else if (loud && lvl < CLAP_THRESHOLD / 2) {
      loud = false;
      unsigned long dur = now - loudStart;
      if (dur < CLAP_MAX_MS && now - lastClap > CLAP_GAP) {
        clapCount++;
        lastClap = now;
        Serial.printf("Хлопок! (%lu мс)\n", dur);
      }
    }
    vTaskDelay(1);
  }
}

// ===== Датчик почвы =====
int readSoilPercent() {
  const int N = 31;
  int v[N];
  xSemaphoreTake(adcMutex, portMAX_DELAY);
  for (int i = 0; i < N; i++) {
    v[i] = analogRead(PIN_SOIL);
    delayMicroseconds(200);
  }
  xSemaphoreGive(adcMutex);

  for (int i = 1; i < N; i++) {
    int x = v[i], j = i - 1;
    while (j >= 0 && v[j] > x) { v[j + 1] = v[j]; j--; }
    v[j + 1] = x;
  }
  soilRaw = v[N / 2];
  soilSpread = v[N - 1] - v[0];

  if (soilSpread > 800) return -1;
  return constrain(map(soilRaw, soilDryCal, soilWetCal, 0, 100), 0, 100);
}

bool needWater(int soilPercent) {
  if (soilPercent >= 0) return soilPercent < SOIL_LOW_PERCENT;
  return soilDry;
}

// ===== Элементы интерфейса =====
void toast(const String& s, unsigned long ms = 2500) {
  toastText = s;
  toastUntil = millis() + ms;
}

void drawWifiIcon(int x, int y, bool ok) {   // x, y — нижняя точка значка
  uint16_t c = ok ? C_INK : C_FAINT;
  canvas.fillArc(x, y, 8, 10, 225, 315, c);
  canvas.fillArc(x, y, 4, 6, 225, 315, c);
  canvas.fillCircle(x, y, 1, c);
  if (!ok) canvas.drawLine(x - 7, y - 9, x + 7, y + 1, C_INK);
}

void drawDrop(int cx, int cy, int r, uint16_t c) {
  canvas.fillCircle(cx, cy, r, c);
  canvas.fillTriangle(cx - r + 1, cy - r / 3, cx + r - 1, cy - r / 3, cx, cy - r * 2, c);
}

// ----- Сглаженная линия (алгоритм Ву): края рисуются полупрозрачными пикселями -----
uint16_t blend565(uint16_t bg, uint16_t fg, float a) {
  if (a <= 0) return bg;
  if (a >= 1) return fg;
  int br = (bg >> 11) & 0x1F, bgc = (bg >> 5) & 0x3F, bb = bg & 0x1F;
  int fr = (fg >> 11) & 0x1F, fgc = (fg >> 5) & 0x3F, fb = fg & 0x1F;
  int r = br + (fr - br) * a, g = bgc + (fgc - bgc) * a, b = bb + (fb - bb) * a;
  return (r << 11) | (g << 5) | b;
}

void plotAA(int x, int y, float a, uint16_t c) {
  if (x < 0 || y < 0 || x >= canvas.width() || y >= canvas.height()) return;
  canvas.drawPixel(x, y, blend565(canvas.readPixel(x, y), c, a));
}

void drawLineAA(float x0, float y0, float x1, float y1, uint16_t c) {
  bool steep = fabsf(y1 - y0) > fabsf(x1 - x0);
  if (steep) { std::swap(x0, y0); std::swap(x1, y1); }
  if (x0 > x1) { std::swap(x0, x1); std::swap(y0, y1); }
  float dx = x1 - x0, dy = y1 - y0;
  float grad = (dx == 0) ? 1 : dy / dx;
  float y = y0;
  for (int x = (int)roundf(x0); x <= (int)roundf(x1); x++) {
    int yi = (int)floorf(y);
    float f = y - yi;
    if (steep) { plotAA(yi, x, 1 - f, c); plotAA(yi + 1, x, f, c); }
    else       { plotAA(x, yi, 1 - f, c); plotAA(x, yi + 1, f, c); }
    y += grad;
  }
}

// Гладкая кривая через точки (сплайн Катмулла — Рома)
float catmull(float p0, float p1, float p2, float p3, float t) {
  return 0.5f * (2 * p1 + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t +
                 (-p0 + 3 * p1 - 3 * p2 + p3) * t * t * t);
}

// Маленький график звука в шапке
void drawSparkline(int x0, int y0, int w, int h) {
  const int P = 48;                      // последние 48 точек ≈ 5 секунд
  int n = min(micCount, P);
  int base = y0 + h - 1;
  if (n < 4) {
    drawLineAA(x0, base, x0 + w - 1, base, C_FAINT);
    return;
  }

  // Сглаживание и нелинейная шкала (тихие звуки тоже видно)
  float v[P];
  float s = micHistGet(micCount - n);
  for (int i = 0; i < n; i++) {
    s = s * 0.55f + micHistGet(micCount - n + i) * 0.45f;
    v[i] = sqrtf(constrain(s, 0, MIC_SCALE) / (float)MIC_SCALE);
  }

  float px = x0, py = base;
  for (int x = 0; x < w; x++) {
    float t = x * (n - 1) / (float)(w - 1);
    int i = (int)t;
    float f = t - i;
    float p0 = v[max(i - 1, 0)], p1 = v[i], p2 = v[min(i + 1, n - 1)], p3 = v[min(i + 2, n - 1)];
    float val = constrain(catmull(p0, p1, p2, p3, f), 0.0f, 1.0f);
    float y = base - val * (h - 1);
    if (x > 0) {                         // два прохода — линия толщиной ~1.6 пикселя
      drawLineAA(px, py, x0 + x, y, C_INK);
      drawLineAA(px, py + 0.6f, x0 + x, y + 0.6f, C_INK);
    }
    px = x0 + x;
    py = y;
  }
}

void drawHeader(const char* title) {
  if (title && title[0]) {
    canvas.setTextDatum(textdatum_t::top_left);
    canvas.setFont(&fonts::FreeSansBold9pt7b);
    canvas.setTextColor(C_INK);
    canvas.drawString(title, 6, 2);
  } else {
    drawSparkline(6, 3, 126, 15);      // вместо названия — график звука
  }
  drawWifiIcon(148, 15, wifiOk && serverOk);
  canvas.fillRect(6, 21, 148, 2, C_INK);
}

// Подпись: мелкий шрифт, знак градуса рисуется отдельно
void drawLabel(int x, int y, const char* text, bool degree, const char* unit, uint16_t color) {
  canvas.setFont(&fonts::Font0);
  canvas.setTextDatum(textdatum_t::top_left);
  canvas.setTextColor(color);
  canvas.drawString(text, x, y);
  int cx = x + canvas.textWidth(text);
  if (degree) {
    canvas.drawCircle(cx + 2, y + 1, 1, color);
    cx += 4;
  }
  canvas.drawString(unit, cx, y);
}

void drawCell(int x, int y, int w, int h, const char* label, bool degree, const char* unit,
              const String& value, bool alert) {
  uint16_t fg = C_INK, mu = C_MUTED;
  if (alert) {
    canvas.fillRoundRect(x + 2, y + 1, w - 4, h - 2, 4, C_INK);
    fg = C_BG;
    mu = C_BG;
  }
  drawLabel(x + 6, y + 5, label, degree, unit, mu);

  // Крупное значение; если не помещается — шрифт поменьше
  canvas.setTextDatum(textdatum_t::bottom_left);
  canvas.setTextColor(fg);
  canvas.setFont(&fonts::FreeSansBold18pt7b);
  if (canvas.textWidth(value) > w - 10) canvas.setFont(&fonts::FreeSansBold12pt7b);
  canvas.drawString(value, x + 5, y + h - 4);
}

String agoText() {
  if (!everWatered) return "--";
  unsigned long m = (millis() - lastWater) / 60000;
  if (m < 1) return "now";
  if (m < 60) return String(m) + "m";
  if (m < 48 * 60) return String(m / 60) + "h";
  return String(m / 1440) + "d";
}

void drawMainScreen() {
  drawHeader("");                    // в шапке — маленький график звука

  const int top = 25, w = 80, h = 51;

  // Почва
  bool dry = needWater(curSoil);
  if (curSoil >= 0) {
    drawCell(0, top, w, h, "Soil, %", false, "", String(curSoil), dry);
  } else {
    drawCell(0, top, w, h, "Soil (D0)", false, "", soilDry ? "DRY" : "WET", dry);
  }

  // Температура
  drawCell(w, top, w, h, "Temp, ", true, "C",
           isnan(curTemp) ? String("--") : String(curTemp, 1), false);

  // Влажность воздуха
  drawCell(0, top + h + 1, w, h, "Air RH, %", false, "",
           isnan(curHum) ? String("--") : String((int)round(curHum)), false);

  // Последний полив
  drawCell(w, top + h + 1, w, h, "Watered", false, "", agoText(), false);
}

void drawToast() {
  if (millis() > toastUntil || toastText.length() == 0) return;
  canvas.fillRect(0, 108, 160, 20, C_INK);
  canvas.setFont(&fonts::Font2);
  canvas.setTextDatum(textdatum_t::middle_center);
  canvas.setTextColor(C_BG);
  canvas.drawString(toastText, 80, 118);
}

void renderFrame() {
  canvas.fillScreen(C_BG);
  drawMainScreen();
  drawToast();
  canvas.pushSprite(0, 0);
}

void renderWatering(const String& reason) {
  canvas.fillScreen(C_BG);
  drawDrop(80, 58, 14, C_INK);
  canvas.setFont(&fonts::FreeSansBold12pt7b);
  canvas.setTextDatum(textdatum_t::top_center);
  canvas.setTextColor(C_INK);
  canvas.drawString("Watering", 80, 80);
  canvas.setFont(&fonts::Font0);
  canvas.setTextColor(C_MUTED);
  canvas.drawString(reason, 80, 108);
  canvas.pushSprite(0, 0);
}

// ===== Связь с сервером =====
String numOrNull(float v, int digits) {
  return isnan(v) ? String("null") : String(v, digits);
}

int jsonInt(const String& s, const char* key, int def) {
  String k = String("\"") + key + "\":";
  int i = s.indexOf(k);
  if (i < 0) return def;
  String rest = s.substring(i + k.length());
  rest.trim();
  if (rest.startsWith("null")) return def;
  return rest.toInt();
}

bool postJson(const char* path, const String& body, String* response) {
  wifiOk = (WiFi.status() == WL_CONNECTED);
  if (!wifiOk) return false;
  HTTPClient http;
  http.setTimeout(3000);
  http.begin(String(SERVER) + path);
  http.addHeader("Content-Type", "application/json");
  if (strlen(API_TOKEN) > 0) http.addHeader("X-Api-Key", API_TOKEN);
  int code = http.POST(body);
  bool ok = (code == 200);
  if (ok && response) *response = http.getString();
  if (!ok) Serial.printf("Сервер: ошибка %d (%s)\n", code, path);
  http.end();
  return ok;
}

void sendEvent(const char* type, float value, const String& note) {
  String body = "{\"device\":\"" + String(DEVICE_ID) + "\",\"type\":\"" + type +
                "\",\"value\":" + numOrNull(value, 1) + ",\"note\":\"" + note + "\"}";
  postJson("/api/event", body, nullptr);
}

// ===== Полив =====
void water(String reason, unsigned long ms = WATER_MS) {
  Serial.printf(">>> Полив: %s, %lu мс\n", reason.c_str(), ms);
  renderWatering(reason);
  digitalWrite(PIN_RELAY, RELAY_ON);
  delay(ms);
  digitalWrite(PIN_RELAY, RELAY_OFF);
  lastWater = millis();
  everWatered = true;
  clapCount = 0;
  toast("Watering done");
  sendEvent("water", ms / 1000.0, reason);
}

void sendTelemetry() {
  String body = "{";
  body += "\"device\":\"" + String(DEVICE_ID) + "\",";
  body += "\"temp\":" + numOrNull(curTemp, 2) + ",";
  body += "\"air_hum\":" + numOrNull(curHum, 2) + ",";
  body += "\"soil_raw\":" + String(soilRaw) + ",";
  body += "\"soil_pct\":" + String(curSoil) + ",";
  body += "\"soil_ok\":" + String(curSoil >= 0 ? "true" : "false") + ",";
  body += "\"soil_dry\":" + String(soilDry ? "true" : "false");
  body += "}";

  String resp;
  serverOk = postJson("/api/telemetry", body, &resp);
  if (!serverOk) return;

  int dry = jsonInt(resp, "dry", soilDryCal);
  int wet = jsonInt(resp, "wet", soilWetCal);
  if (dry != wet && (dry != soilDryCal || wet != soilWetCal)) {
    soilDryCal = dry;
    soilWetCal = wet;
    Serial.printf("Новая калибровка: сухо=%d, мокро=%d\n", dry, wet);
    toast("Calibration updated");
  }

  if (resp.indexOf("\"command\":\"water\"") >= 0) {
    int sec = constrain(jsonInt(resp, "seconds", 3), 1, 10);
    water("web", sec * 1000UL);
  }
}

// ===== Датчики =====
void updateSensors() {
  if (ahtOk) {
    sensors_event_t he, te;
    aht.getEvent(&he, &te);
    curTemp = te.temperature;
    curHum = he.relative_humidity;
  }

  curSoil = readSoilPercent();
  soilDry = digitalRead(PIN_SOIL_D) == HIGH;   // если D0 показывает наоборот — поменяйте на LOW

  Serial.printf("Темп: %.1f C | Воздух: %.1f%% | Почва: %d%% (raw %d) | D0: %s\n",
                curTemp, curHum, curSoil, soilRaw, soilDry ? "сухо" : "мокро");

  bool cooldownDone = !everWatered || millis() - lastWater > COOLDOWN_MS;
  if (AUTO_WATER && needWater(curSoil) && cooldownDone) {
    water(curSoil < 0 ? "auto-D0" : "auto");
  }
}

// ===== Команды хлопками =====
void runCommand(int claps) {
  if (claps == 1) {
    toast("Hello! I am listening");          // 1 хлопок — приветствие
  } else if (claps == 2) {
    water("claps");                           // 2 хлопка — полив
  } else {
    toast("Unknown: " + String(claps) + " claps");
  }
}

void connectWiFi() {
  toast("Connecting WiFi...", 15000);
  renderFrame();
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 10000) delay(200);

  wifiOk = (WiFi.status() == WL_CONNECTED);
  if (wifiOk) {
    Serial.print("Wi-Fi подключён, IP: ");
    Serial.println(WiFi.localIP());
    toast("WiFi " + WiFi.localIP().toString());
  } else {
    Serial.println("Wi-Fi не подключился, работаем без сервера");
    toast("Offline mode");
  }
}

void setup() {
  digitalWrite(PIN_RELAY, RELAY_OFF);
  pinMode(PIN_RELAY, OUTPUT);

  Serial.begin(115200);
  delay(1000);

  adcMutex = xSemaphoreCreateMutex();

  pinMode(PIN_SOIL_D, INPUT);
  analogSetPinAttenuation(PIN_MIC, ADC_11db);
  analogSetPinAttenuation(PIN_SOIL, ADC_11db);

  // Дисплей
  lcd.init();
  lcd.setRotation(3);                 // горизонтально, 160x128 (если вверх ногами — поставьте 1)
  canvas.setColorDepth(16);
  canvas.createSprite(lcd.width(), lcd.height());
  C_BG    = canvas.color565(244, 243, 238);   // «бумажный» фон
  C_INK   = canvas.color565(18, 18, 18);      // «чернила»
  C_MUTED = canvas.color565(105, 105, 100);   // подписи
  C_FAINT = canvas.color565(190, 190, 185);   // сетка графика

  Wire.begin(8, 9);
  ahtOk = aht.begin();
  if (!ahtOk) Serial.println("AHT20 не найден");

  connectWiFi();

  xTaskCreatePinnedToCore(micTask, "mic", 4096, NULL, 1, NULL, 0);

  updateSensors();
  sendTelemetry();
  sendEvent("boot", 0, "start");
}

void loop() {
  if (Serial.available() && Serial.read() == 'w') water("manual");

  if (clapCount > 0 && millis() - lastClap > CLAP_WAIT) {
    int n = clapCount;
    clapCount = 0;
    runCommand(n);
  }

  static unsigned long lastSensors = 0;
  if (millis() - lastSensors > 2000) {
    updateSensors();
    lastSensors = millis();
  }

  // Точка графика микрофона: самый громкий звук за последние 100 мс
  static unsigned long lastMic = 0;
  if (millis() - lastMic >= MIC_INTERVAL) {
    micHistPush(micPeak);
    micPeak = 0;
    lastMic = millis();
  }

  static unsigned long lastSend = 0;
  if (millis() - lastSend > SEND_INTERVAL) {
    sendTelemetry();
    lastSend = millis();
  }

  // Кадр 10 раз в секунду, чтобы график звука двигался плавно
  static unsigned long lastFrame = 0;
  if (millis() - lastFrame >= 100) {
    renderFrame();
    lastFrame = millis();
  }
}
