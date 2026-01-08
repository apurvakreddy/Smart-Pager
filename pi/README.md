# SmartPager - Raspberry Pi Audio Capture

Record voice commands and receive TTS schedule summaries on Raspberry Pi 4B.

## 🎯 Features

-   **Voice Recording:** Hold button to record schedule commands via I2S mic
-   **Server Upload:** Automatic upload to SmartPager server for processing
-   **TTS Playback:** Receive and play schedule summaries through I2S speaker
-   **Visual Feedback:** LED indicator for recording/playback status

## 🔧 Hardware Requirements

| Component        | Description                         |
| ---------------- | ----------------------------------- |
| Raspberry Pi 4B  | Main board                          |
| SPH0645          | I2S MEMS microphone (audio capture) |
| MAX98357A        | I2S mono amplifier (TTS playback)   |
| 8Ω 2W Speaker    | Connected to MAX98357A output       |
| Momentary Button | Connected to GPIO 11 (BCM)          |
| LED              | Connected to GPIO 26 (BCM)          |

---

## 📌 Pinout Reference

### Quick Wiring Table

| Raspberry Pi     | SPH0645 (Mic)    | MAX98357A (Amp) |
| ---------------- | ---------------- | --------------- |
| Pin 1 (3.3V)     | VDD (3.3V only!) | -               |
| Pin 2 (5V)       | -                | VIN             |
| Pin 6 (GND)      | GND              | GND             |
| Pin 12 (GPIO 18) | BCLK             | BCLK            |
| Pin 35 (GPIO 19) | LRCL             | LRC             |
| Pin 38 (GPIO 20) | DOUT             | -               |
| Pin 40 (GPIO 21) | -                | DIN             |

### SPH0645 Microphone Pinout

```
    SPH0645 Module
   ┌──────────────┐
   │  ┌────────┐  │
   │  │  MEMS  │  │      Pin Connections:
   │  │  Mic   │  │      ─────────────────
   │  └────────┘  │      SEL  → GND (Left channel)
   │              │      LRCL → GPIO 19 (Pin 35)
   │ SEL LRCL DOUT│      DOUT → GPIO 20 (Pin 38)
   │  │   │    │  │      BCLK → GPIO 18 (Pin 12)
   └──┼───┼────┼──┘      GND  → GND (Pin 6 or 9)
      │   │    │         3V   → 3.3V (Pin 1)
      ▼   ▼    ▼
     GND GPIO GPIO       ⚠️ Use 3.3V ONLY!
          19   20

   │ GND  BCLK  3V │
   └──┼────┼────┼──┘
      │    │    │
      ▼    ▼    ▼
     GND GPIO  3.3V
          18
```

| Pin  | Name      | Description                                |
| ---- | --------- | ------------------------------------------ |
| SEL  | Select    | Channel select: GND=Left, 3.3V=Right       |
| LRCL | LR Clock  | Word select / Frame sync (shared with amp) |
| DOUT | Data Out  | I2S audio data output to Pi                |
| BCLK | Bit Clock | I2S bit clock (shared with amp)            |
| GND  | Ground    | Connect to Pi GND                          |
| 3V   | Power     | **3.3V ONLY** - Do NOT use 5V!             |

### MAX98357A Amplifier Pinout

```
    MAX98357A Module
   ┌────────────────┐
   │                │       Pin Connections:
   │   ┌────────┐   │       ─────────────────
   │   │  Amp   │   │       VIN  → 5V (Pin 2)
   │   │  Chip  │   │       GND  → GND (Pin 6)
   │   └────────┘   │       SD   → VIN (enable) or NC
   │                │       GAIN → NC (9dB default)
   │ VIN GND SD GAIN│       DIN  → GPIO 21 (Pin 40)
   └──┬───┬──┬───┬──┘       BCLK → GPIO 18 (Pin 12)
      │   │  │   │          LRC  → GPIO 19 (Pin 35)
      ▼   ▼  ▼   ▼
      5V GND NC  NC

   │ DIN BCLK LRC │
   └──┬───┬────┬──┘
      │   │    │
      ▼   ▼    ▼
    GPIO GPIO GPIO
     21   18   19

   │ OUT+ OUT- │  ──► To 8Ω Speaker
   └──┬────┬───┘
      +    -
```

