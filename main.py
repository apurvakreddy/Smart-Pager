"""
SmartPager - Audio Capture Module
ESP32-S3 I2S Microphone Recording with Button Control

Hardware:
- Adafruit SPH0645 I2S MEMS Microphone
- Button on GPIO 11 (Record button)
- LED on GPIO 19 (Status indicator)
"""

from machine import Pin, I2S
import time
import struct
import network
import urequests as requests
import neopixel
import gc  # Garbage collection for memory management

# ==================== WIFI CONFIGURATION ====================

WIFI_SSID = "Columbia University"        # Change to your WiFi name
WIFI_PASSWORD = ""  # Change to your WiFi password
SERVER_URL = "http://10.207.24.55:5000/upload"  # Change to your server IP

# ==================== PIN CONFIGURATION ====================

# I2S Pins for SPH0645 Microphone
I2S_SCK_PIN = 4   # BCLK (Bit Clock)
I2S_WS_PIN = 5    # LRCLK/WS (Word Select / Left-Right Clock)
I2S_SD_PIN = 6    # DOUT (Data Out from Mic)

# Button and LEDs
BUTTON_PIN = 11   # Record button (active LOW with internal pullup)
LED_PIN = 19      # Status LED (external)
NEOPIXEL_PIN = 38 # Onboard NeoPixel (RGB LED)

# ==================== AUDIO CONFIGURATION ====================

SAMPLE_RATE = 16000      # 16 kHz sample rate (excellent for voice, streaming enabled!)
BITS_PER_SAMPLE = 16     # 16-bit samples
CHANNELS = 1             # Mono audio
BUFFER_SIZE = 2048       # I2S buffer size in bytes
MAX_RECORDING_SECS = 15  # Maximum recording duration (streaming supports any length!)
UPLOAD_TO_SERVER = True  # Set to False to save locally only
CHUNK_SIZE = 512         # Upload chunk size (small for streaming)

# ==================== NEOPIXEL SETUP ====================

# Initialize NeoPixel (onboard RGB LED)
np = neopixel.NeoPixel(Pin(NEOPIXEL_PIN), 1)

# Color definitions (R, G, B) - brightness reduced for comfort
COLOR_OFF = (0, 0, 0)
COLOR_RED = (40, 0, 0)           # Error / Failed
COLOR_GREEN = (0, 40, 0)         # Success / Connected
COLOR_BLUE = (0, 0, 40)          # Connecting
COLOR_YELLOW = (40, 40, 0)       # Recording
COLOR_CYAN = (0, 40, 40)         # Uploading
COLOR_PURPLE = (40, 0, 40)       # Processing
COLOR_WHITE = (20, 20, 20)       # Idle / Ready
COLOR_ORANGE = (40, 20, 0)       # Warning

def neopixel_set(color):
    """Set NeoPixel to a specific color"""
    np[0] = color
    np.write()

def neopixel_strobe(color, times=3, delay_ms=200):
    """Strobe the NeoPixel on and off"""
    for _ in range(times):
        neopixel_set(color)
        time.sleep_ms(delay_ms)
        neopixel_set(COLOR_OFF)
        time.sleep_ms(delay_ms)

