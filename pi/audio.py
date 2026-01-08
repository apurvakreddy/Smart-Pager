#!/usr/bin/env python3
"""
Combined audio capture + OLED display controller for SmartPager on Raspberry Pi.

Features:
- Audio capture (hold BCM11) -> upload to server /upload with TTS playback.
- OLED lift-to-wake via IMU, display toggle (BCM4).
- Task button (BCM5) shows current/next task (≤48 chars) from /api/schedule/today.
- Scroll mode (BCM6 toggle): scroll list of today's events (BCM5 down, BCM4 top).
"""

import base64
import io
import math
import os
import sys
import tempfile
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

# Set SDL audio vars before importing pygame
os.environ["SDL_AUDIODRIVER"] = "alsa"
os.environ["AUDIODEV"] = "hw:3,0"

import pygame  # noqa: E402
import requests  # noqa: E402
import sounddevice as sd  # noqa: E402
import RPi.GPIO as GPIO  # noqa: E402

# ---------- I2C + ISM330DLC setup ----------
try:
    import smbus2 as smbus
except ImportError:
    import smbus

I2C_BUS_NUMBER = 1          # Raspberry Pi 4B uses bus 1 for I2C1
ISM330_ADDR = 0x6A          # SA0/SDO tied to GND -> address 1101010b

# Register addresses
REG_WHO_AM_I = 0x0F
REG_CTRL1_XL = 0x10
REG_CTRL3_C = 0x12
REG_OUTX_L_XL = 0x28

# Accelerometer sensitivity at ±2 g: 0.061 mg/LSB
ACC_SENSITIVITY_2G = 0.061 / 1000.0  # g per LSB

bus = smbus.SMBus(I2C_BUS_NUMBER)


def twos_complement(val, bits):
    """Convert unsigned integer to signed two's complement."""
    if val & (1 << (bits - 1)):
        val -= 1 << bits
    return val


def read_register(reg):
    return bus.read_byte_data(ISM330_ADDR, reg)


def write_register(reg, value):
    bus.write_byte_data(ISM330_ADDR, reg, value)


def init_ism330dlc():
    """Initialize ISM330DLC accelerometer for basic polling."""
    who = read_register(REG_WHO_AM_I)
    if who != 0x6A:
        print(f"Warning: WHO_AM_I = 0x{who:02X}, expected 0x6A")

    # CTRL3_C: set BDU=1 (block data update), keep other bits as default
    ctrl3 = read_register(REG_CTRL3_C)
    ctrl3 |= 1 << 6  # BDU bit
    write_register(REG_CTRL3_C, ctrl3)

    # CTRL1_XL: ODR=52 Hz, FS=±2 g
    write_register(REG_CTRL1_XL, 0x30)
    print("ISM330DLC initialized (ACC: ±2 g @ 52 Hz)")


def read_accel_g():
    """Read accelerometer data and return (ax, ay, az) in g."""
    data = bus.read_i2c_block_data(ISM330_ADDR, REG_OUTX_L_XL, 6)
    x_raw = twos_complement(data[1] << 8 | data[0], 16)
    y_raw = twos_complement(data[3] << 8 | data[2], 16)
    z_raw = twos_complement(data[5] << 8 | data[4], 16)
    ax = x_raw * ACC_SENSITIVITY_2G
    ay = y_raw * ACC_SENSITIVITY_2G
    az = z_raw * ACC_SENSITIVITY_2G
    return ax, ay, az


# ---------- Lift-to-wake logic ----------
def is_vertical_for_view(ax, ay, az):
    """
    Detect roughly vertical orientation for lift-to-wake.
    """
    g_total = math.sqrt(ax * ax + ay * ay + az * az)
    if g_total < 0.7 or g_total > 1.3:
        return False
    vertical_component = max(abs(ax), abs(ay))
    return abs(az) < 0.5 and vertical_component > 0.7


# ---------- OLED display setup (luma.oled SSD1306) ----------
from luma.core.interface.serial import i2c as luma_i2c  # noqa: E402
from luma.core.render import canvas  # noqa: E402
from luma.oled.device import ssd1306  # noqa: E402

serial = luma_i2c(port=1, address=0x3C)  # change addr if needed
display = ssd1306(serial, width=128, height=32)
display_on = False


def show_text(text: str, x: int = 0, y: int = 0) -> None:
    """Draw a single line of text at (x, y)."""
    with canvas(display) as draw:
        draw.text((x, y), text, fill=255)


def turn_off_display():
    """Turn the panel off and reset our flag."""
    global display_on
    display.hide()
    display_on = False


