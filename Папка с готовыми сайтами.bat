@echo off
cd /d "%~dp0"
if not exist sites mkdir sites
start "" "%~dp0sites"
