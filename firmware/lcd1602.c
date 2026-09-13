#include <reg52.h>
#include "lcd1602.h"
#define LCD_DATA P0
sbit LCD_RS = P2^6;
sbit LCD_RW = P2^5;
sbit LCD_EN = P2^7;
#define BEEP_IDLE 1
static void DelayMs(unsigned int ms) {
  unsigned int i, j; for (i = 0; i < ms; i++) for (j = 0; j < 120; j++);
}
static void LCD_WriteCmd(unsigned char cmd) {
  LCD_RS = 0; LCD_RW = 0; LCD_DATA = cmd; LCD_EN = 1; DelayMs(1); LCD_EN = 0; DelayMs(2);
}
static void LCD_WriteDat(unsigned char dat) {
  LCD_RS = 1; LCD_RW = 0; LCD_DATA = dat; LCD_EN = 1; DelayMs(1); LCD_EN = 0; DelayMs(1);
}
void LCD_Init(void) {
  DelayMs(15);
  LCD_WriteCmd(0x38); LCD_WriteCmd(0x0C); LCD_WriteCmd(0x06); LCD_WriteCmd(0x01); DelayMs(2);
  LCD_RW = BEEP_IDLE; /* release shared pin for buzzer idle */
}
void LCD_WriteLine(unsigned char line, char *s) {
  unsigned char i;
  bit et0_was_on = ET0;
  /* Buzzer shares P2.5 with LCD_RW — freeze timer toggle while writing */
  ET0 = 0;
  LCD_RW = 0;
  LCD_WriteCmd(line ? 0xC0 : 0x80);
  for (i = 0; i < 16; i++) LCD_WriteDat((s && s[i]) ? (unsigned char)s[i] : ' ');
  /* Leave pin in buzzer-idle level so StepChirp can square-wave. */
  LCD_RW = BEEP_IDLE;
  ET0 = et0_was_on;
}
