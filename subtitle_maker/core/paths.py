from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = ROOT / "settings.json"
MODELS_DIR = ROOT / "models"
WHISPER_DIR = MODELS_DIR / "whisper"
