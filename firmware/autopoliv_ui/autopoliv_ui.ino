// Прошивка автополива: интерфейс в стиле e-paper (ESP32-S3 + ST7735 1.77", 160x128)
// Библиотеки (Менеджер библиотек): LovyanGFX, Adafruit AHTX0
// Adafruit ST7735 и Adafruit GFX для этой прошивки больше не нужны.

#define LGFX_USE_V1
#include <LovyanGFX.hpp>
#include <WiFi.h>
#include <HTTPClient.h>
#include <Wire.h>
#include <Adafruit_AHTX0.h>
#include <Preferences.h>
#include <WebServer.h>
#include <DNSServer.h>
#include <ArduinoJson.h>
#include <freertos/stream_buffer.h>
#include "audio_out.h"   // голос (V2): настройки и вывод звука
#include "jarvis_sprite.h"   // персонаж «Джарвис» для экрана
#include <time.h>
#include <utility>

// ===== Wi-Fi и сервер =====
// Настройки хранятся в памяти устройства (Preferences) и задаются с телефона в режиме настройки.
// secrets.h (не в репозитории) необязателен: его значения используются, пока в памяти ничего нет.
#if __has_include("secrets.h")
#include "secrets.h"
#else
const char* WIFI_SSID  = "";
const char* WIFI_PASS  = "";
const char* SERVER     = "";
const char* API_TOKEN  = "";
#endif
const char* DEVICE_ID  = "plant1";
const char* FW_VERSION = "1.1.0";

// ===== Режим настройки =====
// Нет сети / не подключился за 10 с / 3 хлопка → точка доступа, форма на http://192.168.4.1
const char* AP_SSID = "Autopoliv-Setup";
const char* AP_PASS = "plant1234";
const unsigned long SETUP_RETRY_MS = 300000;   // без клиентов — раз в 5 мин снова пробуем подключиться
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
// Режим ml (D0): поливает сервер по прогнозу, локальный порог молчит — но только пока
// сервер на связи. Без ответа сервера дольше этого времени — снова локальный порог.
const unsigned long SERVER_ML_TIMEOUT_MS = 600000;

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
      cfg.rgb_order = true;        // у этой панели красный и синий переставлены (проверено: капли были красными)
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
volatile bool voiceBusy = false;   // идёт голосовой диалог: хлопки и замер почвы на паузе
volatile bool wakeTrigger = false; // V3: звучит речь — проверить, не «Джарвис» ли это
unsigned long wakeBackoffUntil = 0;

unsigned long lastWater = 0;
bool everWatered = false;
time_t lastWaterEpoch = 0;          // время последнего полива по часам (NTP), хранится в Preferences

// ===== Ответ сервера (F2: ArduinoJson) =====
int nextWaterH = -1;                // прогноз: через сколько часов полив (-1 — нет прогноза)
unsigned long nextWaterAt = 0;      // когда получен прогноз
int calibStage = 0;                 // 1–3 — идёт калибровка
bool demoMode = false;              // D1: сервер в демо-режиме — метка DEMO в шапке
const unsigned long NEXT_FRESH_MS = 600000;   // прогноз старше 10 мин (нет связи) не показываем

// ===== Голосовой помощник: состояние для полосы внизу экрана (F3, V2) =====
enum VoiceState { VS_IDLE, VS_LISTEN, VS_THINK, VS_SPEAK };
volatile VoiceState voiceState = VS_IDLE;

// ===== Офлайн-буфер телеметрии (F2) =====
// Сервер недоступен — замеры копятся в RAM и досылаются с полем age_s
struct Sample {
  unsigned long ms;
  float temp, hum;
  int raw, pct;
  bool dry;
};
const int BUF_N = 240;              // 240 × 30 с = 2 часа
Sample sampleBuf[BUF_N];
int bufHead = 0, bufCount = 0;
const int FLUSH_PER_SEND = 10;      // за раз досылаем не больше 10, чтобы не тормозить экран

float curTemp = NAN, curHum = NAN;
int   curSoil = -1;
int   soilRaw = 0, soilSpread = 0;
bool  soilDry = false;

bool wifiOk = false, serverOk = false;
bool serverMl = false;              // последний ответ сервера: режим ml
unsigned long serverMlAt = 0;

