# smartPager/server/modules/whisper_handler.py

import os
from pathlib import Path
from typing import Optional

# Lazy load whisper to avoid startup delay if not needed
_model = None

def _get_model():
    """Load Whisper model lazily (first call only)"""
    global _model
    if _model is None:
        import whisper
        print("[whisper_handler] Loading Whisper model (this may take a moment)...")
        _model = whisper.load_model("base")
        print("[whisper_handler] Whisper model loaded.")
    return _model


def transcribe_audio_file(audio_path: str) -> Optional[str]:
    """
    Transcribe an audio file using local Whisper.
    
    Args:
        audio_path: Full path to the audio file
        
    Returns:
        Transcribed text or None if failed
    """
    path = Path(audio_path)
    
    if not path.exists():
        print(f"[whisper_handler] Audio file not found: {audio_path}")
        return None

    if path.suffix.lower() not in [".wav", ".mp3", ".m4a", ".flac", ".ogg"]:
        print(f"[whisper_handler] Unsupported audio format: {path.suffix}")
        return None

    print(f"[whisper_handler] Transcribing: {path.name}")

    try:
        model = _get_model()
        result = model.transcribe(str(path))

        if "text" not in result:
            print("[whisper_handler] Whisper did not return text output.")
            return None

        transcript = result["text"].strip()
        print(f"[whisper_handler] Transcription complete: '{transcript[:50]}...'")
        return transcript
        
    except Exception as e:
        print(f"[whisper_handler] Transcription error: {e}")
        return None
