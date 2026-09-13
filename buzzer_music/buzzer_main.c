#include <reg52.h>
#include "songs.h"

sbit BUZZER = P1^5;   /* active-low drive via transistor */
sbit KEY_STOP = P3^0; /* K2 */
sbit KEY_PLAY = P3^2; /* K3 play / next */
sbit KEY_PREV = P3^3; /* K4 prev */

/* LCD1602 — same wiring as TaskPad51 */
sbit LCD_RS = P2^6;
sbit LCD_RW = P2^5;
sbit LCD_EN = P2^7;
#define LCD_DATA P0

static unsigned char g_song;
static bit g_playing;
static bit g_abort;

static void DelayMs(unsigned int ms) {
  unsigned int i, j;
  for (i = 0; i < ms; i++)
    for (j = 0; j < 120; j++);
}

/* ~1us busy wait (approx @11.0592MHz 12T) */
static void DelayUs(unsigned int us) {
  while (us--) {
    ;
  }
}

static void BuzzHi(void) { BUZZER = 0; }
static void BuzzLo(void) { BUZZER = 1; }

static void LCD_Write(bit rs, unsigned char dat) {
  LCD_RS = rs; LCD_RW = 0; LCD_DATA = dat;
  LCD_EN = 1; DelayUs(2); LCD_EN = 0; DelayUs(40);
}

static void LCD_Cmd(unsigned char c) { LCD_Write(0, c); DelayMs(2); }
static void LCD_Dat(unsigned char c) { LCD_Write(1, c); }

static void LCD_Init(void) {
  DelayMs(20);
  LCD_Cmd(0x38); LCD_Cmd(0x0C); LCD_Cmd(0x06); LCD_Cmd(0x01);
}

static void LCD_Line(unsigned char row, char *s) {
  unsigned char i;
  LCD_Cmd(row ? 0xC0 : 0x80);
  for (i = 0; i < 16; i++) LCD_Dat((s && s[i]) ? s[i] : ' ');
}

static void LCD_ShowSong(void) {
  char line0[17];
  unsigned char i;
  char *n = song_names[g_song];
  for (i = 0; i < 16; i++) line0[i] = ' ';
  line0[16] = 0;
  line0[0] = 'S'; line0[1] = '0' + g_song + 1; line0[2] = ':';
  for (i = 0; i < 12 && n[i]; i++) line0[3 + i] = n[i];
  LCD_Line(0, line0);
  LCD_Line(1, g_playing ? "Playing... K2=stop" : "K3 play  K4 prev ");
}

static bit KeyEdge(bit down, unsigned char *st, unsigned char *cnt) {
  if (down) {
    if (*cnt < 4) (*cnt)++;
    if (*cnt >= 4 && *st == 0) { *st = 1; return 1; }
  } else { *cnt = 0; *st = 0; }
  return 0;
}

static void PlayTone(unsigned char midi, unsigned char dur10) {
  unsigned int half;
  unsigned long loops;
  unsigned int guard;
  if (midi == 0) {
    DelayMs((unsigned int)dur10 * 10);
    return;
  }
  if (midi < NOTE_BASE || midi >= NOTE_BASE + NOTE_SPAN) return;
  half = half_us_tab[midi - NOTE_BASE];
  if (half < 50) half = 50;
  /* total half-cycles in duration */
  loops = ((unsigned long)dur10 * 10000UL) / (half * 2UL);
  if (loops == 0) loops = 1;
  while (loops--) {
    if (KEY_STOP == 0) { g_abort = 1; break; }
    BuzzHi(); DelayUs(half);
    BuzzLo(); DelayUs(half);
    /* occasional key poll already via STOP */
    guard = 0; guard++;
  }
  BuzzLo();
}

static void PlaySong(void) {
  NOTE *p;
  g_playing = 1; g_abort = 0;
  LCD_ShowSong();
  p = song_table[g_song];
  while (p->note || p->dur) {
    if (g_abort) break;
    PlayTone(p->note, p->dur);
    p++;
  }
  BuzzLo();
  g_playing = 0;
  LCD_ShowSong();
}

void main(void) {
  unsigned char c3=0,c2=0,c4=0,s3=0,s2=0,s4=0;
  BuzzLo();
  LCD_Init();
  g_song = 0; g_playing = 0;
  LCD_ShowSong();
  /* boot chirp */
  PlayTone(72, 8); DelayMs(40); PlayTone(76, 8);
  while (1) {
    if (KeyEdge(KEY_PLAY == 0, &s3, &c3)) {
      PlaySong();
      /* after finish, auto-advance */
      if (!g_abort) {
        g_song++; if (g_song >= SONG_COUNT) g_song = 0;
        LCD_ShowSong();
      }
    }
    if (KeyEdge(KEY_PREV == 0, &s4, &c4)) {
      if (g_song == 0) g_song = SONG_COUNT - 1; else g_song--;
      LCD_ShowSong();
    }
    if (KeyEdge(KEY_STOP == 0, &s2, &c2)) {
      g_abort = 1; BuzzLo(); g_playing = 0; LCD_ShowSong();
    }
  }
}
