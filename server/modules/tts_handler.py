# smartPager/server/modules/tts_handler.py
"""
Text-to-Speech handler using Piper TTS.
Generates audio files from text for ESP32 playback.
"""

import os
import wave
from typing import Optional
from pathlib import Path

# Resolve model path relative to this file
BASE_DIR = Path(__file__).resolve().parent.parent
MODEL_PATH = BASE_DIR / "models" / "tts" / "en_US-amy-medium.onnx"

_model = None
_tts_available = None


def is_tts_available() -> bool:
    """Check if TTS is available (Piper installed and model exists)"""
    global _tts_available
    
    if _tts_available is not None:
        return _tts_available
    
    # Check if piper is installed
    try:
        from piper.voice import PiperVoice
    except ImportError:
        print("[tts_handler] Piper TTS not installed. Run: pip install piper-tts")
        _tts_available = False
        return False
    
    # Check if model exists
    if not MODEL_PATH.exists():
        print(f"[tts_handler] TTS model not found at: {MODEL_PATH}")
        print("[tts_handler] TTS will be disabled. Download model to enable.")
        _tts_available = False
        return False
    
    _tts_available = True
    return True


def _load_model():
    """Load the Piper voice model once and cache it."""
    global _model
    
    if not is_tts_available():
        return None
        
    if _model is None:
        from piper.voice import PiperVoice
        print(f"[tts_handler] Loading TTS model from: {MODEL_PATH}")
        _model = PiperVoice.load(str(MODEL_PATH))
        print("[tts_handler] TTS model loaded.")
    
    return _model


def synthesize_speech(text: str, output_path: str) -> Optional[str]:
    """
    Generate a WAV file from text using the Piper TTS model.
    
    Args:
        text: Text to synthesize
        output_path: Path for output WAV file
        
    Returns:
        Path to generated audio file, or None if TTS unavailable
    """
    voice = _load_model()
    
    if voice is None:
        print("[tts_handler] TTS not available, skipping synthesis")
        return None

    # Ensure output directory exists
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    print(f"[tts_handler] Synthesizing speech: '{text[:50]}...'")

    # Get generator of AudioChunk objects
    gen = voice.synthesize(text)

    # Get first chunk (to read metadata)
    first_chunk = next(gen, None)
    if first_chunk is None:
        print("[tts_handler] Piper returned no audio for given text.")
        return None

    # Open WAV file and configure parameters according to Piper metadata
    with wave.open(output_path, "wb") as wav_file:
        wav_file.setnchannels(first_chunk.sample_channels)
        wav_file.setsampwidth(first_chunk.sample_width)
        wav_file.setframerate(first_chunk.sample_rate)

        # Write first chunk
        wav_file.writeframes(first_chunk.audio_int16_bytes)

        # Write remaining chunks
        for chunk in gen:
            wav_file.writeframes(chunk.audio_int16_bytes)

    print(f"[tts_handler] Audio saved to: {output_path}")
    return output_path

