@echo off
setlocal
rem Reproducibly build the native launcher with x86_64-w64-mingw32-gcc 13.2.0.
rem The launcher contains no TerraNore logic: all build logic is in build.py.
pushd "%~dp0"
where x86_64-w64-mingw32-gcc >nul 2>nul
if errorlevel 1 (
  echo ERROR: x86_64-w64-mingw32-gcc 13.2.0 is required. 1>&2
  popd
  exit /b 2
)
x86_64-w64-mingw32-gcc -Os -s -municode -mconsole build_launcher.c -lshell32 -Wl,--no-insert-timestamp -o build.exe
set result=%errorlevel%
if not "%result%"=="0" echo ERROR: build.exe compilation failed. 1>&2
popd
exit /b %result%
