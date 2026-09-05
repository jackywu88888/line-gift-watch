@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ===== latest-coupons.txt =====
type latest-coupons.txt
echo.
echo ===== last 30 log lines =====
powershell -NoProfile -Command "Get-Content -Path '.\coupon-watch.log' -Encoding UTF8 -Tail 30"
echo.
pause