Preferences prefs;
WebServer web(80);
DNSServer dns;
String cfgSsid, cfgPass, cfgServer, cfgToken;
bool setupMode = false;
unsigned long setupSince = 0;
String scanItems;   // HTML-список сетей с последнего сканирования

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
  unsigned long speechStart = 0, speechLast = 0;   // V3: непрерывный звук — речь
  for (;;) {
    int lvl = micLevel(10);
    if (lvl > micPeak) micPeak = lvl;
    unsigned long now = millis();

    if (voiceBusy) {                  // во время записи/ответа хлопки не считаем
      loud = false;
      speechStart = 0;
      vTaskDelay(10);
      continue;
    }
    if (WAKE_ENABLED && VOICE_ENABLED) {
      if (lvl > WAKE_LEVEL) {
        if (!speechStart || now - speechLast > WAKE_GAP_MS) speechStart = now;
        speechLast = now;
        // Хлопок короче 150 мс; серию хлопков не трогаем — её разбирает runCommand
        if (now - speechStart >= WAKE_MIN_MS && now - lastClap > CLAP_WAIT && clapCount == 0) {
          wakeTrigger = true;
        }
      } else if (now - speechLast > WAKE_GAP_MS) {
        speechStart = 0;
      }
    }
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
  } else if (setupMode) {              // в режиме настройки — адрес формы
    canvas.setTextDatum(textdatum_t::top_left);
    canvas.setFont(&fonts::Font2);
    canvas.setTextColor(C_INK);
    canvas.drawString("Setup: " + WiFi.softAPIP().toString(), 6, 3);
  } else {
    drawSparkline(6, 3, demoMode ? 92 : 126, 15);   // вместо названия — график звука
  }
  if (demoMode) {                      // D1: демо-режим помечен на экране
    canvas.fillRoundRect(102, 4, 34, 14, 3, C_INK);
    canvas.setFont(&fonts::Font0);
    canvas.setTextDatum(textdatum_t::middle_center);
    canvas.setTextColor(C_BG);
    canvas.drawString("DEMO", 119, 11);
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

bool clockOk() { return time(nullptr) > 1700000000; }

String nextText(int hours);

String agoText() {
  unsigned long m;
  if (everWatered) m = (millis() - lastWater) / 60000;
  else if (lastWaterEpoch > 0 && clockOk()) m = (time(nullptr) - lastWaterEpoch) / 60;
  else return "--";
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

  // Следующий полив по прогнозу сервера; без связи — время с последнего полива, как раньше
  if (nextWaterH >= 0 && serverOk && millis() - nextWaterAt < NEXT_FRESH_MS) {
    drawCell(w, top + h + 1, w, h, "Next", false, "", nextText(nextWaterH), false);
  } else {
    drawCell(w, top + h + 1, w, h, "Watered", false, "", agoText(), false);
  }
}

String nextText(int hours) {
  if (hours <= 0) return "now";
  if (hours < 48) return String(hours) + "h";
  return String(hours / 24) + "d";
}

void drawBar(const String& text) {
  canvas.fillRect(0, 108, 160, 20, C_INK);
  canvas.setFont(&fonts::Font2);
  canvas.setTextDatum(textdatum_t::middle_center);
  canvas.setTextColor(C_BG);
  canvas.drawString(text, 80, 118);
}

// Полоса внизу: сообщение > голосовой помощник > калибровка
void drawToast() {
  if (millis() <= toastUntil && toastText.length() > 0) {
    drawBar(toastText);
  } else if (voiceState == VS_LISTEN) {
    drawBar("Listening...");
  } else if (voiceState == VS_THINK) {
    drawBar("Thinking...");
  } else if (voiceState == VS_SPEAK) {
    drawBar("Speaking...");
  } else if (calibStage >= 1 && calibStage <= 3) {
    drawBar("Calibrating " + String(calibStage) + "/3");
  }
}

// ===== Персонаж «Джарвис» =====
void drawJarvis(int x, int y, int scale, bool squint = false) {
  for (int j = 0; j < JARVIS_H; j++) {
    bool sq = squint && j >= JARVIS_SQUINT_FROM && j < JARVIS_SQUINT_FROM + 3;
    const char* row = sq ? JARVIS_SQUINT[j - JARVIS_SQUINT_FROM] : JARVIS[j];
    for (int i = 0; i < JARVIS_W; i++) {
      char ch = row[i];
      if (ch == '.') continue;
      const uint8_t* c = JARVIS_RGB[ch - 'a'];
      canvas.fillRect(x + i * scale, y + j * scale, scale, scale, canvas.color565(c[0], c[1], c[2]));
    }
  }
}

// Голосовой помощник работает — вместо датчиков персонаж, внизу полоса состояния
void drawJarvisScreen() {
  const int s = 6, w = JARVIS_W * s;
  int bob = (voiceState == VS_LISTEN && (millis() / 400) % 2) ? -2 : 0;   // «дышит», пока слушает
  drawJarvis((160 - w) / 2, 10 + bob, s);
}

void renderFrame() {
  canvas.fillScreen(C_BG);
  if (voiceState != VS_IDLE) drawJarvisScreen();
  else drawMainScreen();
  drawToast();
  canvas.pushSprite(0, 0);
}

// Полив: персонаж под падающими каплями (кадр зависит от времени — вызывать в цикле)
void renderWatering(const String& reason) {
  const int s = 5, w = JARVIS_W * s, x0 = (160 - w) / 2, y0 = 30;
  canvas.fillScreen(C_BG);
  drawJarvis(x0, y0, s, true);         // жмурится от брызг
  uint16_t blue = canvas.color565(70, 150, 230);
  unsigned long t = millis();
  const int cols[] = {x0 + 8, x0 + 30, x0 + 52, x0 + 70, x0 + 18, x0 + 60};
  for (int k = 0; k < 6; k++) {
    int y = (t / 6 + k * 23) % 60;                    // падают сверху до головы и дальше
    if (y > 32) continue;
    drawDrop(cols[k], y + 4, 3, blue);
  }
  drawBar("Watering: " + reason);
  canvas.pushSprite(0, 0);
}

// ===== Связь с сервером =====
String numOrNull(float v, int digits) {
  return isnan(v) ? String("null") : String(v, digits);
}

bool postJson(const char* path, const String& body, String* response) {
  wifiOk = (WiFi.status() == WL_CONNECTED);
  if (!wifiOk || cfgServer.length() == 0) return false;
  HTTPClient http;
  http.setTimeout(3000);
  http.begin(cfgServer + path);
  http.addHeader("Content-Type", "application/json");
  if (cfgToken.length() > 0) http.addHeader("X-Api-Key", cfgToken);
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
  unsigned long t0 = millis();
  while (millis() - t0 < ms) {         // пока работает насос — капли падают на персонажа
    renderWatering(reason);
    delay(30);
  }
  digitalWrite(PIN_RELAY, RELAY_OFF);
  lastWater = millis();
  everWatered = true;
  if (clockOk()) {                     // время полива переживает перезагрузку
    lastWaterEpoch = time(nullptr);
    prefs.begin("state", false);
    prefs.putLong64("water_ts", lastWaterEpoch);
    prefs.end();
  }
  clapCount = 0;
  toast("Watering done");
  sendEvent("water", ms / 1000.0, reason);
}

void handleResponse(const String& resp);
String telemetryJson(float temp, float hum, int raw, int pct, bool dry, long ageS);
void bufferSample();
void flushBuffer();

void sendTelemetry() {
  String body = telemetryJson(curTemp, curHum, soilRaw, curSoil, soilDry, 0);

  String resp;
  serverOk = postJson("/api/telemetry", body, &resp);
  if (!serverOk) {
    bufferSample();
    return;
  }
  handleResponse(resp);
  flushBuffer();
}

// Ответ на телеметрию (контракт 4.1): {"command":"water","seconds":3,"dry":3900,"wet":1300,…}
void handleResponse(const String& resp) {
  JsonDocument doc;
  if (deserializeJson(doc, resp)) {
    Serial.println("Сервер: не удалось разобрать ответ");
    return;
  }
  int dry = doc["dry"] | soilDryCal;
  int wet = doc["wet"] | soilWetCal;
  if (dry != wet && (dry != soilDryCal || wet != soilWetCal)) {
    soilDryCal = dry;
    soilWetCal = wet;
    Serial.printf("Новая калибровка: сухо=%d, мокро=%d\n", dry, wet);
    toast("Calibration updated");
  }

  bool ml = doc["mode"] == "ml";
  if (ml != serverMl) Serial.printf("Режим полива: %s\n", ml ? "ml (решает сервер)" : "порог");
  serverMl = ml;
  if (ml) serverMlAt = millis();

  calibStage = doc["calib_stage"] | 0;
  demoMode = doc["demo"] | false;
  nextWaterH = doc["next_water_h"] | -1;
  nextWaterAt = millis();

  if (doc["command"] == "water") {
    int sec = constrain(doc["seconds"] | 3, 1, 10);
    water("web", sec * 1000UL);
  }
}

String telemetryJson(float temp, float hum, int raw, int pct, bool dry, long ageS) {
  String body = "{";
  body += "\"device\":\"" + String(DEVICE_ID) + "\",";
  body += "\"temp\":" + numOrNull(temp, 2) + ",";
  body += "\"air_hum\":" + numOrNull(hum, 2) + ",";
  body += "\"soil_raw\":" + String(raw) + ",";
  body += "\"soil_pct\":" + String(pct) + ",";
  body += "\"soil_ok\":" + String(pct >= 0 ? "true" : "false") + ",";
  body += "\"soil_dry\":" + String(dry ? "true" : "false") + ",";
  if (ageS > 0) body += "\"age_s\":" + String(ageS) + ",";
  body += "\"uptime_s\":" + String(millis() / 1000) + ",";
  body += "\"fw_version\":\"" + String(FW_VERSION) + "\"";
  body += "}";
  return body;
}

void bufferSample() {
  sampleBuf[bufHead] = {millis(), curTemp, curHum, soilRaw, curSoil, soilDry};
  bufHead = (bufHead + 1) % BUF_N;
  if (bufCount < BUF_N) bufCount++;    // переполнение — затираем самые старые
}

// Досылка накопленного: от старых к новым; ответы на старые замеры не исполняем —
// по устаревшим данным сервер решений не принимает
void flushBuffer() {
  for (int k = 0; k < FLUSH_PER_SEND && bufCount > 0; k++) {
    int i = (bufHead - bufCount + BUF_N) % BUF_N;
    const Sample& s = sampleBuf[i];
    long age = (millis() - s.ms) / 1000;
    if (!postJson("/api/telemetry", telemetryJson(s.temp, s.hum, s.raw, s.pct, s.dry, age),
                  nullptr)) return;
    bufCount--;
  }
}

// Сервер в режиме ml и отвечал недавно — локальный автополив не нужен
bool serverDecides() {
  return serverMl && millis() - serverMlAt < SERVER_ML_TIMEOUT_MS;
}

// ===== Датчики =====
void updateSensors() {
  if (ahtOk) {
    sensors_event_t he, te;
    aht.getEvent(&he, &te);
    curTemp = te.temperature;
    curHum = he.relative_humidity;
  }

  if (voiceBusy) return;               // АЦП занят записью фразы — замер в следующий раз
  curSoil = readSoilPercent();
  soilDry = digitalRead(PIN_SOIL_D) == HIGH;   // если D0 показывает наоборот — поменяйте на LOW

  Serial.printf("Темп: %.1f C | Воздух: %.1f%% | Почва: %d%% (raw %d) | D0: %s\n",
                curTemp, curHum, curSoil, soilRaw, soilDry ? "сухо" : "мокро");

  bool cooldownDone = !everWatered || millis() - lastWater > COOLDOWN_MS;
  if (AUTO_WATER && needWater(curSoil) && cooldownDone && !serverDecides()) {
    water(curSoil < 0 ? "auto-D0" : "auto");
  }
}

// ===== Настройки сети (Preferences) =====
// Адрес сервера: без схемы дописываем http://, «/» в конце убираем
String normalizeServer(String s) {
  s.trim();
  while (s.endsWith("/")) s.remove(s.length() - 1);
  if (s.length() > 0 && s.indexOf("://") < 0) s = "http://" + s;
  return s;
}

String prefOr(const char* key, const char* def) {
  return prefs.isKey(key) ? prefs.getString(key) : String(def);
}

void loadConfig() {
  prefs.begin("net", true);
  cfgSsid   = prefOr("ssid", WIFI_SSID);
  cfgPass   = prefOr("pass", WIFI_PASS);
  cfgServer = normalizeServer(prefOr("server", SERVER));
  cfgToken  = prefOr("token", API_TOKEN);
  prefs.end();
  Serial.printf("Настройки: сеть \"%s\", сервер %s\n", cfgSsid.c_str(), cfgServer.c_str());
}

void saveConfig() {
  prefs.begin("net", false);
  prefs.putString("ssid", cfgSsid);
  prefs.putString("pass", cfgPass);
  prefs.putString("server", cfgServer);
  prefs.putString("token", cfgToken);
  prefs.end();
}

// ===== Режим настройки: точка доступа + форма =====
String htmlEscape(const String& s) {
  String r;
  for (size_t i = 0; i < s.length(); i++) {
    char c = s[i];
    if (c == '&') r += "&amp;";
    else if (c == '<') r += "&lt;";
    else if (c == '>') r += "&gt;";
    else if (c == '"') r += "&quot;";
    else r += c;
  }
  return r;
}

// Сканирование асинхронное: забираем готовый результат; новое — только по запросу.
// Во время сканирования точка доступа переключает канал и на миг пропадает: iPhone
// открывает страницу несколькими запросами и на этом обрывается — поэтому не на каждый запрос.
// Точка доступа работает одна (без режима клиента): иначе радио уходит на другие каналы,
// очередь отправки переполняется (WiFiUdp «could not send data: 12») и iPhone не открывает
// страницу. Режим клиента включаем только на время поиска сетей.
void collectScan(int n) {
  scanItems = "";
  for (int i = 0; i < n; i++) {
    String s = htmlEscape(WiFi.SSID(i));
    if (s.length() == 0 || scanItems.indexOf("<b>" + s + "</b>") >= 0) continue;
    scanItems += "<li onclick=\"f.ssid.value=this.firstChild.textContent\"><b>" + s +
                 "</b> " + String(WiFi.RSSI(i)) + " дБм</li>";
  }
  WiFi.scanDelete();
}

void refreshScan(bool start) {
  int n = WiFi.scanComplete();
  if (n == WIFI_SCAN_RUNNING) return;
  if (n >= 0) collectScan(n);
  if (start) {
    WiFi.mode(WIFI_AP_STA);
    WiFi.scanNetworks(true);
  } else if (WiFi.getMode() == WIFI_AP_STA) {
    WiFi.mode(WIFI_AP);             // поиск закончен — снова только точка доступа
  }
}

const char PAGE_HEAD[] PROGMEM = R"html(<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Автополив</title>
<style>body{font-family:sans-serif;max-width:420px;margin:16px auto;padding:0 16px}
input,button{width:100%;box-sizing:border-box;padding:10px;margin:4px 0 12px;font-size:16px}
li{padding:8px;border-bottom:1px solid #ddd;cursor:pointer;list-style:none}ul{padding:0}
small{color:#666}</style></head><body>)html";

// Журнал режима настройки в монитор порта: видно, доходят ли запросы телефона
void logRequest() {
  Serial.printf("Веб: %s http://%s%s от %s\n", web.method() == HTTP_POST ? "POST" : "GET",
                web.hostHeader().c_str(), web.uri().c_str(),
                web.client().remoteIP().toString().c_str());
}

void onApEvent(arduino_event_id_t event, arduino_event_info_t info) {
  if (event == ARDUINO_EVENT_WIFI_AP_STACONNECTED) {
    const uint8_t* m = info.wifi_ap_staconnected.mac;
    Serial.printf("Точка: подключился %02X:%02X:%02X:%02X:%02X:%02X\n",
                  m[0], m[1], m[2], m[3], m[4], m[5]);
  } else if (event == ARDUINO_EVENT_WIFI_AP_STAIPASSIGNED) {
    Serial.printf("Точка: выдан адрес %s\n",
                  IPAddress(info.wifi_ap_staipassigned.ip.addr).toString().c_str());
  } else if (event == ARDUINO_EVENT_WIFI_AP_STADISCONNECTED) {
    Serial.println("Точка: клиент отключился");
  }
}

void handleRoot() {
  logRequest();
  refreshScan(web.hasArg("scan"));
  String p = FPSTR(PAGE_HEAD);
  p += "<h2>Настройка автополива</h2><form id=f method=post action=/save>";
  p += "<label>Сеть Wi-Fi</label><input name=ssid required value=\"" + htmlEscape(cfgSsid) + "\">";
  bool scanning = WiFi.scanComplete() == WIFI_SCAN_RUNNING;
  p += "<ul>" + (scanItems.length() ? scanItems : String(scanning ?
       "<li>Поиск сетей… обновите через 5 секунд</li>" : "<li>Сетей не найдено</li>")) + "</ul>";
  p += "<small><a href=\"/?scan=1\">Обновить список сетей</a></small><br><br>";
  p += "<label>Пароль Wi-Fi</label><input name=pass type=password placeholder=\"пусто — не менять\">";
  p += "<label>Адрес сервера</label><input name=server placeholder=\"http://192.168.1.100:8000\" value=\"" +
       htmlEscape(cfgServer) + "\">";
  p += "<label>Токен устройства</label><input name=token type=password placeholder=\"" +
       String(cfgToken.length() ? "задан, пусто — не менять" : "PLANT_TOKEN на сервере") + "\">";
  p += "<button>Сохранить и перезагрузить</button></form>";
  p += "<small>Пароль Wi-Fi можно не вводить, если сеть та же. Для открытой сети выберите её и оставьте пароль пустым.</small>";
  p += "</body></html>";
  web.sendHeader("Cache-Control", "no-store");
  web.send(200, "text/html; charset=utf-8", p);
}

void handleSave() {
  logRequest();
  String ssid = web.arg("ssid");
  ssid.trim();
  if (ssid.length() == 0) {
    web.send(400, "text/plain; charset=utf-8", "Укажите сеть Wi-Fi");
    return;
  }
  String pass = web.arg("pass");
  if (pass.length() > 0 || ssid != cfgSsid) cfgPass = pass;   // пустой пароль у той же сети — не менять
  cfgSsid = ssid;
  cfgServer = normalizeServer(web.arg("server"));
  String token = web.arg("token");
  token.trim();
  if (token.length() > 0) cfgToken = token;
  saveConfig();
  Serial.printf("Настройки сохранены: сеть \"%s\", сервер %s\n", cfgSsid.c_str(), cfgServer.c_str());

  web.send(200, "text/html; charset=utf-8",
           String(FPSTR(PAGE_HEAD)) + "<h2>Сохранено</h2><p>Устройство перезагружается и подключается к сети «" +
           htmlEscape(cfgSsid) + "».</p></body></html>");
  toast("Saved, rebooting", 5000);
  renderFrame();
  delay(1500);
  ESP.restart();
}

// Любой адрес → форма (телефон сам откроет страницу входа в сеть)
void handleRedirect() {
  logRequest();
  web.sendHeader("Location", "http://" + WiFi.softAPIP().toString() + "/", true);
  web.send(302, "text/plain", "");
}

void startSetup() {
  static bool routes = false;
  if (!routes) {
    web.on("/", HTTP_GET, handleRoot);
    web.on("/save", HTTP_POST, handleSave);
    // Проверки «есть ли интернет» телефонов и ПК — сразу форма, телефон покажет её как вход в сеть
    for (const char* probe : {"/hotspot-detect.html", "/library/test/success.html",
                              "/generate_204", "/gen_204", "/connecttest.txt", "/ncsi.txt",
                              "/canonical.html", "/success.txt"}) {
      web.on(probe, HTTP_GET, handleRoot);
    }
    web.onNotFound(handleRedirect);
    WiFi.onEvent(onApEvent);
    routes = true;
  }
  WiFi.setAutoReconnect(false);   // попытки подключения переключают канал и мешают точке доступа
  WiFi.disconnect();
  WiFi.mode(WIFI_STA);            // сети ищем один раз — до запуска точки доступа
  int n = WiFi.scanNetworks(false);
  if (n >= 0) collectScan(n);
  WiFi.mode(WIFI_AP);
  WiFi.setSleep(false);           // радио не засыпает — пакеты телефону уходят сразу
  WiFi.softAP(AP_SSID, AP_PASS);
  dns.start(53, "*", WiFi.softAPIP());
  web.begin();

  setupMode = true;
  setupSince = millis();
  wifiOk = serverOk = false;
  Serial.printf("Режим настройки: сеть %s (пароль %s), http://%s\n",
                AP_SSID, AP_PASS, WiFi.softAPIP().toString().c_str());
  toast("Setup mode");
}

void stopSetup() {
  web.stop();
  dns.stop();
  WiFi.softAPdisconnect(true);
  WiFi.setSleep(true);            // как в обычном режиме
  setupMode = false;
}

void connectWiFi() {
  if (cfgSsid.length() == 0) {
    Serial.println("Wi-Fi не настроен");
    startSetup();
    return;
  }
  toast("Connecting WiFi...", 15000);
  renderFrame();
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(cfgSsid.c_str(), cfgPass.c_str());
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 10000) delay(200);

  wifiOk = (WiFi.status() == WL_CONNECTED);
  if (wifiOk) {
    Serial.print("Wi-Fi подключён, IP: ");
    Serial.println(WiFi.localIP());
    toast("WiFi " + WiFi.localIP().toString());
    configTime(0, 0, "pool.ntp.org", "time.google.com");   // часы — для времени полива
  } else {
    Serial.println("Wi-Fi не подключился, включаю режим настройки");
    startSetup();
  }
}

void handleSetup() {
  if (!setupMode) return;
  dns.processNextRequest();
  web.handleClient();
  // Поиск по кнопке закончился — вернуть режим «только точка доступа»
  if (WiFi.getMode() == WIFI_AP_STA && WiFi.scanComplete() != WIFI_SCAN_RUNNING) {
    refreshScan(false);
  }
  // Роутер мог просто загружаться дольше устройства — периодически пробуем снова
  if (WiFi.softAPgetStationNum() > 0) setupSince = millis();
  if (cfgSsid.length() > 0 && millis() - setupSince > SETUP_RETRY_MS) {
    stopSetup();
    connectWiFi();
  }
}

// ===== Голос (V2): запись → сервер → ответ =====
// Запись идёт в loop (ядро 1) под adcMutex; отправка — в отдельной задаче на ядре 0 (там Wi-Fi):
// данные идут через StreamBuffer, вся фраза в RAM не хранится.
StreamBufferHandle_t voiceStream = nullptr;
volatile bool voiceRecording = false;
volatile int voiceResult = 0;          // 0 — идёт, 1 — успех, -1 — ошибка
// V3: 2 — фраза без «Джарвис» (204), 3 — сервер просит подождать (429)
const int VOICE_NOT_FOR_US = 2, VOICE_BUSY = 3;
volatile bool voiceWake = false;       // запись без хлопка: /api/voice?wake=1
volatile bool voiceListen = false;     // сервер ответил «Слушаю» — ждём вопрос (X-Listen)
TaskHandle_t voiceTaskHandle = nullptr;

bool splitServer(String& host, uint16_t& port) {
  String u = cfgServer;
  if (!u.startsWith("http://")) return false;     // HTTPS устройство не использует (раздел 4.4)
  u = u.substring(7);
  int slash = u.indexOf('/');
  if (slash >= 0) u = u.substring(0, slash);
  int colon = u.indexOf(':');
  host = colon >= 0 ? u.substring(0, colon) : u;
  port = colon >= 0 ? u.substring(colon + 1).toInt() : 80;
  return host.length() > 0 && port > 0;
}

// Задача на ядре 0: POST /api/voice (chunked) → читаем ответ и выводим звук
void voiceTask(void*) {
  String host;
  uint16_t port;
  WiFiClient client;
  int result = -1;
  if (splitServer(host, port) && client.connect(host.c_str(), port, 3000)) {
    client.setTimeout(VOICE_TIMEOUT_MS / 1000);
    client.print(String("POST ") + (voiceWake ? "/api/voice?wake=1" : "/api/voice") +
                 " HTTP/1.1\r\nHost: " + host + "\r\n"
                 "Content-Type: audio/L16; rate=16000; channels=1\r\n"
                 "X-Device: " + String(DEVICE_ID) + "\r\n" +
                 (cfgToken.length() ? "X-Api-Key: " + cfgToken + "\r\n" : String("")) +
                 "Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n");
    uint8_t buf[2048];
    bool ok = true;
    for (;;) {
      size_t n = xStreamBufferReceive(voiceStream, buf, sizeof(buf), pdMS_TO_TICKS(50));
      if (n > 0) {
        client.printf("%X\r\n", (unsigned)n);
        if (client.write(buf, n) != n) { ok = false; break; }
        client.print("\r\n");
      } else if (!voiceRecording && xStreamBufferIsEmpty(voiceStream)) {
        break;
      }
    }
    if (ok) {
      client.print("0\r\n\r\n");
      if (!voiceWake) voiceState = VS_THINK;     // без хлопка: молчим, пока не ясно, нам ли
      String status = client.readStringUntil('\n');
      long length = -1;
      for (;;) {                                   // заголовки ответа
        String line = client.readStringUntil('\n');
        line.trim();
        if (line.length() == 0) break;
        if (line.startsWith("Content-Length:") || line.startsWith("content-length:")) {
          length = line.substring(15).toInt();
        }
        if (line.startsWith("X-Listen:") || line.startsWith("x-listen:")) voiceListen = true;
      }
      if (status.indexOf(" 204") > 0) {
        result = VOICE_NOT_FOR_US;
      } else if (status.indexOf(" 429") > 0) {
        result = VOICE_BUSY;
      } else if (status.indexOf(" 200") > 0) {
        voiceState = VS_SPEAK;
        AudioOut* out = makeAudioOut();
        bool play = out->begin();
        unsigned long t0 = millis();
        long got = 0;
        int16_t pcm[512];
        while ((length < 0 || got < length) && (client.connected() || client.available())) {
          int n = client.read((uint8_t*)pcm, sizeof(pcm));
          if (n <= 0) { vTaskDelay(1); continue; }
          if (play) out->write(pcm, n / 2);
          got += n;
        }
        // Звук играет в браузере — держим «Speaking...», пока длится ответ
        unsigned long durMs = got * 1000UL / (VOICE_RATE * 2);
        while (AUDIO_OUT == AUDIO_NONE && millis() - t0 < durMs) vTaskDelay(50);
        out->end();
        delete out;
        result = 1;
      } else {
        Serial.printf("Голос: сервер ответил %s\n", status.c_str());
      }
    }
  } else {
    Serial.println("Голос: нет связи с сервером");
  }
  client.stop();
  voiceResult = result;
  voiceTaskHandle = nullptr;
  vTaskDelete(nullptr);
}

// Запись фразы с MAX9814: 16 кГц, DC убираем, конец — по тишине
void recordPhrase() {
  const unsigned long periodUs = 1000000UL / VOICE_RATE;
  const int BLOCK = VOICE_RATE / 10;               // 100 мс — блок для уровня громкости
  int16_t block[160];
  int fill = 0, bmin = 4095, bmax = 0, inBlock = 0;
  float dc = 2048;
  bool spoke = false;
  long samples = 0;
  unsigned long start = millis(), lastLoud = millis();
  xSemaphoreTake(adcMutex, portMAX_DELAY);
  unsigned long next = micros();
  while (millis() - start < PHRASE_MAX_MS) {
    while ((long)(micros() - next) < 0) {}
    next += periodUs;
    int v = analogRead(PIN_MIC);
    samples++;
    dc += (v - dc) * 0.001f;
    block[fill++] = constrain((int)((v - dc) * 16), -32767, 32767);
    if (fill == 160) {                             // 10 мс — отдаём на отправку
      xStreamBufferSend(voiceStream, block, sizeof(block), 0);
      fill = 0;
    }
    if (v < bmin) bmin = v;
    if (v > bmax) bmax = v;
    if (++inBlock >= BLOCK) {
      if (bmax - bmin > SPEECH_LEVEL) { spoke = true; lastLoud = millis(); }
      bmin = 4095; bmax = 0; inBlock = 0;
      if (spoke && millis() - lastLoud > SILENCE_END_MS) break;
      if (!spoke && millis() - start > NO_SPEECH_MS) break;
    }
  }
  xSemaphoreGive(adcMutex);
  if (fill) xStreamBufferSend(voiceStream, block, fill * 2, 0);
  // Для ручной проверки: частота должна быть ≈ 16000 (иначе analogRead не успевает)
  unsigned long ms = millis() - start;
  Serial.printf("Голос: записано %.1f с, частота %ld Гц, речь: %s\n", ms / 1000.0,
                ms ? samples * 1000L / (long)ms : 0, spoke ? "да" : "нет");
}

void voiceDialog(bool wake = false) {
  if (!wifiOk || !serverOk || cfgServer.length() == 0) {
    if (!wake) toast("No server for voice");
    return;
  }
  if (!voiceStream) voiceStream = xStreamBufferCreate(32768, 1);   // ≈ 1 с запаса
  xStreamBufferReset(voiceStream);
  voiceBusy = true;
  voiceResult = 0;
  voiceWake = wake;
  voiceListen = false;
  voiceRecording = true;
  voiceState = wake ? VS_IDLE : VS_LISTEN;   // без хлопка «Listening...» не показываем
  renderFrame();
  xTaskCreatePinnedToCore(voiceTask, "voice", 8192, nullptr, 1, &voiceTaskHandle, 0);
  recordPhrase();
  voiceRecording = false;
  if (!wake) voiceState = VS_THINK;
  unsigned long t0 = millis();
  while (voiceResult == 0 && millis() - t0 < VOICE_TIMEOUT_MS + 5000) {
    renderFrame();                                 // экран живёт: Thinking... / Speaking...
    delay(100);
  }
  int result = voiceResult;
  bool listen = voiceListen;
  voiceState = VS_IDLE;
  voiceBusy = false;
  if (wake) {
    wakeTrigger = false;                       // звук своего же ответа — не новая фраза
    if (result == VOICE_BUSY) wakeBackoffUntil = millis() + WAKE_BUSY_MS;
    else if (result != 1) wakeBackoffUntil = millis() + WAKE_BACKOFF_MS;
    if (result == 1 && listen) voiceDialog(false);   // прозвучало только «Джарвис» — вопрос
    return;
  }
  if (result != 1) toast("Voice error");
}

// V3: речь без хлопка — может быть «Джарвис…»
void checkWake() {
  if (!wakeTrigger) return;
  wakeTrigger = false;
  if (!WAKE_ENABLED || !VOICE_ENABLED || setupMode || !serverOk || clapCount > 0) return;
  if ((long)(millis() - wakeBackoffUntil) < 0) return;
  voiceDialog(true);
}

// ===== Команды хлопками: только режим настройки =====
// Голосовой помощник — по слову «Джарвис» (V3), полив — по порогу или прогнозу ИИ (D0).
void runCommand(int claps) {
  if (claps == 3) {                           // 3 хлопка — режим настройки (повторно — выход)
    if (setupMode) {
      stopSetup();
      connectWiFi();
    } else {
      startSetup();
    }
  } else {
    Serial.printf("Хлопков: %d — команды нет (3 хлопка — режим настройки)\n", claps);
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

  loadConfig();
  prefs.begin("state", true);
  lastWaterEpoch = prefs.getLong64("water_ts", 0);
  prefs.end();
  connectWiFi();

  xTaskCreatePinnedToCore(micTask, "mic", 4096, NULL, 1, NULL, 0);

  updateSensors();
  sendTelemetry();
  sendEvent("boot", 0, "start");
}

void loop() {
  if (Serial.available() && Serial.read() == 'w') water("manual");

  handleSetup();

  checkWake();

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
