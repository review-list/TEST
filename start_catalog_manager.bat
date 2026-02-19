@echo off
cd /d %~dp0
REM Try windowed launcher first
where pyw >nul 2>nul
if %errorlevel%==0 (
  pyw -3 CatalogManager_GUI.pyw
  exit /b
)
where pythonw >nul 2>nul
if %errorlevel%==0 (
  pythonw CatalogManager_GUI.pyw
  exit /b
)
REM Fallback (may show console)
python CatalogManager_GUI.pyw
