"""Распознавание (faster-whisper) и синтез речи (Silero) на сервере, бесплатно, на CPU.

Модели загружаются при первом обращении (или прогревом при старте) в <models_dir>/voice.
"""

import threading
from pathlib import Path

import numpy as np

from .audio import RATE, resample

SILERO_URL = "https://models.silero.ai/models/tts/ru/v4_ru.pt"
SILERO_RATE = 24000
# Подсказка распознаванию: имя помощника и типичные фразы
PROMPT = "{name}, как там мой цветок? {name}, полей растение."


class VoiceUnavailable(RuntimeError):
    pass


class Whisper:
    def __init__(self, model: str, root: Path):
        self.model_name, self.root, self._model = model, root, None
        self.assistant_name = "Джарвис"
        self._lock = threading.Lock()

    def _load(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as e:
                raise VoiceUnavailable("faster-whisper не установлен") from e
            self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8",
                                       download_root=str(self.root / "whisper"))
        return self._model

    def transcribe(self, audio) -> str:
        """audio — float32 16 кГц или файл (webm/ogg/wav — декодирует PyAV)."""
        with self._lock:
            model = self._load()
            segments, _ = model.transcribe(audio, language="ru", beam_size=1, vad_filter=True,
                                           initial_prompt=PROMPT.format(
                                               name=self.assistant_name))
            return " ".join(s.text.strip() for s in segments).strip()


class Silero:
    def __init__(self, speaker: str, root: Path):
        self.speaker, self.path, self._model = speaker, root / "silero" / "v4_ru.pt", None
        self._lock = threading.Lock()

    def _load(self):
        if self._model is None:
            try:
                import torch
            except ImportError as e:
                raise VoiceUnavailable("torch не установлен") from e
            if not self.path.exists():
                import httpx
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with httpx.stream("GET", SILERO_URL, timeout=120, follow_redirects=True) as r:
                    r.raise_for_status()
                    tmp = self.path.with_suffix(".part")
                    with open(tmp, "wb") as f:
                        for chunk in r.iter_bytes():
                            f.write(chunk)
                    tmp.replace(self.path)
            torch.set_num_threads(2)
            model = torch.package.PackageImporter(str(self.path)).load_pickle("tts_models",
                                                                               "model")
            model.to(torch.device("cpu"))
            self._model = model
        return self._model

    def synthesize(self, text: str) -> np.ndarray:
        """Текст (кириллица, без цифр) → float32 16 кГц."""
        with self._lock:
            model = self._load()
            parts = []
            for sentence in split_sentences(text):
                wav = model.apply_tts(text=sentence, speaker=self.speaker,
                                      sample_rate=SILERO_RATE, put_accent=True, put_yo=True)
                parts.append(wav.numpy())
                parts.append(np.zeros(SILERO_RATE // 5, dtype=np.float32))   # пауза 0.2 с
            audio = np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)
            return resample(audio, SILERO_RATE, RATE)


def split_sentences(text: str, max_len: int = 200) -> list[str]:
    import re
    out = []
    for s in re.split(r"(?<=[.!?])\s+", text):
        s = s.strip()
        while len(s) > max_len:
            cut = s.rfind(" ", 0, max_len)
            cut = cut if cut > 0 else max_len
            out.append(s[:cut])
            s = s[cut:].strip()
        if s:
            out.append(s)
    return out
