#include <reg52.h>
#include "uart.h"

#define RX_MAX 24
static idata char rx_buf[RX_MAX];
static unsigned char rx_len;
static bit line_ready;

void Uart_Init(void) {
  TMOD &= 0x0F; TMOD |= 0x20;
  TH1 = 0xFD; TL1 = 0xFD;
  PCON &= 0x7F; SCON = 0x50;
  ES = 1; EA = 1; TR1 = 1;
  rx_len = 0; line_ready = 0;
}

void Uart_SendByte(unsigned char b) {
  SBUF = b; while (!TI); TI = 0;
}

void Uart_SendString(char *s) {
  while (*s) Uart_SendByte((unsigned char)*s++);
}

void Uart_SendLine(char *s) {
  Uart_SendString(s); Uart_SendByte('\n');
}

bit Uart_HasLine(void) { return line_ready; }

void Uart_GetLine(char *buf, unsigned char maxlen) {
  unsigned char i;
  ES = 0;
  for (i = 0; i < rx_len && i + 1 < maxlen; i++) buf[i] = rx_buf[i];
  buf[i] = 0; rx_len = 0; line_ready = 0; ES = 1;
}

void serial_isr(void) interrupt 4 {
  unsigned char c;
  if (RI) {
    RI = 0; c = SBUF;
    if (c == '\r') return;
    if (c == '\n') { if (rx_len > 0) line_ready = 1; return; }
    if (!line_ready && rx_len + 1 < RX_MAX) rx_buf[rx_len++] = (char)c;
  }
}