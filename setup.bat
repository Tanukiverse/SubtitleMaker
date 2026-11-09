@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul || (echo ไม่พบ Python 3.12 ^(py launcher^) & pause & exit /b 1)
where ffmpeg >nul 2>nul || (echo ไม่พบ ffmpeg ใน PATH: ติดตั้งก่อน เช่น winget install Gyan.FFmpeg & pause & exit /b 1)
if not exist ".venv\Scripts\python.exe" py -3.12 -m venv .venv || (pause & exit /b 1)
".venv\Scripts\python.exe" -m pip install --upgrade pip || (pause & exit /b 1)
".venv\Scripts\python.exe" -m pip install -r requirements.txt || (pause & exit /b 1)
echo.
echo ติดตั้งเสร็จ. โมเดล Whisper จะดาวน์โหลดเองครั้งแรกที่ใช้ ^(large-v3-turbo ราว 1.6 GB^) ลงในโฟลเดอร์ models\whisper
echo ต้องเปิด Ollama และดึงโมเดลแปลไว้ด้วย เช่น:
echo   ollama pull hf.co/HauhauCS/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive:Q4_K_M
pause
