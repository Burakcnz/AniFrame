@echo off
REM AniFrame Native Messaging Host
REM Chrome/Edge/Firefox native messaging host launcher
REM Protocol: 4-byte length prefix + JSON message over stdio

cd /d "%~dp0"
call python -m backend.native_messaging
