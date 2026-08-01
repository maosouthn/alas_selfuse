@echo off
rem ============================================================
rem  ALAS 手动更新 (双击运行)
rem  调起 Git Bash 执行 update.sh,保留本地改动
rem ============================================================
setlocal
cd /d "%~dp0"

rem ---- 查找 Git Bash 的 bash.exe ----
set "BASH="
for %%p in (
  "%ProgramFiles%\Git\bin\bash.exe"
  "%ProgramFiles(x86)%\Git\bin\bash.exe"
  "%LocalAppData%\Programs\Git\bin\bash.exe"
  "%USERPROFILE%\scoop\apps\git\current\bin\bash.exe"
) do (
  if not defined BASH if exist "%%~p" set "BASH=%%~p"
)
if not defined BASH (
  for /f "delims=" %%i in ('where bash 2^>nul') do if not defined BASH set "BASH=%%i"
)
if not defined BASH (
  echo [错误] 未找到 Git Bash。请安装 Git for Windows,或在 Git Bash 中手动运行: bash update.sh
  pause
  exit /b 1
)

echo 使用 Git Bash: %BASH%
"%BASH%" -c "bash update.sh"
pause
