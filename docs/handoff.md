# Handoff: Subtitle Maker

## สถานะ (2026-11-09)
Pipeline ครบ 5 ขั้น + CLI + GUI (PySide6) ผ่าน pytest 12 ข้อ และ `tools/ui_smoke.py` (ลากวาง → ทำทั้งหมด → แก้แถว → ส่งออก)
ทดสอบจริงด้วยเสียงอังกฤษสังเคราะห์ (Windows SAPI) กับ Whisper `base` บน GPU + Gemma-4-E4B แปลไทยได้
**ยังไม่ได้ทดสอบ:** เสียงญี่ปุ่นจริง, วิดีโอยาว, โมเดล large-v3-turbo (ยังไม่ได้ดาวน์โหลด), VRAM สูงสุด, หน้าต่างบนจอจริง (smoke ใช้ offscreen เห็นแค่เลย์เอาต์)

## โครงสร้าง
- `subtitle_maker/core/`: audio, asr, segment, translate, thai_lines, srt, project, settings, pipeline (`StepRunner`)
- `subtitle_maker/ui/`: main_window, settings_dialog, worker (QThread)
- `project.json` ต่อวิดีโอใน `work/<ชื่อ>/` (segments, ภาษา, สถานะขั้น); `Seg.locked` = แก้เองแล้ว

## จุดที่ต้องระวัง
- **faster-whisper 1.2.1 + PyAV 19**: `decode_audio` ส่ง `metadata_errors` ให้ `av.open` แล้วพัง จึงอ่าน wav เป็น numpy เองใน `asr.read_wav` ห้ามส่ง path ให้ `transcribe` ตรงๆ
- GPU: ใช้ DLL cuBLAS/cuDNN จาก wheel `nvidia-*-cu12` (ไม่ลง torch) `asr._add_cuda_dll_dirs` เพิ่ม path ให้เอง ถ้าโหลด CUDA ไม่ได้จะถอยไป CPU int8 พร้อมพิมพ์เตือนที่ stderr
- 8 GB VRAM: ASR ปล่อยโมเดลเมื่อเสร็จ ขั้นแปลสั่ง Ollama `keep_alive: 0` เมื่อเสร็จ ห้ามให้ทั้งสองอยู่พร้อมกัน
- Gemma ตอบว่างเมื่อบังคับ JSON schema: `translate._translate_chunk` ถามซ้ำแบบอิสระ (วิธีเดียวกับ MangaTranslator)
- ห้ามส่งไทยที่มีวรรคตอนให้ newmm ทั้งก้อน `thai_lines.tokenize` แยกส่วนไทยก่อน
- ห้ามต่อ signal จาก worker thread เข้า lambda ต่อเข้าเมธอดของหน้าต่างเท่านั้น
- ตอนรันขั้นใหม่ `asr` จะแทนที่ segments ทั้งหมด (รวมแถวที่ 🔒) ขั้น `translate` ข้ามแถวที่ล็อก

## UI แบบ Subtitle Edit (2026-11-09)
- `ui/main_window.py` (ตาราง+กล่องแก้ไข+วิดีโอ+แท็บ+คลื่นเสียง), `ui/widgets.py` (TimeSpin, VideoView ซ้อนซับ, Waveform ลากขอบ/ตัวกรอบ), `core/edit.py` (Editor: undo/redo, แยก/รวม/แทรก/ลบ, ค้นหา, นำเข้า .srt ไม่มี Qt เทสต์ง่าย)
- `core/export.py`: burn-in ใช้ฟิลเตอร์ `subtitles` รันใน temp dir ด้วยชื่อไฟล์เรียบง่าย (path ที่มี `:` `[` ช่องว่างทำให้ฟิลเตอร์พัง); FontSize/MarginV อยู่ในหน่วยแคนวาส ASS 288 ไม่ใช่พิกเซล
- Qt ไม่วาดเฟรมวิดีโอจนกว่าจะ play: `on_media_status` play แล้ว pause หลังโหลด
- เปิดโปรแกรมด้วย PowerShell `Start-Process -ArgumentList` ต้องครอบพาธที่มีช่องว่างด้วยเครื่องหมายคำพูดเอง ไม่งั้น argv ถูกตัด
- `Seg.locked` = แก้คำแปลเอง; เวลาที่แก้ในตารางไม่ถูก fix_timing ทับตอนกด Ctrl+S (ใช้ `write_as_is`) แต่ขั้น 5 ของ pipeline ยัง retime
- หาชื่อด้วยโมเดล (Gemma-4-E4B) พลาดได้: ยูมิ→ยูกิ, ทาคาชิ→ทาคาสึ, มีอักษรเกาหลีปน จึงต้องผ่านหน้าต่างตรวจก่อนเสมอ

## ไอเดียรอบหน้า
- แบ่ง segment ยาวของ Whisper (เช่น 7 วินาที 2 ชุดประโยค) ตามรอยต่อประโยคด้วย word timestamps
- ตรวจภาษาต่อ segment สำหรับวิดีโอผสม ญี่ปุ่น+อังกฤษ (ตอนนี้ตรวจภาษาครั้งเดียวต่อไฟล์)
- แก้เวลาเริ่ม/จบในตาราง, แยก/รวมแถว, burn-in ซับลงวิดีโอด้วย ffmpeg
- เตือนแถวที่อ่านไม่ทัน (CPS สูงเกิน `max_cps`) ในตาราง
