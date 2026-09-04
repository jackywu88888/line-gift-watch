@echo off
chcp 65001 >nul
set "F=%~dp0latest-live.txt"
if exist "%F%" (
  notepad "%F%"
) else (
  echo 還沒有結果檔，請先跑一次 line-gift-watch.ps1
  pause
)
