@echo off
rem Run the workbench from a source checkout, in the project's uv environment.
rem Set it up first: see "Build from source" in README.md.
setlocal
cd /d "%~dp0"
uv run anolis-workbench %*
