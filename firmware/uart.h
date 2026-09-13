#ifndef UART_H
#define UART_H
void Uart_Init(void);
void Uart_SendByte(unsigned char b);
void Uart_SendString(char *s);
void Uart_SendLine(char *s);
bit Uart_HasLine(void);
void Uart_GetLine(char *buf, unsigned char maxlen);
#endif