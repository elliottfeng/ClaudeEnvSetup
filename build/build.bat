@echo off
setlocal EnableExtensions

rem Build interpreter: Python 3.10 (best Tk 8.6 compatibility)
set "BUILD_PY=D:\python-3.10.11-amd64\python.exe"
if not exist "%BUILD_PY%" (
  echo [ERROR] Build interpreter not found: %BUILD_PY%
  echo         Edit BUILD_PY in build.bat or pass a Python 3.10 path.
  exit /b 1
)

set "ROOT=%~dp0.."
pushd "%ROOT%"

"%BUILD_PY%" -c "import sys; assert (3,10) <= sys.version_info < (3,11), sys.version"
if errorlevel 1 (
  echo [ERROR] Build interpreter must be Python 3.10.x
  popd
  exit /b 1
)

if not exist "%ROOT%\.venv-build\Scripts\python.exe" (
  "%BUILD_PY%" -m venv "%ROOT%\.venv-build"
  if errorlevel 1 goto :fail
)

"%ROOT%\.venv-build\Scripts\python.exe" -m pip install -U pip
if errorlevel 1 goto :fail
"%ROOT%\.venv-build\Scripts\python.exe" -m pip install -r "%ROOT%\requirements-dev.txt"
if errorlevel 1 goto :fail

echo.
echo === Run unit tests ===
"%ROOT%\.venv-build\Scripts\python.exe" -m unittest discover -s "%ROOT%\tests"
if errorlevel 1 goto :fail

echo.
echo === Build GUI executable ===
if not exist "%ROOT%\build\spec" mkdir "%ROOT%\build\spec"
if not exist "%ROOT%\build\work" mkdir "%ROOT%\build\work"
if not exist "%ROOT%\dist" mkdir "%ROOT%\dist"
"%ROOT%\.venv-build\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --noconsole ^
  --name ClaudeEnvSetup ^
  --paths "%ROOT%" ^
  --manifest "%ROOT%\build\app.manifest" ^
  --version-file "%ROOT%\build\version_info.txt" ^
  --noupx ^
  --distpath "%ROOT%\dist" ^
  --workpath "%ROOT%\build\work" ^
  --specpath "%ROOT%\build\spec" ^
  "%ROOT%\ClaudeEnvSetup.py"
if errorlevel 1 goto :fail

echo.
echo === Build console diagnostic executable ===
if not exist "%ROOT%\build\spec-console" mkdir "%ROOT%\build\spec-console"
if not exist "%ROOT%\build\work-console" mkdir "%ROOT%\build\work-console"
"%ROOT%\.venv-build\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --console ^
  --name ClaudeEnvSetup-console ^
  --paths "%ROOT%" ^
  --noupx ^
  --distpath "%ROOT%\dist" ^
  --workpath "%ROOT%\build\work-console" ^
  --specpath "%ROOT%\build\spec-console" ^
  "%ROOT%\ClaudeEnvSetup.py"
if errorlevel 1 goto :fail

echo.
echo === Build complete ===
dir /b "%ROOT%\dist\ClaudeEnvSetup*.exe"
popd
exit /b 0

:fail
echo.
echo [ERROR] Build failed
popd
exit /b 1