| Pin  | Name      | Description                                |
| ---- | --------- | ------------------------------------------ |
| VIN  | Power     | 2.5-5.5V input. Use 5V for best headroom.  |
| GND  | Ground    | Connect to Pi GND                          |
| SD   | Shutdown  | Tie to VIN to enable, or leave floating    |
| GAIN | Gain      | NC=9dB, GND=9dB, VIN=15dB                  |
| DIN  | Data In   | I2S audio data input from Pi               |
| BCLK | Bit Clock | I2S bit clock (shared with mic)            |
| LRC  | LR Clock  | Word select / Frame sync (shared with mic) |
| OUT+ | Speaker + | Positive speaker terminal                  |
| OUT- | Speaker - | Negative speaker terminal                  |

### GPIO Summary

| Function | BCM GPIO | Physical Pin | Notes              |
| -------- | -------- | ------------ | ------------------ |
| I2S BCLK | GPIO 18  | Pin 12       | Shared (mic + amp) |
| I2S LRC  | GPIO 19  | Pin 35       | Shared (mic + amp) |
| I2S DIN  | GPIO 20  | Pin 38       | Mic → Pi           |
| I2S DOUT | GPIO 21  | Pin 40       | Pi → Amp           |
| Button   | GPIO 11  | Pin 23       | Active LOW         |
| LED      | GPIO 26  | Pin 37       | Status indicator   |

### Raspberry Pi GPIO Header

```
              Raspberry Pi 4B - 40-Pin Header
              ════════════════════════════════
                (SmartPager pins marked *)

         3V3* [1]  [2]  5V*     SPH0645 3V | MAX98357A VIN | OLED VCC
       GPIO2* [3]  [4]  5V      OLED SDA | ISM330 SDA
       GPIO3* [5]  [6]  GND*    SPH0645 GND | MAX98357A GND | OLED GND | Buttons GND
      *GPIO4  [7]  [8]  GPIO14  BUTTON 1 (Toggle)
         GND  [9]  [10] GPIO15
      GPIO17 [11] [12] GPIO18*  BCLK (shared)
      GPIO27 [13] [14] GND
      GPIO22 [15] [16] GPIO23
         3V3 [17] [18] GPIO24
      GPIO10 [19] [20] GND
       GPIO9 [21] [22] GPIO25
     *GPIO11 [23] [24] GPIO8    RECORD BUTTON
         GND [25] [26] GPIO7
       GPIO0 [27] [28] GPIO1
      *GPIO5 [29] [30] GND      BUTTON 2 (Task)
      *GPIO6 [31] [32] GPIO12   BUTTON 3 (Scroll)
      GPIO13 [33] [34] GND
     *GPIO19 [35] [36] GPIO16   LRC (shared)
     *GPIO26 [37] [38] GPIO20*  LED | SPH0645 DOUT
         GND [39] [40] GPIO21*  MAX98357A DIN
```

### Additional Hardware (Display & Sensors)

| Component | Pin | Description |
|-----------|-----|-------------|
| **OLED SSD1306** | | 128x32 I2C Display |
| VCC | 3.3V (Pin 1) | Power |
| GND | GND | Ground |
| SDA | GPIO 2 (Pin 3) | I2C Data |
| SCL | GPIO 3 (Pin 5) | I2C Clock |
| **ISM330DLC** | | Accelerometer (Lift-to-wake) |
| VCC | 3.3V | Power |
| GND | GND | Ground |
| SDA | GPIO 2 | Shared I2C |
| SCL | GPIO 3 | Shared I2C |
| **Control Buttons** | | Active LOW (Connect to GND) |
| Toggle Display | GPIO 4 (Pin 7) | Turn display on/off |
| Show Task | GPIO 5 (Pin 29) | Show current/next task |
| Scroll Mode | GPIO 6 (Pin 31) | Enter/exit scroll mode |


---

## 📦 I2S Audio Setup

### Step 1: Enable I2S Overlay

```bash
sudo nano /boot/firmware/config.txt
```

Add at the end:

```ini
# Enable I2S full-duplex audio
dtparam=i2s=on
dtoverlay=googlevoicehat-soundcard

# Enable I2C for Display & Accelerometer
dtparam=i2c_arm=on
```

### Step 2: Configure ALSA

