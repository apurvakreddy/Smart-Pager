"""
SmartPager - Audio Capture Module (Raspberry Pi 4B)
Record audio from I2S SPH0645 mic and upload to SmartPager server.
Receives TTS audio summary and plays through I2S MAX98357A speaker.

Hardware:
- Raspberry Pi 4B running Raspberry Pi OS
- I2S SPH0645 MEMS microphone (audio input)
- I2S MAX98357A mono amplifier + 8Ω speaker (TTS output)
- Momentary button on GPIO 11 (recording trigger)
- Status LED on GPIO 26

Supported voice commands:
- Add events: "Add meeting Monday at 2pm", "Schedule lunch tomorrow at noon"
- Query day: "What's on Monday?", "What do I have today?"
- Query week: "What does my week look like?"
- Delete: "Cancel dentist appointment", "Remove gym on Tuesday"
- Clear: "Clear Monday", "Start fresh"
- Help: "What can you do?"

Run with:
    cd smartPager
    python3 pi/audio_capture_rpi.py
"""

import io
import os
import time
import wave
import sys
import base64
import tempfile
from datetime import datetime
from typing import Tuple, Optional

import requests
import sounddevice as sd
import RPi.GPIO as GPIO  # Install with: sudo apt-get install python3-rpi.gpio

# Configure SDL audio BEFORE importing pygame
# Set to your I2S audio device (card 3 for MAX98357A)
os.environ['SDL_AUDIODRIVER'] = 'alsa'
os.environ['AUDIODEV'] = 'hw:3,0'

import pygame  # For TTS audio playback
from smart_display import SmartDisplay

# ==================== SERVER CONFIGURATION ====================

# Point this at your Flask server (/upload endpoint)
SERVER_URL = "http://34.132.9.223:5000/upload"  # e.g. "http://192.168.1.10:5000/upload"

# ==================== GPIO CONFIGURATION ====================

# Use BCM numbering
BUTTON_GPIO = 11  # Physicacl pin 23  (SPI0 SCLK)
LED_GPIO = 26     # Physical pin 35  (PCM FS)

GPIO.setmode(GPIO.BCM)
GPIO.setup(BUTTON_GPIO, GPIO.IN, pull_up_down=GPIO.PUD_UP)  # Active-LOW
GPIO.setup(LED_GPIO, GPIO.OUT)
GPIO.output(LED_GPIO, GPIO.LOW)

# ==================== AUDIO CONFIGURATION ====================

SAMPLE_RATE = 48000       # 48 kHz (most USB mics support this)
CHANNELS = 1              # Mono
SAMPLE_WIDTH = 2          # bytes (16-bit)
BLOCK_FRAMES = 1024       # Frames per read from the stream
MAX_RECORDING_SECS = 15   # Hard cap, like the ESP32 version

# Input device index - will be detected at startup
# Set to None to use default, or specify index manually
INPUT_DEVICE = None  # Will be auto-detected


def list_audio_devices():
    """List all available audio devices for debugging."""
    print("\n🔍 Available audio devices:")
    print("-" * 60)
    devices = sd.query_devices()
    for i, dev in enumerate(devices):
        # Mark input devices with [IN], output with [OUT]
        caps = []
        if dev['max_input_channels'] > 0:
            caps.append(f"IN:{dev['max_input_channels']}ch")
        if dev['max_output_channels'] > 0:
            caps.append(f"OUT:{dev['max_output_channels']}ch")
        caps_str = ", ".join(caps)
        default_marker = ""
        if i == sd.default.device[0]:
            default_marker = " << DEFAULT INPUT"
        elif i == sd.default.device[1]:
            default_marker = " << DEFAULT OUTPUT"
        print(f"  [{i}] {dev['name']} ({caps_str}){default_marker}")
    print("-" * 60)
    return devices