def neopixel_pulse(color, duration_ms=1000, steps=20):
    """Pulse the NeoPixel (fade in and out)"""
    r, g, b = color
    for i in range(steps):
        # Fade in
        brightness = i / steps
        np[0] = (int(r * brightness), int(g * brightness), int(b * brightness))
        np.write()
        time.sleep_ms(duration_ms // (steps * 2))
    
    for i in range(steps, 0, -1):
        # Fade out
        brightness = i / steps
        np[0] = (int(r * brightness), int(g * brightness), int(b * brightness))
        np.write()
        time.sleep_ms(duration_ms // (steps * 2))
    
    neopixel_set(COLOR_OFF)

# Initialize NeoPixel to off
neopixel_set(COLOR_OFF)

# ==================== WIFI SETUP ====================

def connect_wifi():
    """Connect to WiFi network with NeoPixel status indicators"""
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    
    if wlan.isconnected():
        print(f"Already connected to WiFi")
        print(f"IP Address: {wlan.ifconfig()[0]}")
        neopixel_set(COLOR_GREEN)
        time.sleep_ms(1000)
        return True
    
    print(f"Connecting to WiFi: {WIFI_SSID}...")
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)
    
    # Wait for connection with blue strobe
    max_wait = 10
    while max_wait > 0:
        if wlan.isconnected():
            break
        
        # Strobe blue while connecting
        if max_wait % 2 == 0:
            neopixel_set(COLOR_BLUE)
        else:
            neopixel_set(COLOR_OFF)
        
        max_wait -= 1
        print(".", end="")
        time.sleep(1)
    
    if wlan.isconnected():
        print(f"\n✅ WiFi Connected!")
        print(f"IP Address: {wlan.ifconfig()[0]}")
        print(f"Server URL: {SERVER_URL}")
        
        # Success: Green pulse
        neopixel_pulse(COLOR_GREEN, duration_ms=800)
        neopixel_set(COLOR_GREEN)
        time.sleep_ms(1000)
        
        return True
    else:
        print(f"\n❌ WiFi Connection Failed")
        
        # Error: Red strobe
        neopixel_strobe(COLOR_RED, times=5, delay_ms=200)
        neopixel_set(COLOR_RED)
        
        return False

# ==================== SETUP ====================

# Initialize LED (output)
led = Pin(LED_PIN, Pin.OUT)
led.value(0)  # Start with LED off

# Initialize button (input with pullup)
button = Pin(BUTTON_PIN, Pin.IN, Pin.PULL_UP)

# Connect to WiFi if upload is enabled
wifi_connected = False
if UPLOAD_TO_SERVER:
    wifi_connected = connect_wifi()
    if not wifi_connected:
        print("⚠️  Will save files locally only (WiFi not connected)")
        UPLOAD_TO_SERVER = False

# Initialize I2S for audio input
audio_in = I2S(
    0,  # I2S peripheral ID
    sck=Pin(I2S_SCK_PIN),
    ws=Pin(I2S_WS_PIN),
    sd=Pin(I2S_SD_PIN),
    mode=I2S.RX,  # Receive mode
    bits=BITS_PER_SAMPLE,
    format=I2S.MONO,
    rate=SAMPLE_RATE,
    ibuf=BUFFER_SIZE * 2  # Internal buffer (reduced for memory efficiency)
)

print("\n" + "="*50)
print("🎤 SmartPager Audio Capture Initialized")
print("="*50)
print(f"Sample Rate: {SAMPLE_RATE} Hz")
print(f"Bits per Sample: {BITS_PER_SAMPLE}")
print(f"Max Recording: {MAX_RECORDING_SECS} seconds")
if UPLOAD_TO_SERVER and wifi_connected:
    print(f"Upload: ENABLED → {SERVER_URL}")
else:
    print("Upload: DISABLED (saving locally only)")
print("="*50)
print("\n📍 Press and hold GPIO 11 button to record...")
print("📍 Release button to stop and save\n")

# ==================== LED PATTERNS ====================

def led_blink_fast(duration_ms=500):
    """Fast blink pattern - indicates recording"""
    led.value(1)
    time.sleep_ms(duration_ms)
    led.value(0)

def led_on():
    """Solid LED - recording in progress"""
    led.value(1)

def led_off():
    """LED off - idle"""
    led.value(0)

def led_pulse(count=3):
    """Pulse pattern - indicates save/processing"""
    for _ in range(count):
        led.value(1)
        time.sleep_ms(100)
        led.value(0)
        time.sleep_ms(100)

# ==================== AUDIO RECORDING ====================

def create_wav_header(sample_rate, bits_per_sample, num_channels, data_size):
    """Create a WAV file header"""
    byte_rate = sample_rate * num_channels * bits_per_sample // 8
    block_align = num_channels * bits_per_sample // 8
    
    header = struct.pack('<4sI4s', b'RIFF', data_size + 36, b'WAVE')
    header += struct.pack('<4sIHHIIHH', b'fmt ', 16, 1, num_channels, 
                          sample_rate, byte_rate, block_align, bits_per_sample)
    header += struct.pack('<4sI', b'data', data_size)
    
    return header

def record_audio_to_file(filename):
    """Record audio directly to Flash (no RAM accumulation)"""
    print("\n[RECORDING STARTED - Streaming to Flash]")
    print(f"Free memory: {gc.mem_free()} bytes")
    
    led_on()
    neopixel_set(COLOR_YELLOW)  # Yellow = recording
    
    # Open file for writing immediately
    temp_filename = filename + ".tmp"
    total_bytes = 0
    start_time = time.ticks_ms()
    
    try:
        with open(temp_filename, 'wb') as f:
            # Write placeholder WAV header (we'll update it later)
            placeholder_header = create_wav_header(SAMPLE_RATE, BITS_PER_SAMPLE, CHANNELS, 0)
            f.write(placeholder_header)
            
            # Pre-allocate buffer ONCE (reuse it)
            mic_samples = bytearray(BUFFER_SIZE)
            
            # Record while button is held down (active LOW)
            while button.value() == 0:
                # Check max duration
                if time.ticks_diff(time.ticks_ms(), start_time) > MAX_RECORDING_SECS * 1000:
                    print("Max recording duration reached")
                    break
                
                # Read audio data from I2S
                try:
                    num_bytes_read = audio_in.readinto(mic_samples)
                    
                    if num_bytes_read > 0:
                        # Write directly to Flash (no RAM accumulation!)
                        f.write(mic_samples[:num_bytes_read])
                        total_bytes += num_bytes_read
                        
                        # Blink LED occasionally to show activity
                        if total_bytes % (BUFFER_SIZE * 10) < BUFFER_SIZE:
                            led.value(not led.value())
                
                except Exception as e:
                    print(f"Error reading audio: {e}")
                    print(f"Free memory at error: {gc.mem_free()} bytes")
                    break
        
        duration = time.ticks_diff(time.ticks_ms(), start_time) / 1000.0
        print(f"[RECORDING STOPPED] Duration: {duration:.2f}s, Size: {total_bytes} bytes")
        print(f"Free memory after recording: {gc.mem_free()} bytes")
        
        # Now update the WAV header IN-PLACE (don't reload entire file!)
        if total_bytes > 0:
            # Generate correct header
            correct_header = create_wav_header(SAMPLE_RATE, BITS_PER_SAMPLE, CHANNELS, total_bytes)
            
            # Update header in-place by reopening in r+b mode
            with open(temp_filename, 'r+b') as f:
                f.seek(0)  # Go to start
                f.write(correct_header)  # Overwrite header only
            
            # Rename temp file to final filename
            import os
            try:
                os.remove(filename)  # Delete if exists
            except:
                pass
            os.rename(temp_filename, filename)
            
            print(f"WAV file finalized: {filename}")
            print(f"Free memory after finalize: {gc.mem_free()} bytes")
        
        led_pulse(3)  # Signal recording complete
        neopixel_set(COLOR_PURPLE)  # Purple = processing
        
        return total_bytes, duration
    
    except Exception as e:
        print(f"Error during recording: {e}")
        led_pulse(5)  # Error indicator
        return 0, 0

def save_audio_to_wav(audio_data, filename="recording.wav"):
    """Save audio data as WAV file (LEGACY - not used with stream-to-flash)"""
    if len(audio_data) == 0:
        print("No audio data to save")
        return False
    
    try:
        print(f"Saving audio to {filename}...")
        
        # Create WAV header
        wav_header = create_wav_header(SAMPLE_RATE, BITS_PER_SAMPLE, CHANNELS, len(audio_data))
        
        # Write to file
        with open(filename, 'wb') as f:
            f.write(wav_header)
            f.write(audio_data)
        
        print(f"Audio saved successfully: {filename}")
        led_pulse(2)  # Success indicator
        return True
    
    except Exception as e:
        print(f"Error saving audio: {e}")
        return False

def upload_audio_to_server_streaming(filename):
    """Upload audio file using true streaming (no RAM limit!)"""
    if not UPLOAD_TO_SERVER:
        return False
    
    try:
        print(f"Uploading {filename} to server (streaming)...")
        led_on()
        neopixel_set(COLOR_CYAN)  # Cyan = uploading
        
        # Get file size
        import os
        file_size = os.stat(filename)[6]
        print(f"File size: {file_size} bytes")
        
        # Parse server URL
        # Extract host and port from SERVER_URL
        # Format: http://10.207.24.55:5000/upload
        url_parts = SERVER_URL.replace('http://', '').replace('https://', '')
        host_port, path = url_parts.split('/', 1)
        
        if ':' in host_port:
            host, port = host_port.split(':')
            port = int(port)
        else:
            host = host_port
            port = 80
        
        path = '/' + path
        
        print(f"Connecting to {host}:{port}")
        
        # Create socket connection
        import socket
        addr = socket.getaddrinfo(host, port)[0][-1]
        s = socket.socket()
        s.connect(addr)
        
        # Build HTTP POST request headers
        headers = (
            f'POST {path} HTTP/1.1\r\n'
            f'Host: {host}\r\n'
            f'Content-Type: audio/wav\r\n'
            f'Content-Length: {file_size}\r\n'
            f'Connection: close\r\n'
            f'\r\n'
        )
        
        # Send headers
        s.send(headers.encode())
        
        # Stream file data in chunks
        print("Streaming file...")
        bytes_sent = 0
        chunk_size = 512  # Small chunks to avoid RAM issues
        
        with open(filename, 'rb') as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                s.send(chunk)
                bytes_sent += len(chunk)
                
                # Show progress every 10KB
                if bytes_sent % 10240 < chunk_size:
                    print(f"  Sent: {bytes_sent}/{file_size} bytes")
                    led.value(not led.value())  # Blink LED
        
        print(f"✅ All {bytes_sent} bytes sent!")
        
        # Read response
        response = s.recv(1024).decode()
        s.close()
        
        led_off()
        
        # Check if successful (look for HTTP 200)
        if '200 OK' in response:
            print(f"✅ Upload successful!")
            # Try to extract filename from JSON response
            if '{' in response:
                import json
                json_start = response.index('{')
                json_str = response[json_start:]
                try:
                    result = json.loads(json_str)
                    print(f"   Server filename: {result.get('filename', 'unknown')}")
                except:
                    pass
            
            led_pulse(3)
            neopixel_strobe(COLOR_GREEN, times=3, delay_ms=150)
            return True
        else:
            print(f"❌ Upload failed: {response[:100]}")
            neopixel_strobe(COLOR_RED, times=3, delay_ms=150)
            return False
    
    except Exception as e:
        print(f"❌ Upload error: {e}")
        print(f"Free memory: {gc.mem_free()} bytes")
        led_off()
        neopixel_strobe(COLOR_RED, times=3, delay_ms=150)
        try:
            s.close()
        except:
            pass
        return False

def upload_audio_to_server(filename):
    """Upload audio file to server (ultra memory-optimized) - LEGACY, use streaming instead"""
    if not UPLOAD_TO_SERVER:
        return False
    
    try:
        print(f"Uploading {filename} to server...")
        led_on()  # LED on during upload
        neopixel_set(COLOR_CYAN)  # Cyan = uploading
        
        # Get file size first
        import os
        file_size = os.stat(filename)[6]
        print(f"File size: {file_size} bytes")
        
        # Aggressive memory cleanup before upload
        gc.collect()
        print(f"Free memory before upload: {gc.mem_free()} bytes")
        
        # Read file in chunks and build multipart body with preallocated size
        boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
        
        # Build header
        header = (
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="audio"; filename="{filename}"\r\n'
            f'Content-Type: audio/wav\r\n\r\n'
        ).encode()
        
        footer = f'\r\n--{boundary}--\r\n'.encode()
        
        # Calculate total body size and preallocate
        total_size = len(header) + file_size + len(footer)
        print(f"Total upload size: {total_size} bytes")
        
        # Pre-allocate bytearray with exact size (more efficient)
        try:
            body = bytearray(total_size)
        except MemoryError:
            print("Not enough memory for upload, trying alternative method...")
            # Fallback: build incrementally
            body = bytearray()
            body.extend(header)
            with open(filename, 'rb') as f:
                # Use even smaller chunks
                small_chunk = 512
                while True:
                    chunk = f.read(small_chunk)
                    if not chunk:
                        break
                    body.extend(chunk)
                    # Force GC every few chunks
                    if len(body) % 4096 == 0:
                        gc.collect()
            body.extend(footer)
        else:
            # Copy data into preallocated buffer
            pos = 0
            body[pos:pos+len(header)] = header
            pos += len(header)
            
            with open(filename, 'rb') as f:
                while pos < len(header) + file_size:
                    chunk = f.read(min(CHUNK_SIZE, len(header) + file_size - pos))
                    if not chunk:
                        break
                    body[pos:pos+len(chunk)] = chunk
                    pos += len(chunk)
            
            body[pos:pos+len(footer)] = footer
        
        # Set headers
        headers = {
            'Content-Type': f'multipart/form-data; boundary={boundary}'
        }
        
        print(f"Sending {len(body)} bytes...")
        
        # Send POST request
        response = requests.post(SERVER_URL, data=bytes(body), headers=headers)
        
        # Free memory immediately after upload
        del body
        gc.collect()
        
        led_off()
        
        if response.status_code == 200:
            result = response.json()
            print(f"✅ Upload successful!")
            print(f"   Server filename: {result.get('filename', 'unknown')}")
            print(f"   Size: {result.get('size_bytes', 0)} bytes")
            led_pulse(3)  # Success pattern
            neopixel_strobe(COLOR_GREEN, times=3, delay_ms=150)  # Green strobe = success
            response.close()
            return True
        else:
            print(f"❌ Upload failed: HTTP {response.status_code}")
            neopixel_strobe(COLOR_RED, times=3, delay_ms=150)  # Red strobe = failed
            response.close()
            return False
    
    except Exception as e:
        print(f"❌ Upload error: {e}")
        print(f"Free memory at error: {gc.mem_free()} bytes")
        led_off()
        neopixel_strobe(COLOR_RED, times=3, delay_ms=150)  # Red strobe = error
        gc.collect()
        return False

# ==================== MAIN LOOP ====================

def main():
    """Main loop - wait for button press to record"""
    recording_count = 0
    
    # Set idle state colors
    led_off()
    neopixel_set(COLOR_WHITE)  # White = ready/idle
    
    # Initial garbage collection
    gc.collect()
    print(f"Initial free memory: {gc.mem_free()} bytes")
    
    while True:
        # Wait for button press (active LOW)
        if button.value() == 0:
            # Debounce
            time.sleep_ms(50)
            if button.value() == 0:
                # Free memory before recording
                gc.collect()
                
                recording_count += 1
                filename = f"recording_{recording_count}.wav"
                
                # Record audio directly to Flash (no RAM accumulation!)
                total_bytes, duration = record_audio_to_file(filename)
                
                # Check if we got data
                if total_bytes > 0:
                    print(f"✅ Recording saved: {filename}")
                    gc.collect()
                    print(f"Memory after recording: {gc.mem_free()} bytes")
                    
                    # Upload to server if enabled (using streaming!)
                    if UPLOAD_TO_SERVER:
                        upload_success = upload_audio_to_server_streaming(filename)
                        if upload_success:
                            print("📤 File uploaded (streamed) and saved locally")
                        else:
                            print("⚠️  Saved locally only (upload failed)")
                    else:
                        print("💾 Saved locally only")
                    
                    print(f"\nReady for next recording... (Total: {recording_count})")
                else:
                    print("Recording too short, discarded")
                    neopixel_strobe(COLOR_ORANGE, times=2, delay_ms=150)  # Orange = warning
                    recording_count -= 1  # Don't count failed recordings
                
                # Return to idle state
                led_off()
                neopixel_set(COLOR_WHITE)  # White = ready/idle
                
                # Final cleanup
                gc.collect()
                print(f"Ready - Free memory: {gc.mem_free()} bytes\n")
        
        time.sleep_ms(50)  # Small delay to prevent CPU hogging

# ==================== RUN ====================

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nProgram stopped by user")
        led_off()
        neopixel_set(COLOR_OFF)
        audio_in.deinit()
    except Exception as e:
        print(f"\n\nError: {e}")
        led_off()
        # Blink LEDs rapidly to indicate error
        for _ in range(10):
            led.value(not led.value())
            neopixel_set(COLOR_RED if _ % 2 == 0 else COLOR_OFF)
            time.sleep_ms(200)
        neopixel_set(COLOR_OFF)