```bash
sudo nano /etc/asound.conf
```

Add:

```
pcm.!default {
    type asym
    playback.pcm "speaker"
    capture.pcm "mic"
}

pcm.speaker {
    type plug
    slave {
        pcm "hw:0,0"
        rate 48000
    }
}

pcm.mic {
    type plug
    slave {
        pcm "hw:0,0"
        rate 48000
    }
}

ctl.!default {
    type hw
    card 0
}
```

### Step 3: Reboot & Test

```bash
sudo reboot

# After reboot, test:
speaker-test -c 1 -t sine -f 440     # Test speaker
arecord -d 5 -f S16_LE -r 48000 test.wav  # Test mic
aplay test.wav                        # Play recording
```

---

## 🚀 Quick Start

### 1. Install Dependencies

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-pip python3-venv portaudio19-dev python3-pygame python3-rpi.gpio
```

### 2. Setup Python Environment

```bash
cd ~/smartPager/pi
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure Server

Edit `audio_capture_rpi.py`:

```python
SERVER_URL = "http://YOUR_SERVER_IP:5000/upload"
```

### 4. Run

```bash
python3 audio_capture_rpi.py
```

---

## 🧪 Testing & Verification

We have included a test script to verify your hardware components.

```bash
python3 test_hardware.py
```

This interactive script allows you to:
1. **Test Display**: Cycles through text messages.
2. **Test Buttons**: Detects presses on Toggle, Task, and Scroll buttons.
3. **Test Accelerometer**: Shows real-time X/Y/Z values and "Lifted" status.

### Quick Manual Checks

- **Display**: Should show "SmartPager Ready" on boot.
- **Buttons**:
    - **Toggle (GPIO 4)**: Turns display on/off (or wakes it).
    - **Task (GPIO 5)**: Shows "No upcoming tasks" (if no schedule).
    - **Scroll (GPIO 6)**: Enters scroll mode (shows list of events).
- **Lift-to-Wake**:
    - Lay device flat on table -> Display may sleep (if logic implemented) or just stay ready.
    - Lift device up to face you -> Display should wake/refresh.

---

## 💡 Usage

1. **Start the Client**:
   ```bash
   python3 audio_capture_rpi.py
   ```
2. **Record Command**:
   - Hold **Record Button (GPIO 11)**.
   - Speak: "Add meeting with John at 2pm".
   - Release button.
3. **Feedback**:
   - Display shows: "Listening..." -> "Uploading..." -> "Speaking...".
   - TTS plays audio summary.
   - Display updates with the new event (e.g., "Next: Meeting 14:00").

## 🧠 Client Code Flow
 
 The `audio_capture_rpi.py` script operates in a continuous loop:
 
 1.  **Idle State**:
     *   Waits for Button Press (GPIO 11).
     *   LED is OFF.
 
 2.  **Recording State** (Button Held):
     *   **Capture**: Reads raw PCM data from I2S Microphone (SPH0645).
     *   **Storage**: Buffers audio in RAM (BytesIO) to avoid SD card wear.
     *   **Feedback**: LED stays ON. Display shows "Listening...".
 
 3.  **Processing State** (Button Released):
     *   **Upload**: Sends WAV data via HTTP POST to Server (`/upload`).
     *   **Feedback**: LED blinks fast. Display shows "Uploading...".
 
 4.  **Playback State**:
     *   **Receive**: Decodes Base64 audio from Server response.
     *   **Play**: Uses `pygame` to play audio via I2S Amp (MAX98357A).
     *   **Feedback**: LED stays ON during playback. Display shows "Speaking...".
 
 5.  **Update State**:
     *   Parses JSON response for updated schedule.
     *   Updates OLED display with next event.
 
 ---

## 🐛 Troubleshooting

### Accelerometer WHO_AM_I Mismatch
If you see `Warning: WHO_AM_I = 0x6B`, this is normal for some ISM330/LSM6DS3 variants. The code supports both `0x6A` and `0x6B`.

### Display Not Working
- Check `i2cdetect -y 1`. You should see `3c` (OLED) and `6a` or `6b` (Accel).
- Ensure `dtparam=i2c_arm=on` is in `/boot/firmware/config.txt`.

### No Audio
- Check `aplay -l` and `arecord -l`.
- Ensure I2S overlay is loaded.