def find_input_device():
    """
    Find a suitable I2S input device (SPH0645 microphone).
    Returns device index or None for default.
    """
    devices = sd.query_devices()
    
    # Keywords that suggest an I2S microphone (SPH0645, etc.)
    i2s_keywords = ['i2s', 'sph0645', 'inmp441', 'ics-43434', 'googlevoicehat', 
                    'voicehat', 'simple-card', 'hifiberry', 'snd_rpi']
    # Keywords to avoid (HDMI, headphone outputs, etc.)
    avoid_keywords = ['hdmi', 'bcm2835', 'headphones', 'analog', 'vc4']
    
    candidates = []
    
    for i, dev in enumerate(devices):
        # Only consider input devices
        if dev['max_input_channels'] < 1:
            continue
            
        name_lower = dev['name'].lower()
        
        # Skip devices that are clearly not mics
        if any(kw in name_lower for kw in avoid_keywords):
            continue
        
        # Assign priority based on device type
        priority = 0
        if any(kw in name_lower for kw in i2s_keywords):
            priority = 20  # I2S mics get highest priority
        else:
            priority = 5   # Other input devices
        
        candidates.append((priority, i, dev['name']))
    
    # Sort by priority (highest first)
    candidates.sort(reverse=True)
    
    if candidates:
        priority, idx, name = candidates[0]
        device_type = "I2S" if priority >= 20 else "Generic"
        print(f"✅ Selected input device [{idx}]: {name} ({device_type})")
        return idx
    
    print("⚠️  No I2S mic found, using default input device")
    return None

# ==================== LED HELPERS ====================

def led_on() -> None:
    GPIO.output(LED_GPIO, GPIO.HIGH)

def led_off() -> None:
    GPIO.output(LED_GPIO, GPIO.LOW)

def led_pulse(count: int = 3, on_ms: int = 100, off_ms: int = 100) -> None:
    """Simple blink pattern to signal save/upload/etc."""
    for _ in range(count):
        led_on()
        time.sleep(on_ms / 1000.0)
        led_off()
        time.sleep(off_ms / 1000.0)

# ==================== BUTTON HELPER ====================

def button_pressed() -> bool:
    """
    Return True when button is currently pressed.
    Wiring is active-LOW with internal pull-up.
    """
    return GPIO.input(BUTTON_GPIO) == GPIO.LOW

# ==================== AUDIO CAPTURE (RAM ONLY) ====================

def record_audio_to_ram() -> Tuple[io.BytesIO, float]:
    """
    Record audio into an in-memory WAV (BytesIO), not to disk.

    Returns:
        (wav_buffer, duration_seconds)
        - wav_buffer: BytesIO positioned at start, ready to .read()
        - duration_seconds: approximate length of recording
    """
    print("\n[RECORDING STARTED - Storing audio in RAM]")
    led_on()

    frames = []  # list of raw PCM chunks (bytes)
    start_time = time.time()

    # Open raw input stream (16-bit mono) on the selected device
    with sd.RawInputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype="int16",
        blocksize=BLOCK_FRAMES,
        device=INPUT_DEVICE,  # Use the detected/configured input device
    ) as stream:
        while button_pressed():
            now = time.time()
            if now - start_time > MAX_RECORDING_SECS:
                print("Max recording duration reached")
                break

            data, overflowed = stream.read(BLOCK_FRAMES)
            if overflowed:
                print("⚠️  Input overflow!", file=sys.stderr)
            if data:
                # data is a bytes-like object for RawInputStream
                frames.append(bytes(data))

            # Small sleep to yield CPU
            time.sleep(0.001)

    led_off()
    end_time = time.time()
    duration = end_time - start_time

    # Concatenate raw PCM frames
    pcm_data = b"".join(frames)
    if not pcm_data:
        print("Recording too short; no data captured.")
        return None, 0.0  # type: ignore[return-value]

    print(f"[RECORDING STOPPED] Duration: {duration:.2f}s, Raw size: {len(pcm_data)} bytes")

    # Build WAV in memory
    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm_data)

    # Rewind buffer so it's ready to be read
    wav_buffer.seek(0)

    # Drop raw data to free RAM (we only keep the WAV in memory)
    del frames
    del pcm_data

    print(f"WAV size in RAM: {len(wav_buffer.getvalue())} bytes")
    return wav_buffer, duration