def turn_on_display():
    """Turn the panel on, show something, and set the flag."""
    global display_on
    display.show()
    show_text("Wake", 0, 0)
    display_on = True


# ---------- GPIO setup ----------
TOGGLE_BUTTON_PIN = 4   # display toggle
TASK_BUTTON_PIN = 5     # current/next task
SCROLL_BUTTON_PIN = 6   # scroll mode toggle
BUTTON_GPIO = 11        # record button
LED_GPIO = 26           # status LED

GPIO.setmode(GPIO.BCM)
GPIO.setup(TOGGLE_BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(TASK_BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(SCROLL_BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(BUTTON_GPIO, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(LED_GPIO, GPIO.OUT)
GPIO.output(LED_GPIO, GPIO.LOW)

# Button edge tracking
button_last_state = {
    TOGGLE_BUTTON_PIN: GPIO.input(TOGGLE_BUTTON_PIN),
    TASK_BUTTON_PIN: GPIO.input(TASK_BUTTON_PIN),
    SCROLL_BUTTON_PIN: GPIO.input(SCROLL_BUTTON_PIN),
    BUTTON_GPIO: GPIO.input(BUTTON_GPIO),
}


# ---------- Helpers ----------
def clamp_text(text: str, limit: int = 48) -> str:
    """Ensure user-visible strings are within the smartwatch's limit."""
    text = text.strip()
    if len(text) <= limit:
        return text
    return (text[: limit - 3].rstrip() + "...")[:limit]


# ---------- Schedule fetch ----------
# Defaults point to the same server used by audio_capture_rpi.py.
DEFAULT_SERVER_BASE = "http://10.207.32.68:8000"
SERVER_BASE_URL = os.getenv("SMARTPAGER_SERVER_BASE", DEFAULT_SERVER_BASE)
SERVER_URL = os.getenv("SMARTPAGER_UPLOAD_URL", f"{SERVER_BASE_URL.rstrip('/')}/upload")


def fetch_today_events_from_server(now: datetime) -> Optional[list]:
    """Try to fetch today's events from the server; return None on failure."""
    url = f"{SERVER_BASE_URL.rstrip('/')}/api/schedule/today"
    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        events = data.get("events", [])
        if not events and isinstance(data.get("schedule"), dict):
            events = data["schedule"].get("events", [])
        return events
    except Exception as e:
        print(f"[button-display] Server fetch failed ({url}): {e}")
        return None


def read_today_events(now: datetime) -> list:
    """Get today's events from the server (no local cache)."""
    events = fetch_today_events_from_server(now)
    return events or []


def pick_current_or_next_event(events: list, now: datetime):
    """
    Return a tuple (status, event, start_dt, end_dt) where:
      status: "now" or "next"
      event: event dict
      start_dt/end_dt: datetime objects
    If nothing upcoming, return None.
    """
    parsed = []
    for event in events:
        try:
            start_dt = datetime.fromisoformat(event.get("start"))
            end_dt = datetime.fromisoformat(event.get("end"))
            parsed.append((start_dt, end_dt, event))
        except Exception:
            continue

    if not parsed:
        return None

    parsed.sort(key=lambda tup: tup[0])

    for start_dt, end_dt, event in parsed:
        if start_dt <= now < end_dt:
            return ("now", event, start_dt, end_dt)

    for start_dt, end_dt, event in parsed:
        if now < start_dt:
            return ("next", event, start_dt, end_dt)

    return None


def ensure_display_on():
    """Make sure the OLED is awake before drawing."""
    global display_on
    if not display_on:
        display.show()
        display_on = True


def show_current_or_next_task():
    """Show the current task or the next one (≤48 chars)."""
    now = datetime.now()
    events = read_today_events(now)
    selection = pick_current_or_next_event(events, now)

    if selection:
        status, event, start_dt, end_dt = selection
        name = event.get("name", "Task")
        if status == "now":
            msg = f"Now {name} {start_dt.strftime('%H:%M')}-{end_dt.strftime('%H:%M')}"
        else:
            msg = f"Next {name} {start_dt.strftime('%H:%M')}"
    else:
        msg = "No upcoming tasks today"

    safe_msg = clamp_text(msg)
    print(f"[button-display] Showing: {safe_msg}")
    ensure_display_on()
    show_text(safe_msg, 0, 0)


def handle_toggle_button_press():
    """Toggle the display on/off when the legacy button (GPIO4) is pressed."""
    global display_on
    if display_on:
        print("Button 4: turning display OFF")
        turn_off_display()
    else:
        print("Button 4: turning display ON")
        turn_on_display()


def handle_task_button_press():
    """Display the current or next task when the task button (GPIO5) is pressed."""
    print("Button 5: show current/next task")
    show_current_or_next_task()


# ---------- Scroll mode ----------
ROW_HEIGHT = 10
VISIBLE_ROWS = display.height // ROW_HEIGHT

scroll_mode = False
scroll_lines = []
scroll_top_index = 0


def build_scroll_lines(now: datetime) -> list:
    """Prepare one-line summaries for today's events."""
    events = read_today_events(now)
    if not events:
        return ["No upcoming tasks today"]

    parsed = []
    for event in events:
        try:
            start_dt = datetime.fromisoformat(event.get("start"))
        except Exception:
            start_dt = None
        parsed.append((start_dt, event))

    parsed.sort(key=lambda tup: tup[0] or datetime.max)

    lines = []
    for start_dt, event in parsed:
        name = clamp_text(event.get("name", "Task"), limit=24)
        if start_dt:
            lines.append(f"{start_dt.strftime('%H:%M')} {name}")
        else:
            lines.append(name)

    return lines or ["No upcoming tasks today"]


def render_scroll_window():
    """Draw the current scroll window."""
    ensure_display_on()
    with canvas(display) as draw:
        for row in range(VISIBLE_ROWS):
            line_index = scroll_top_index + row
            if line_index >= len(scroll_lines):
                break
            draw.text((0, row * ROW_HEIGHT), scroll_lines[line_index], fill=255)


def enter_scroll_mode():
    """Start scroll mode and render from the top."""
    global scroll_mode, scroll_lines, scroll_top_index
    now = datetime.now()
    scroll_lines = build_scroll_lines(now)
    scroll_top_index = 0
    scroll_mode = True
    print("Button 6: scroll mode ON")
    render_scroll_window()


def exit_scroll_mode():
    """Leave scroll mode and return to the normal view."""
    global scroll_mode
    scroll_mode = False
    print("Button 6: scroll mode OFF")
    show_current_or_next_task()


def scroll_down_one_row():
    """Move the scroll window down by one row."""
    global scroll_top_index
    max_top = max(0, len(scroll_lines) - VISIBLE_ROWS)
    if scroll_top_index < max_top:
        scroll_top_index += 1
        print(f"Scroll: down to row {scroll_top_index}")
        render_scroll_window()
    else:
        print("Scroll: already at bottom")


def scroll_to_top():
    """Jump to the top of the scroll content."""
    global scroll_top_index
    scroll_top_index = 0
    print("Scroll: top")
    render_scroll_window()


def handle_scroll_toggle():
    """Toggle scroll mode on/off."""
    if scroll_mode:
        exit_scroll_mode()
    else:
        enter_scroll_mode()


def handle_button_press(pin: int):
    """
    Route button presses based on the current mode.
    - Button 6 toggles scroll mode.
    - While in scroll mode:
        * Button 5 scrolls down one row.
        * Button 4 jumps to the top.
    - Outside scroll mode, buttons 4/5 keep their original behavior.
    """
    if pin == SCROLL_BUTTON_PIN:
        handle_scroll_toggle()
        return

    if scroll_mode:
        if pin == TASK_BUTTON_PIN:
            scroll_down_one_row()
        elif pin == TOGGLE_BUTTON_PIN:
            scroll_to_top()
        return

    if pin == TASK_BUTTON_PIN:
        handle_task_button_press()
    elif pin == TOGGLE_BUTTON_PIN:
        handle_toggle_button_press()


# ---------- Audio capture / TTS upload ----------
SAMPLE_RATE = 48000
CHANNELS = 1
SAMPLE_WIDTH = 2
BLOCK_FRAMES = 1024
MAX_RECORDING_SECS = 15
INPUT_DEVICE = None  # auto-detected

_pygame_initialized = False


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


def button_pressed_record() -> bool:
    """Return True when record button is currently pressed (active-LOW)."""
    return GPIO.input(BUTTON_GPIO) == GPIO.LOW


def list_audio_devices():
    """List all available audio devices for debugging."""
    print("\n🔍 Available audio devices:")
    print("-" * 60)
    devices = sd.query_devices()
    for i, dev in enumerate(devices):
        caps = []
        if dev["max_input_channels"] > 0:
            caps.append(f"IN:{dev['max_input_channels']}ch")
        if dev["max_output_channels"] > 0:
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
    """Find a suitable I2S input device (SPH0645 microphone)."""
    devices = sd.query_devices()
    i2s_keywords = ["i2s", "sph0645", "inmp441", "ics-43434", "googlevoicehat", "voicehat", "simple-card", "hifiberry", "snd_rpi"]
    avoid_keywords = ["hdmi", "bcm2835", "headphones", "analog", "vc4"]

    candidates = []
    for i, dev in enumerate(devices):
        if dev["max_input_channels"] < 1:
            continue
        name_lower = dev["name"].lower()
        if any(kw in name_lower for kw in avoid_keywords):
            continue
        priority = 20 if any(kw in name_lower for kw in i2s_keywords) else 5
        candidates.append((priority, i, dev["name"]))

    candidates.sort(reverse=True)
    if candidates:
        _, idx, name = candidates[0]
        print(f"✅ Selected input device [{idx}]: {name}")
        return idx

    print("⚠️  No I2S mic found, using default input device")
    return None


def record_audio_to_ram() -> Tuple[Optional[io.BytesIO], float]:
    """
    Record audio into an in-memory WAV (BytesIO), not to disk.
    Returns (wav_buffer, duration_seconds).
    """
    print("\n[RECORDING STARTED - Storing audio in RAM]")
    led_on()

    frames = []
    start_time = time.time()

    with sd.RawInputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype="int16",
        blocksize=BLOCK_FRAMES,
        device=INPUT_DEVICE,
    ) as stream:
        while button_pressed_record():
            now = time.time()
            if now - start_time > MAX_RECORDING_SECS:
                print("Max recording duration reached")
                break
            data, overflowed = stream.read(BLOCK_FRAMES)
            if overflowed:
                print("⚠️  Input overflow!", file=sys.stderr)
            if data:
                frames.append(bytes(data))
            time.sleep(0.001)

    led_off()
    end_time = time.time()
    duration = end_time - start_time
    pcm_data = b"".join(frames)
    if not pcm_data:
        print("Recording too short; no data captured.")
        return None, 0.0

    print(f"[RECORDING STOPPED] Duration: {duration:.2f}s, Raw size: {len(pcm_data)} bytes")

    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm_data)

    wav_buffer.seek(0)
    return wav_buffer, duration


def init_audio_playback():
    """Initialize pygame mixer for audio playback through I2S (card 3)."""
    global _pygame_initialized
    if _pygame_initialized:
        return True
    try:
        pygame.mixer.init(frequency=22050, size=-16, channels=1, buffer=4096)
        _pygame_initialized = True
        print(f"🔊 Audio playback initialized (device: {os.environ.get('AUDIODEV', 'default')})")
        return True
    except Exception as e:
        print(f"⚠️  Failed to initialize audio playback: {e}")
        return False


def play_tts_audio(audio_data: bytes) -> bool:
    """Play TTS audio through the I2S speaker."""
    if not init_audio_playback():
        return False
    temp_path = os.path.join(tempfile.gettempdir(), "smartpager_tts_response.wav")
    try:
        with open(temp_path, "wb") as f:
            f.write(audio_data)
        print(f"🔊 Playing TTS audio ({len(audio_data)} bytes)...")
        led_on()
        pygame.mixer.music.load(temp_path)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            time.sleep(0.1)
        led_off()
        print("🔊 TTS playback complete")
        return True
    except Exception as e:
        print(f"❌ TTS playback error: {e}")
        led_off()
        return False
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass


def handle_tts_response(response_json: dict) -> bool:
    """Handle TTS audio from server response (base64)."""
    if "tts_audio" not in response_json:
        print("ℹ️  No TTS audio in server response")
        return False
    try:
        audio_base64 = response_json["tts_audio"]
        audio_data = base64.b64decode(audio_base64)
        print(f"📥 Received TTS audio: {len(audio_data)} bytes")
        return play_tts_audio(audio_data)
    except Exception as e:
        print(f"❌ Error handling TTS response: {e}")
        return False


def upload_audio_buffer(wav_buffer: io.BytesIO, duration: float) -> bool:
    """
    Upload in-RAM WAV buffer to SmartPager Flask server.
    Receives and plays TTS audio response.
    """
    if wav_buffer is None:
        return False

    print("📤 Uploading recording to server...")
    led_on()
    try:
        wav_buffer.seek(0)
        client_datetime = datetime.now().isoformat()
        files = {"audio": ("recording_pi.wav", wav_buffer, "audio/wav")}
        data = {"client_datetime": client_datetime}
        headers = {"X-Client-Datetime": client_datetime}

        resp = requests.post(SERVER_URL, files=files, data=data, headers=headers, timeout=120)

        led_off()
        if resp.status_code == 200:
            response_data = resp.json()
            print("✅ Upload & processing successful!")

            upload_info = response_data.get("upload", {})
            print(f"   📁 Server filename: {upload_info.get('filename', 'unknown')}")
            print(f"   💾 Size (server): {upload_info.get('size_bytes', 0)} bytes")
            print(f"   ⏱️  Duration (local): {duration:.2f}s")

            intent = response_data.get("intent", "unknown")
            print(f"   🎯 Intent: {intent}")

            if response_data.get("transcript"):
                transcript = response_data["transcript"]
                preview = transcript[:80] + "..." if len(transcript) > 80 else transcript
                print(f"   📝 Transcript: {preview}")

            response_text = response_data.get("response_text") or response_data.get("summary")
            if response_text:
                preview = response_text[:80] + "..." if len(response_text) > 80 else response_text
                print(f"   💬 Response: {preview}")

            affected_days = response_data.get("affected_days", [])
            if affected_days:
                print(f"   📅 Affected days: {', '.join(affected_days)}")

            if response_data.get("tts_audio"):
                print("\n🔊 Playing response...")
                handle_tts_response(response_data)
            else:
                print("   ℹ️  No TTS audio in response")
                led_pulse(3)
            return True
        else:
            print(f"❌ Upload failed: HTTP {resp.status_code}")
            try:
                error_data = resp.json()
                print(f"   Error: {error_data.get('error', 'Unknown error')}")
            except Exception:
                print(resp.text[:200])
            return False
    except requests.exceptions.Timeout:
        led_off()
        print("❌ Upload timeout - server may be processing slowly")
        return False
    except Exception as e:
        led_off()
        print(f"❌ Upload error: {e}")
        return False


# ---------- Main loop ----------
def main() -> None:
    global INPUT_DEVICE

    print("=" * 60)
    print("🎤 SmartPager Audio + Display - Raspberry Pi 4B")
    print("   - Hold BCM11 to record & upload with TTS playback")
    print("   - BCM5: show current/next task (≤48 chars)")
    print("   - BCM4: toggle display")
    print("   - BCM6: scroll mode toggle (5 down, 4 top)")
    print("=" * 60)

    # Initialize audio playback (for TTS)
    print("\n🔊 Initializing audio playback...")
    if init_audio_playback():
        print("   ✅ Audio playback ready (I2S/ALSA)")
    else:
        print("   ⚠️  Audio playback not available - TTS will be disabled")

    list_audio_devices()
    if INPUT_DEVICE is None:
        INPUT_DEVICE = find_input_device()
    if INPUT_DEVICE is not None:
        dev_info = sd.query_devices(INPUT_DEVICE)
        print(f"\n🎤 Using input device [{INPUT_DEVICE}]: {dev_info['name']}")
        print(f"   Max channels: {dev_info['max_input_channels']}")
        print(f"   Default sample rate: {dev_info['default_samplerate']} Hz")
    else:
        print("\n🎤 Using system default input device")

    init_ism330dlc()
    turn_off_display()  # start with display off

    was_vertical = False
    recording_in_progress = False
    recording_index = 0

    try:
        while True:
            # ----- IMU: lift-to-wake detection -----
            ax, ay, az = read_accel_g()
            vertical = is_vertical_for_view(ax, ay, az)
            if vertical and not was_vertical:
                print("Wake")
                if not display_on:
                    turn_on_display()
            was_vertical = vertical

            # ----- Button polling (active-low) -----
            for pin in (TOGGLE_BUTTON_PIN, TASK_BUTTON_PIN, SCROLL_BUTTON_PIN):
                current_state = GPIO.input(pin)
                if button_last_state[pin] == GPIO.HIGH and current_state == GPIO.LOW:
                    handle_button_press(pin)
                button_last_state[pin] = current_state

            # Record button handled separately to support hold-to-record
            current_state = GPIO.input(BUTTON_GPIO)
            if not recording_in_progress and button_last_state[BUTTON_GPIO] == GPIO.HIGH and current_state == GPIO.LOW:
                recording_in_progress = True
                recording_index += 1
                print(f"\n--- Recording #{recording_index} ---")
                wav_buffer, duration = record_audio_to_ram()
                if wav_buffer is None or duration < 0.3:
                    print("Recording discarded (too short).")
                    led_pulse(count=2)
                else:
                    success = upload_audio_buffer(wav_buffer, duration)
                    if not success:
                        print("⚠️  Upload failed; recording discarded.")
                recording_in_progress = False
            button_last_state[BUTTON_GPIO] = current_state

            time.sleep(0.05)

    except KeyboardInterrupt:
        print("\nStopping SmartPager Pi...")
    finally:
        led_off()
        GPIO.cleanup()
        if _pygame_initialized:
            pygame.mixer.quit()


if __name__ == "__main__":
    main()
