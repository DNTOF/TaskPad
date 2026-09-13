#include <reg52.h>
#include "config.h"
#include "lcd1602.h"
#include "uart.h"

sbit BEEP = P2^5;
sbit KEY_ACK = P3^0;
sbit KEY_START = P3^2;
sbit KEY_CYCLE = P3^3;

#define ST_IDLE 0
#define ST_RUN 1
#define ST_DONE 2
#define LINE_MAX 24

#define RL_M5 0xFE0A
#define RL_M6 0xFE33
#define RL_M7 0xFE56
#define RL_H1 0xFE65
#define RL_H2 0xFE80

#if BUZZER_ACTIVE_LOW
#define BEEP_IDLE 1
#else
#define BEEP_IDLE 0
#endif

static unsigned char g_state, g_tgt, step_tone;
static idata char g_line0[17], g_line1[17];
static bit buzz_on, tone_on;
static unsigned int g_reload;

static void DelayMs(unsigned int ms) {
  unsigned int i, j;
  for (i = 0; i < ms; i++) for (j = 0; j < 120; j++);
}

static void PadCopy(char *dst, char *src) {
  unsigned char i;
  for (i = 0; i < 16; i++) dst[i] = (src && src[i]) ? src[i] : ' ';
  dst[16] = 0;
}

static char *TgtName(void) {
  if (g_tgt == 1) return "zcode";
  if (g_tgt == 2) return "grok";
  return "cursor";
}

static void ShowIdle(void) {
  char *t; unsigned char i;
  PadCopy(g_line0, "Ready  K3=start");
  PadCopy(g_line1, "T:");
  t = TgtName();
  for (i = 0; i < 14 && t[i]; i++) g_line1[2 + i] = t[i];
  for (; i < 14; i++) g_line1[2 + i] = ' ';
  g_line1[16] = 0;
  LCD_WriteLine(0, g_line0);
  LCD_WriteLine(1, g_line1);
}

static bit DebouncePress(bit pressed, unsigned char *stable, unsigned char *cnt) {
  if (pressed) {
    if (*cnt < 5) (*cnt)++;
    if (*cnt >= 5 && *stable == 0) { *stable = 1; return 1; }
  } else { *cnt = 0; *stable = 0; }
  return 0;
}

static bit StartsWith(char *s, char *p) {
  while (*p) { if (*s++ != *p++) return 0; }
  return 1;
}

static void CopyTag(char *src) {
  if (src[0] == 'z') g_tgt = 1;
  else if (src[0] == 'g') g_tgt = 2;
  else g_tgt = 0;
}

static void Timer0_Init(void) {
  TMOD &= 0xF0; TMOD |= 0x01; ET0 = 1; EA = 1; TR0 = 0;
}

void timer0_isr(void) interrupt 1 {
  TH0 = (unsigned char)(g_reload >> 8);
  TL0 = (unsigned char)(g_reload & 0xFF);
  if (tone_on) BEEP = !BEEP;
}

static void ToneStop(void) { tone_on = 0; TR0 = 0; BEEP = BEEP_IDLE; }

static void ToneStart(unsigned int reload) {
  g_reload = reload;
  TH0 = (unsigned char)(reload >> 8);
  TL0 = (unsigned char)(reload & 0xFF);
  tone_on = 1; TR0 = 1;
}

static void Beep(unsigned int reload, unsigned int ms) {
  ToneStart(reload); DelayMs(ms); ToneStop();
}

static void BuzzerOff(void) { buzz_on = 0; ToneStop(); }

/* thinking->editing->... each LCD|L0|  (auto-RUN if needed) */
static void StepChirp(void) {
  unsigned int r;
  if (step_tone == 0) r = RL_M5;
  else if (step_tone == 1) r = RL_M6;
  else if (step_tone == 2) r = RL_M7;
  else if (step_tone == 3) r = RL_H1;
  else r = RL_H2;
  Beep(r, 55);
  step_tone++;
  if (step_tone > 4) step_tone = 0;
}

