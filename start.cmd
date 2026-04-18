@echo off
setlocal

where python >nul 2>nul
if %errorlevel%==0 (
    python -m anolis_workbench.server.app
) else (
    python3 -m anolis_workbench.server.app
)
