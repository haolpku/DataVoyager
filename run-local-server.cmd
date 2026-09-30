@echo off
cd /d "%~dp0"
start "DataVoyager Local" /b "D:\anaconda\envs\dataflow-agents\python.exe" -m dataflowwebagent.chat.server --root "C:\Users\86130\Desktop\dataflowwebagent\DataVoyager\runs\chat" --port 8765
