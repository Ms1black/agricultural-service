// Персонаж «Джарвис» (из image.png): пиксель-арт 15×15, «.» — прозрачно.
// Показывается на экране, пока работает голосовой помощник и во время полива.
#pragma once
#include <stdint.h>

static const int JARVIS_W = 15, JARVIS_H = 15;
static const char* const JARVIS[JARVIS_H] = {
  "........aa.....",
  ".......aa......",
  ".......a.......",
  "....bbbabb.....",
  "...cddddddb....",
  "..cdddddddcb...",
  ".cdddddddddcc..",
  "cddddddddddddc.",
  "cddaefaaefaddc.",
  "cddaffaaffaddc.",
  "cddgddddddgdddc",
  "bdddddddddddddc",
  "bddddddddddddc.",
  ".bddddddddddcc.",
  "..bbaa..aabc...",
};

// При поливе жмурится от брызг: строки 7–9 с глазами «> <» вместо обычных
static const int JARVIS_SQUINT_FROM = 7;
static const char* const JARVIS_SQUINT[3] = {
  "cdddfddddfdddc.",
  "cddddfddfddddc.",
  "cdddfddddfdddc.",
};

// Цвета букв a, b, c… (RGB)
static const uint8_t JARVIS_RGB[][3] = {
  {121, 255, 49},   // a — светло-зелёный (стебель, блики)
  {91, 173, 37},   // b — тёмно-зелёный контур
  {116, 203, 58},   // c — зелёная тень
  {118, 226, 56},   // d — зелёный
  {255, 255, 255},   // e — блик в глазах
  {0, 0, 0},   // f — глаза
  {251, 172, 212},   // g — румянец
};
