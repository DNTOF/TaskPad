@echo off
set PATH=C:\Keil\C51\BIN;%PATH%
set C51INC=C:\Keil\C51\INC
set C51LIB=C:\Keil\C51\LIB
cd /d "C:\Users\USER\Desktop\开发\TaskPad51\firmware\obj"
del /q *.* 2>nul
C51.exe ..\lcd1602.c OBJECT(lcd1602.obj) PRINT(lcd1602.lst) OPTIMIZE(8,SIZE)
if errorlevel 1 exit /b 1
C51.exe ..\uart.c OBJECT(uart.obj) PRINT(uart.lst) OPTIMIZE(8,SIZE)
if errorlevel 1 exit /b 1
C51.exe ..\main.c OBJECT(main.obj) PRINT(main.lst) OPTIMIZE(8,SIZE)
if errorlevel 1 exit /b 1
BL51.exe lcd1602.obj,uart.obj,main.obj TO taskpad51 PRINT(taskpad51.m51)
echo BL51_ERR=%ERRORLEVEL%
if not exist taskpad51 (
  echo NO ABS FILE
  type taskpad51.m51
  exit /b 2
)
OH51.exe taskpad51 HEXFILE(taskpad51.hex)
echo OH51_ERR=%ERRORLEVEL%
dir taskpad51.hex
findstr /i "ERROR WARNING Program Size USED RESTRICTED" taskpad51.m51