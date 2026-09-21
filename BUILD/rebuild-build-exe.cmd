@echo off
setlocal
rem Reproducibly build the native launcher with x86_64-w64-mingw32-gcc 13.2.0.
rem The launcher contains no TerraNore logic: all build logic is in build.py.
set "CC=x86_64-w64-mingw32-gcc"
set "EXPECTED_GCC_VERSION=13.2.0"
pushd "%~dp0"
where %CC% >nul 2>nul
if errorlevel 1 (
  echo ERROR: %CC% %EXPECTED_GCC_VERSION% is required but was not found in PATH. 1>&2
  popd
  exit /b 2
)

set "ACTUAL_GCC_VERSION="
for /f "usebackq delims=" %%V in (`%CC% -dumpfullversion 2^>nul`) do set "ACTUAL_GCC_VERSION=%%V"
if not defined ACTUAL_GCC_VERSION (
  echo ERROR: Could not determine %CC% version with -dumpfullversion. 1>&2
  popd
  exit /b 2
)
echo Using %CC% %ACTUAL_GCC_VERSION%.
if not "%ACTUAL_GCC_VERSION%"=="%EXPECTED_GCC_VERSION%" (
  echo ERROR: Reproducible build requires %CC% %EXPECTED_GCC_VERSION%; found %ACTUAL_GCC_VERSION%. 1>&2
  echo ERROR: Install the documented compiler version or run BUILD\build.py directly. 1>&2
  popd
  exit /b 2
)

rem The compiler triplet and -m64 fix the target to 64-bit x86 Windows.
%CC% -m64 -Os -s -municode -mconsole build_launcher.c -Wl,--no-insert-timestamp -lshell32 -o build.exe
set result=%errorlevel%
if not "%result%"=="0" echo ERROR: build.exe compilation failed. 1>&2
popd
exit /b %result%
