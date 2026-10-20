// Голос (V2): настройки и вывод звука (audioOut). Отдельный файл — чтобы классы были
// объявлены раньше автоматически сгенерированных прототипов функций .ino.
#pragma once
#include <Arduino.h>
#include <driver/i2s.h>

// ===== Голос (V2) =====
// 1 хлопок = «слушаю»: фраза (до 8 с, конец — по тишине) уходит на сервер потоком,
// ответ — PCM 16 кГц. VOICE_ENABLED 0 — как раньше: 1 хлопок — приветствие.
#ifndef VOICE_ENABLED
#define VOICE_ENABLED 1
#endif
// Куда выводить ответ: AUDIO_NONE — звучит в браузере (дашборд, V1b), по умолчанию;
// AUDIO_PDM — I2S PDM на один вывод → RC-фильтр → PAM8403/LM386 → динамик 8 Ом;
// AUDIO_I2S_MAX98357 — I2S на усилитель MAX98357A.
#define AUDIO_NONE 0
#define AUDIO_PDM 1
#define AUDIO_I2S_MAX98357 2
// V3: «Джарвис» без хлопка. Устройство слушает фон; речь дольше WAKE_MIN_MS (хлопок короче
// 150 мс её не запускает) записывается и уходит на /api/voice?wake=1 — сервер распознаёт
// фразу и отвечает, только если она начинается с «Джарвис». Поток — только когда звучит речь.
#ifndef WAKE_ENABLED
#define WAKE_ENABLED 1
#endif
static const int WAKE_LEVEL = 400;                    // размах АЦП за 10 мс: выше — звук
static const unsigned long WAKE_MIN_MS = 250;         // столько подряд — это речь, а не хлопок
static const unsigned long WAKE_GAP_MS = 120;         // паузы между слогами короче — не обрыв
static const unsigned long WAKE_BACKOFF_MS = 2000;    // после фразы без обращения
static const unsigned long WAKE_BUSY_MS = 30000;      // сервер ответил 429 — много лишних фраз
#ifndef AUDIO_OUT              // можно задать флагом сборки: -D AUDIO_OUT=AUDIO_PDM
#define AUDIO_OUT AUDIO_NONE
#endif
// Выводы звука — УТОЧНИТЬ У ЧЕЛОВЕКА перед включением (правило 3); при AUDIO_NONE не используются
#define PIN_AUDIO_PDM   6       // PDM: данные (кандидаты верхнего ряда: 6, 7)
#define PIN_I2S_BCLK    6       // MAX98357A: BCLK
#define PIN_I2S_LRC     7       // MAX98357A: LRC
#define PIN_I2S_DIN     -1      // MAX98357A: DIN — один пин нижнего ряда, уточнить
static const float AUDIO_VOLUME = 0.5f;               // громкость ~50 % (динамик 0.5 Вт)
static const int VOICE_RATE = 16000;
static const unsigned long PHRASE_MAX_MS = 8000;      // контракт 4.3: фраза ≤ 8 с
static const unsigned long SILENCE_END_MS = 1000;     // конец фразы — тишина ≥ 1 с
static const unsigned long NO_SPEECH_MS = 3000;       // так и не заговорили — прекращаем
static const int SPEECH_LEVEL = 300;                  // размах АЦП за 100 мс: выше — речь
static const unsigned long VOICE_TIMEOUT_MS = 25000;  // сервер: обработка ≤ 20 с + запас

// ===== Голос (V2): вывод звука =====
class AudioOut {
public:
  virtual bool begin() { return true; }
  virtual void write(const int16_t* pcm, size_t n) {}
  virtual void end() {}
  virtual ~AudioOut() {}
};

// Звук только в браузере: данные принимаем и отбрасываем
class AudioOutNone : public AudioOut {};

// I2S на ESP32-S3 (драйвер IDF 4.4): PDM — один вывод данных, MAX98357A — BCLK/LRC/DIN
class AudioOutI2s : public AudioOut {
  bool pdm;
public:
  explicit AudioOutI2s(bool pdmMode) : pdm(pdmMode) {}
  bool begin() override {
    i2s_config_t cfg = {};
    cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX | (pdm ? I2S_MODE_PDM : 0));
    cfg.sample_rate = VOICE_RATE;
    cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
    cfg.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;
    cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
    cfg.dma_buf_count = 6;
    cfg.dma_buf_len = 256;
    cfg.tx_desc_auto_clear = true;
    if (i2s_driver_install(I2S_NUM_0, &cfg, 0, nullptr) != ESP_OK) return false;
    i2s_pin_config_t pins = {};
    pins.mck_io_num = I2S_PIN_NO_CHANGE;
    pins.data_in_num = I2S_PIN_NO_CHANGE;
    if (pdm) {
      pins.bck_io_num = I2S_PIN_NO_CHANGE;
      pins.ws_io_num = I2S_PIN_NO_CHANGE;     // такт PDM не выводим: после RC-фильтра не нужен
      pins.data_out_num = PIN_AUDIO_PDM;
    } else {
      pins.bck_io_num = PIN_I2S_BCLK;
      pins.ws_io_num = PIN_I2S_LRC;
      pins.data_out_num = PIN_I2S_DIN;
    }
    return i2s_set_pin(I2S_NUM_0, &pins) == ESP_OK;
  }
  void write(const int16_t* pcm, size_t n) override {
    int16_t buf[256];
    for (size_t i = 0; i < n; i += 256) {
      size_t k = min((size_t)256, n - i), written;
      for (size_t j = 0; j < k; j++) buf[j] = pcm[i + j] * AUDIO_VOLUME;
      i2s_write(I2S_NUM_0, buf, k * 2, &written, portMAX_DELAY);
    }
  }
  void end() override {
    i2s_zero_dma_buffer(I2S_NUM_0);
    i2s_driver_uninstall(I2S_NUM_0);
  }
};

inline AudioOut* makeAudioOut() {
#if AUDIO_OUT == AUDIO_PDM
  return new AudioOutI2s(true);
#elif AUDIO_OUT == AUDIO_I2S_MAX98357
  return new AudioOutI2s(false);
#else
  return new AudioOutNone();
#endif
}