# ==================== TTS AUDIO PLAYBACK ====================

# Initialize pygame mixer for audio playback
# We rely on pygame.mixer.get_init() instead of a global flag

def init_audio_playback():
    """Initialize pygame mixer for audio playback through I2S (card 3)."""
    try:
        # Check if mixer is already initialized
        if pygame.mixer.get_init():
            return True
            
        # Re-assert env vars just in case
        os.environ['SDL_AUDIODRIVER'] = 'alsa'
        os.environ['AUDIODEV'] = 'hw:3,0'
        
        # Use pre_init to ensure settings stick
        pygame.mixer.pre_init(frequency=22050, size=-16, channels=1, buffer=4096)
        pygame.mixer.init()
        
        # Give the audio device a moment to settle
        time.sleep(0.2)
        
        print(f"🔊 Audio playback initialized (device: {os.environ.get('AUDIODEV', 'default')})")
        return True
    except Exception as e:
        print(f"⚠️  Failed to initialize audio playback: {e}")
        return False


def play_tts_audio(audio_data: bytes) -> bool:
    """
    Play TTS audio through the I2S speaker.
    
    Args:
        audio_data: Raw WAV file bytes
        
    Returns:
        True if playback successful, False otherwise
    """
    if not init_audio_playback():
        return False
    
    # Write audio to temp file (pygame needs a file)
    temp_path = os.path.join(tempfile.gettempdir(), "smartpager_tts_response.wav")
    
    try:
        # Save audio data to temp file
        with open(temp_path, 'wb') as f:
            f.write(audio_data)
        
        print(f"🔊 Playing TTS audio ({len(audio_data)} bytes)...")
        led_on()  # LED on during playback
        
        # Load and play the audio
        pygame.mixer.music.load(temp_path)
        pygame.mixer.music.play()
        
        # Wait for playback to finish
        while pygame.mixer.music.get_busy():
            time.sleep(0.1)
        
        print("🔊 TTS playback complete")
        return True
        
    except Exception as e:
        print(f"❌ TTS playback error: {e}")
        return False
        
    finally:
        led_off()
        # Always quit mixer to release device (critical for I2S shared access)
        try:
            pygame.mixer.quit()
        except:
            pass
            
        # Clean up temp file
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except:
            pass


def handle_tts_response(response_json: dict) -> bool:
    """
    Handle TTS audio from server response.
    Decodes base64 audio and plays it.
    
    Args:
        response_json: JSON response from server containing 'tts_audio' field
        
    Returns:
        True if TTS was played, False otherwise
    """
    if 'tts_audio' not in response_json:
        print("ℹ️  No TTS audio in server response")
        return False
    
    try:
        # Decode base64 audio
        audio_base64 = response_json['tts_audio']
        audio_data = base64.b64decode(audio_base64)
        
        print(f"📥 Received TTS audio: {len(audio_data)} bytes")
        
        # Play the audio
        return play_tts_audio(audio_data)
        
    except Exception as e:
        print(f"❌ Error handling TTS response: {e}")
        return False


# ==================== UPLOAD TO SERVER ====================

