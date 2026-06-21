@echo off
cd /d C:\Users\Pc\youtube-clipper-bot
:loop
python -m bot
echo Bot crashed at %time%, restarting in 5 seconds...
timeout /t 5
goto loop