static void HandleRx(char *line) {
  if (StartsWith(line, "PING")) { Uart_SendLine("PONG"); return; }
  if (StartsWith(line, "LCD|L0|")) {
    PadCopy(g_line0, line + 7);
    LCD_WriteLine(0, g_line0);
    /* Soft-enter RUN so hub progress chirps without prior STAT|run */
    if (g_state == ST_IDLE) g_state = ST_RUN;
    if (g_state == ST_RUN) StepChirp();
    return;
  }
  if (StartsWith(line, "LCD|L1|")) {
    PadCopy(g_line1, line + 7);
    LCD_WriteLine(1, g_line1);
    return;
  }
  if (StartsWith(line, "TARGET|")) { CopyTag(line + 7); if (g_state == ST_IDLE) ShowIdle(); return; }
  if (StartsWith(line, "BUZZ|off")) { BuzzerOff(); return; }
  if (StartsWith(line, "STAT|idle")) { g_state = ST_IDLE; BuzzerOff(); ShowIdle(); return; }
  if (StartsWith(line, "STAT|run|")) {
    g_state = ST_RUN; step_tone = 0; CopyTag(line + 9); BuzzerOff();
    PadCopy(g_line0, "RUNNING...      ");
    PadCopy(g_line1, TgtName());
    LCD_WriteLine(0, g_line0); LCD_WriteLine(1, g_line1);
    Beep(RL_M6, 55);
    return;
  }
  if (StartsWith(line, "STAT|done|")) {
    CopyTag(line + 10);
    PadCopy(g_line0, "DONE  K2=ACK   ");
    PadCopy(g_line1, TgtName());
    LCD_WriteLine(0, g_line0); LCD_WriteLine(1, g_line1);
    g_state = ST_DONE; buzz_on = 1; ToneStart(RL_H1);
    return;
  }
  if (StartsWith(line, "STAT|fail|")) {
    PadCopy(g_line0, "FAIL            ");
    PadCopy(g_line1, line + 10);
    LCD_WriteLine(0, g_line0); LCD_WriteLine(1, g_line1);
    Beep(RL_M5, 100);
    g_state = ST_IDLE; ShowIdle();
    return;
  }
}

void main(void) {
  unsigned char cStart=0,cAck=0,cCyc=0,sStart=0,sAck=0,sCyc=0;
  idata char line[LINE_MAX];
  g_state = ST_IDLE; g_tgt = 0; buzz_on = 0; step_tone = 0;
  BEEP = BEEP_IDLE; Timer0_Init(); LCD_Init(); Uart_Init();
  Beep(RL_H1, 100);
  Uart_SendLine("HELLO"); ShowIdle();
  while (1) {
    if (Uart_HasLine()) { Uart_GetLine(line, LINE_MAX); HandleRx(line); }
    if (DebouncePress(KEY_START == 0, &sStart, &cStart) && g_state == ST_IDLE) {
      Uart_SendLine("BTN:START"); Beep(RL_M7, 50);
      PadCopy(g_line0, "START sent...   ");
      PadCopy(g_line1, TgtName());
      LCD_WriteLine(0, g_line0); LCD_WriteLine(1, g_line1);
    }
    if (DebouncePress(KEY_CYCLE == 0, &sCyc, &cCyc) && g_state == ST_IDLE) {
      g_tgt++; if (g_tgt > 2) g_tgt = 0;
      ShowIdle();
      Uart_SendString("TARGET:"); Uart_SendLine(TgtName());
    }
    if (DebouncePress(KEY_ACK == 0, &sAck, &cAck) && g_state == ST_DONE) {
      BuzzerOff(); g_state = ST_IDLE; Uart_SendLine("BTN:ACK"); ShowIdle();
    }
    if (buzz_on && g_state == ST_DONE && !tone_on) ToneStart(RL_H1);
  }
}