def upload_audio_buffer(wav_buffer: io.BytesIO, duration: float, display: Optional[SmartDisplay] = None) -> bool:
    """
    Upload in-RAM WAV buffer to SmartPager Flask server.
    Receives and plays TTS audio response.

    Uses multipart/form-data with field name 'audio' to match /upload.
    Server ignores our filename and renames it to recording_XXX.wav.
    
    Includes client_datetime for proper day resolution (e.g., "today", "tomorrow").
    
    After successful upload, the server processes the audio and returns
    a TTS audio summary which is played through the I2S speaker.
    """
    if wav_buffer is None:
        return False

    print("📤 Uploading recording to server...")
    if display:
        display.show_text("Uploading...")
    led_on()
    try:
        wav_buffer.seek(0)
        
        # Include current datetime for day resolution
        client_datetime = datetime.now().isoformat()
        
        files = {
            "audio": ("recording_pi.wav", wav_buffer, "audio/wav"),
        }
        data = {
            "client_datetime": client_datetime,
        }
        
        # Also send datetime in header for streaming uploads
        headers = {
            "X-Client-Datetime": client_datetime,
        }
        
        # Increased timeout for processing (Whisper + LLM + TTS can take time)
        # Increased to 180s to prevent client-side timeout during heavy server load
        resp = requests.post(SERVER_URL, files=files, data=data, headers=headers, timeout=180)

        led_off()
        if resp.status_code == 200:
            response_data = resp.json()
            print("✅ Upload & processing successful!")
            
            # Show upload info
            upload_info = response_data.get('upload', {})
            print(f"   📁 Server filename: {upload_info.get('filename', 'unknown')}")
            print(f"   💾 Size (server): {upload_info.get('size_bytes', 0)} bytes")
            print(f"   ⏱️  Duration (local): {duration:.2f}s")
            
            # Show intent classification
            intent = response_data.get('intent', 'unknown')
            print(f"   🎯 Intent: {intent}")
            
            # Show processing results
            if response_data.get('transcript'):
                transcript = response_data['transcript']
                preview = transcript[:80] + "..." if len(transcript) > 80 else transcript
                print(f"   📝 Transcript: {preview}")
            
            # Show response text (what will be spoken)
            response_text = response_data.get('response_text') or response_data.get('summary')
            if response_text:
                preview = response_text[:80] + "..." if len(response_text) > 80 else response_text
                print(f"   💬 Response: {preview}")
                if display:
                    display.show_text(preview[:20] + "...")
            
            # Update Display Schedule if available
            # Check for 'agenda' or 'schedule' in response
            # Use safe access: .get() returns None if missing, so we default to {} if None
            agenda_data = response_data.get('agenda') or {}
            schedule_data = response_data.get('schedule') or {}
            
            events = agenda_data.get('events') or schedule_data.get('events')
            if events and display:
                display.update_schedule(events)
            
            # Show affected days if any
            affected_days = response_data.get('affected_days', [])
            if affected_days:
                print(f"   📅 Affected days: {', '.join(affected_days)}")
            
            # Play TTS audio if available
            if response_data.get('tts_audio'):
                print("\n🔊 Playing response...")
                if display:
                    display.show_text("Speaking...")
                handle_tts_response(response_data)
                # Restore schedule display after speaking
                if events and display:
                    display.update_schedule(events)
                elif display:
                    display.show_text("Ready")
            else:
                print("   ℹ️  No TTS audio in response")
                led_pulse(3)
                if display:
                    display.show_text("Done")
            
            # Refresh schedule
            if display:
                fetch_week_schedule(display)
            return True
        else:
            print(f"❌ Upload failed: HTTP {resp.status_code}")
            if display:
                display.show_text(f"Error: {resp.status_code}")
            try:
                error_data = resp.json()
                print(f"   Error: {error_data.get('error', 'Unknown error')}")
            except:
                print(resp.text[:200])
            return False
    except requests.exceptions.Timeout:
        led_off()
        print("❌ Upload timeout - server may be processing slowly")
        if display:
            display.show_text("Timeout")
        return False
    except Exception as e:
        led_off()
        print(f"❌ Upload error: {e}")
        if display:
            display.show_text("Error")
        return False

def fetch_week_schedule(display: Optional[SmartDisplay] = None):
    """Fetch the full week's schedule and update the display."""
    if not display:
        return

    print("📅 Fetching week schedule...")
    # Base URL from SERVER_URL (remove /upload)
    base_url = SERVER_URL.rsplit('/', 1)[0]
    
    try:
        resp = requests.get(f"{base_url}/api/schedule/week", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get('success'):
                print("✅ Schedule updated")
                display.update_week_schedule(data)
            else:
                print(f"⚠️ Schedule fetch failed: {data.get('error')}")
        else:
            print(f"⚠️ Schedule fetch HTTP {resp.status_code}")
    except Exception as e:
        print(f"❌ Schedule fetch error: {e}")

# ==================== MAIN LOOP ====================

def main() -> None:
    global INPUT_DEVICE
    
    print("=" * 60)
    print("🎤 SmartPager Audio Capture - Raspberry Pi 4B")
    print("   with TTS Playback Support")
    print("=" * 60)
    
    # Initialize audio playback (for TTS) - Just check if we can init, then quit
    print("\n🔊 Checking audio playback...")
    if init_audio_playback():
        print("   ✅ Audio playback ready (I2S/ALSA)")
        pygame.mixer.quit() # Release immediately
    else:
        print("   ⚠️  Audio playback not available - TTS will be disabled")
    
    # List all audio devices for debugging
    list_audio_devices()
    
    # Auto-detect input device if not manually configured
    if INPUT_DEVICE is None:
        INPUT_DEVICE = find_input_device()
    
    # Show selected device info
    if INPUT_DEVICE is not None:
        dev_info = sd.query_devices(INPUT_DEVICE)
        print(f"\n🎤 Using input device [{INPUT_DEVICE}]: {dev_info['name']}")
        print(f"   Max channels: {dev_info['max_input_channels']}")
        print(f"   Default sample rate: {dev_info['default_samplerate']} Hz")
    else:
        print("\n🎤 Using system default input device")
    
    print("\n" + "=" * 60)
    print(f"📊 AUDIO CONFIG:")
    print(f"   Sample Rate: {SAMPLE_RATE} Hz")
    print(f"   Channels: {CHANNELS}")
    print(f"   Max Recording: {MAX_RECORDING_SECS} seconds")
    print(f"\n🌐 SERVER:")
    print(f"   URL: {SERVER_URL}")
    print("=" * 60)
    print("\n🎯 VOICE COMMANDS:")
    print("   • Add events:  'Add meeting Monday 2pm'")
    print("   • Query day:   'What's on Monday?' or 'What do I have today?'")
    print("   • Query week:  'What does my week look like?'")
    print("   • Delete:      'Cancel my dentist appointment'")
    print("   • Clear:       'Clear Monday' or 'Start fresh'")
    print("   • Help:        'What can you do?'")
    print("=" * 60)
    print("\n💡 USAGE:")
    print("   1. Hold the button to speak your command")
    print("   2. Release to stop and process")
    print("   3. Wait for TTS response playback")
    print("   Press Ctrl+C to exit.\n")

    # Initialize Display
    print("\n🖥️  Initializing SmartDisplay...")
    try:
        display = SmartDisplay()
        print("   ✅ Display initialized")
    except Exception as e:
        print(f"   ⚠️  Display init failed: {e}")
        display = None

    if display:
        fetch_week_schedule(display)

    recording_index = 0

    try:
        while True:
            # Wait for button press (active LOW)
            if button_pressed():
                # Debounce
                time.sleep(0.05)
                if not button_pressed():
                    continue

                recording_index += 1
                print(f"\n--- Recording #{recording_index} ---")
                if display:
                    display.show_text("Listening...")

                # Capture audio into RAM
                wav_buffer, duration = record_audio_to_ram()

                if wav_buffer is None or duration < 0.3:
                    print("Recording discarded (too short).")
                    led_pulse(count=2)
                    if display:
                        display.show_text("Too Short")
                    continue

                # Upload to server
                success = upload_audio_buffer(wav_buffer, duration, display)
                if not success:
                    print("⚠️  Upload failed; recording existed only in RAM and is now discarded.")

                print("Ready for next recording...\n")

            time.sleep(0.02)
    except KeyboardInterrupt:
        print("\nStopping SmartPager Pi capture...")
        if display:
            display.cleanup()
    finally:
        led_off()
        GPIO.cleanup()
        # Clean up pygame
        try:
            pygame.mixer.quit()
        except:
            pass

if __name__ == "__main__":
    main()
