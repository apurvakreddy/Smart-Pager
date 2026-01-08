# SmartPager Server

Flask server for receiving, processing, and scheduling voice commands from Raspberry Pi.

## 🎯 Features

-   **Audio Capture:** Receive WAV files from Raspberry Pi
-   **Speech-to-Text:** Transcribe audio using Whisper
-   **Intent Classification:** LLM-based command routing (add/query/delete/clear)
-   **Weekly Schedule:** Persistent Monday-Sunday schedule storage
-   **Schedule Optimization:** Optimize with OR-Tools (no overlapping events)
-   **Natural Language Responses:** Context-aware summaries for TTS
-   **TTS Output:** Audio response via Piper TTS

## 🚀 Quick Start

### 1. Set up virtual environment

```bash
# Create venv
python3 -m venv venv

# Activate venv
source venv/bin/activate  # macOS/Linux
# or: venv\Scripts\activate  # Windows
```

### 2. Install System Dependencies

You must install `ffmpeg` for audio processing:

```bash
sudo apt-get install ffmpeg
```

### 3. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure API Keys

```bash
# Copy example env file
cp env-example.txt .env

# Edit .env with your OpenAI API key
OPENAI_API_KEY=sk-your-api-key-here
```

### 5. Run server

```bash
python audioCapture_server.py
```

Server will run at: **http://localhost:5000**

### 6. Configure Raspberry Pi

Update the Raspberry Pi code with your server's IP address:

```python
# In pi/audio_capture_rpi.py
SERVER_URL = "http://YOUR_COMPUTER_IP:5000/upload"
```

Find your IP:

-   **macOS/Linux:** `ifconfig | grep inet`
-   **Windows:** `ipconfig`

### 7. Test the System

Verify everything works:

```bash
# Add a test event
curl -X POST http://localhost:5000/api/process_transcript \
  -H "Content-Type: application/json" \
  -d '{"transcript": "Add meeting Monday at 2pm"}'

# Check the week schedule
curl http://localhost:5000/api/schedule/week
```

## 📁 File Structure

```
server/
├── audioCapture_server.py        # Main Flask server entry point
├── requirements.txt              # Python dependencies
├── env-example.txt               # Example environment file
├── README.md                     # This file
├── modules/                      # Core Logic Modules
│   ├── __init__.py
│   ├── audio_pipeline.py         # Main orchestrator (Pipeline Pattern)
│   ├── whisper_handler.py        # STT: Audio -> Text (OpenAI Whisper)
│   ├── intent_router.py          # NLP: Text -> Intent (GPT-4)
│   ├── llm_interpreter.py        # NLP: Intent -> Structured Data
│   ├── scheduler.py              # Logic: Conflict Resolution & Optimization
│   ├── schedule_manager.py       # Data: JSON Persistence Layer
│   ├── calendar_utils.py         # Data: Google Calendar Sync
│   ├── summary_generator.py      # NLG: Structured Data -> Natural Text
│   └── tts_handler.py            # TTS: Text -> Audio (Piper)
├── schedule/                     # Local Database (JSON)
│   ├── monday/schedule.json
│   └── ...
├── recordings/                   # Incoming Audio Storage
└── output/                       # Processing Artifacts
```

## 🏗️ Code Architecture

The server follows a **Pipeline Architecture** orchestrated by `modules/audio_pipeline.py`.

### Request Flow

1.  **Entry Point (`audioCapture_server.py`)**:
    *   Receives POST request at `/upload`.
    *   Saves raw WAV file to `recordings/`.
    *   Calls `process_audio_file()` in `audio_pipeline.py`.

2.  **Pipeline Orchestrator (`modules/audio_pipeline.py`)**:
    *   **Step 1: Transcribe** -> Calls `whisper_handler.py` to convert Audio to Text.
    *   **Step 2: Classify** -> Calls `intent_router.py` to determine if user wants to ADD, QUERY, DELETE, or CLEAR.
    *   **Step 3: Route** -> Dispatches to specific handlers based on intent.

3.  **Intent Handlers**:
    *   **Modify Schedule**:
        *   Uses `llm_interpreter.py` to extract event details (time, name).
        *   Uses `scheduler.py` to check conflicts and optimize time slots.
        *   Uses `schedule_manager.py` to save to local JSON.
        *   Uses `calendar_utils.py` to sync with Google Calendar.
    *   **Query Schedule**:
        *   Reads from `schedule_manager.py`.
        *   Generates summary via `summary_generator.py`.

4.  **Response Generation**:
    *   **Step 4: TTS** -> Calls `tts_handler.py` to generate audio response.
    *   Returns JSON + Base64 Audio to client.

## 🔧 Endpoints

### Audio Upload & Processing

| Endpoint              | Method | Description                               |
| --------------------- | ------ | ----------------------------------------- |
| `/upload`             | POST   | Upload audio + auto-process (recommended) |
| `/upload_and_process` | POST   | Upload + process (legacy)                 |

### Processing

| Endpoint                  | Method | Description                        |
| ------------------------- | ------ | ---------------------------------- |
| `/api/process/<filename>` | POST   | Process specific recording         |
| `/api/process_latest`     | POST   | Process most recent recording      |
| `/api/process_transcript` | POST   | Process text directly (skip audio) |
| `/api/results/<filename>` | GET    | Get processing results             |

### Weekly Schedule (NEW)

| Endpoint                           | Method | Description                         |
| ---------------------------------- | ------ | ----------------------------------- |
| `/api/schedule/week`               | GET    | Get entire week schedule            |
| `/api/schedule/week`               | DELETE | Clear entire week (start fresh)     |
| `/api/schedule/<day>`              | GET    | Get specific day (monday, today)    |
| `/api/schedule/<day>`              | DELETE | Clear specific day                  |
| `/api/schedule/<day>/event`        | POST   | Add event to day                    |
| `/api/schedule/<day>/event/<name>` | DELETE | Delete event by name                |
| `/api/schedule/summary`            | GET    | Get week summary (natural language) |

### Raspberry Pi / ESP32 Endpoints

| Endpoint            | Method | Description             |
| ------------------- | ------ | ----------------------- |
| `/api/agenda/today` | GET    | Get today's schedule    |
| `/api/agenda/next`  | GET    | Get next upcoming event |

### Recordings

| Endpoint               | Method | Description                     |
| ---------------------- | ------ | ------------------------------- |
| `/`                    | GET    | Web interface (view recordings) |
| `/api/recordings`      | GET    | List all recordings (JSON)      |
| `/audio/<filename>`    | GET    | Stream audio file               |
| `/download/<filename>` | GET    | Download audio file             |

## 🎤 Supported Voice Commands

The system uses LLM-based intent classification to understand natural language commands:

| Intent            | Example Commands                                                                                              |
| ----------------- | ------------------------------------------------------------------------------------------------------------- |
| **Add Events**    | "Add meeting Monday at 2pm", "Schedule lunch tomorrow at noon", "I have a dentist appointment Tuesday at 3pm" |
| **Query Day**     | "What's on Monday?", "What do I have today?", "Tell me about tomorrow's schedule"                             |
| **Query Week**    | "What does my week look like?", "Give me a summary", "What's coming up?"                                      |
| **Delete Events** | "Cancel dentist appointment", "Remove gym on Tuesday", "Delete the meeting"                                   |
| **Clear Day**     | "Clear Monday", "Delete everything on Tuesday"                                                                |
| **Clear Week**    | "Start fresh", "Clear my week", "Reset my schedule"                                                           |
| **Clear Week**    | "Start fresh", "Clear my week", "Reset my schedule"                                                           |
| **Help**          | "What can you do?", "Help"                                                                                    |

### Interactive Features (NEW)

The system now supports interactive clarification and conflict resolution:

1.  **Clarification:** If you say "Add a meeting" without a time, it will ask: *"When would you like to schedule that?"*
2.  **Conflict Recommendation:** If you try to book a double-booking, it won't auto-resolve. Instead, it will say: *"I couldn't add 'Meeting' because it conflicts with 'Doctor' at 2pm. I recommend moving it to 3pm."*

## 📝 Example Usage & Test Commands

### 1. Add Events

```bash
# Add a meeting on Monday
curl -X POST http://localhost:5000/api/process_transcript \
  -H "Content-Type: application/json" \
  -d '{"transcript": "Add a meeting on Monday at 2pm"}'

# Add multiple events
curl -X POST http://localhost:5000/api/process_transcript \
  -H "Content-Type: application/json" \
  -d '{"transcript": "Schedule lunch with Bob tomorrow at noon and a dentist appointment on Wednesday at 3pm"}'

# Add event with specific client datetime (for proper "today"/"tomorrow" resolution)
curl -X POST http://localhost:5000/api/process_transcript \
  -H "Content-Type: application/json" \
  -d '{"transcript": "Add gym session today at 6pm", "client_datetime": "2024-12-07T14:00:00"}'
```

### 2. Query Schedule

```bash
# Query a specific day
curl -X POST http://localhost:5000/api/process_transcript \
  -H "Content-Type: application/json" \
  -d '{"transcript": "What do I have on Monday?"}'

# Query week overview
curl -X POST http://localhost:5000/api/process_transcript \
  -H "Content-Type: application/json" \
  -d '{"transcript": "What does my week look like?"}'

# Ask for help
curl -X POST http://localhost:5000/api/process_transcript \
  -H "Content-Type: application/json" \
  -d '{"transcript": "What can you do?"}'
```

### 3. Delete Events

```bash
# Delete by name
curl -X POST http://localhost:5000/api/process_transcript \
  -H "Content-Type: application/json" \
  -d '{"transcript": "Cancel the dentist appointment"}'

# Delete from specific day
curl -X POST http://localhost:5000/api/process_transcript \
  -H "Content-Type: application/json" \
  -d '{"transcript": "Remove gym on Tuesday"}'
```

### 4. Clear Schedule

```bash
# Clear a specific day
curl -X POST http://localhost:5000/api/process_transcript \
  -H "Content-Type: application/json" \
  -d '{"transcript": "Clear Monday"}'

# Clear entire week
curl -X POST http://localhost:5000/api/process_transcript \
  -H "Content-Type: application/json" \
  -d '{"transcript": "Start fresh"}'
```

### 5. Direct API Access (No Voice)

```bash
# Get entire week schedule
curl http://localhost:5000/api/schedule/week

# Get specific day schedule
curl http://localhost:5000/api/schedule/monday
curl http://localhost:5000/api/schedule/today

# Get week summary text
curl http://localhost:5000/api/schedule/summary

# Clear a day via API
curl -X DELETE http://localhost:5000/api/schedule/tuesday

# Clear entire week via API
curl -X DELETE http://localhost:5000/api/schedule/week

# Add event directly via API (bypasses voice)
curl -X POST http://localhost:5000/api/schedule/monday/event \
  -H "Content-Type: application/json" \
  -d '{"name": "Team standup", "start": "09:00", "end": "09:30"}'

# Delete event via API
curl -X DELETE http://localhost:5000/api/schedule/monday/event/standup
```

### 6. Legacy Endpoints

```bash
# Get today's agenda (Raspberry Pi format)
curl http://localhost:5000/api/agenda/today

# Get next event
curl http://localhost:5000/api/agenda/next

# Process the latest recording
curl -X POST http://localhost:5000/api/process_latest
```

## 🎤 Processing Pipeline

```
┌─────────────────────────────────────────────────────────────────┐
│                        Voice Command                             │
│  "What's my schedule for Tuesday?" / "Add meeting Monday 2pm"   │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Whisper STT                                 │
│              Converts speech to text                             │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Intent Router (GPT-4)                          │
│  Classifies: MODIFY | QUERY_DAY | QUERY_WEEK | CLEAR | HELP     │
└─────────────────────────────────────────────────────────────────┘
                                │
                    ┌───────────┴───────────┐
                    ▼                       ▼
         ┌──────────────────┐    ┌──────────────────┐
         │  Schedule Tools  │    │   Query Tools    │
         │  Add/Edit/Delete │    │  Day/Week Query  │
         │  + OR-Tools Opt. │    │  + Summary Gen   │
         └──────────────────┘    └──────────────────┘
                    │                       │
                    └───────────┬───────────┘
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Response Generator                             │
│  Natural language response for TTS playback                     │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Piper TTS → Raspberry Pi                    │
└─────────────────────────────────────────────────────────────────┘
```

### Pipeline Steps:

1. **Whisper:** Converts speech to text transcript
2. **Intent Router:** GPT-4 classifies intent (add/query/delete/clear/help)
3. **Schedule Manager:** Loads/saves persistent weekly schedules
4. **OR-Tools:** Optimizes schedule to avoid conflicts (for add/edit)
5. **Summary Generator:** Creates natural language response
6. **Piper TTS:** Converts response to audio for playback

### Week Reset Behavior:

-   **Automatic:** Server detects new calendar week (Monday) and resets
-   **Manual:** User says "Start fresh" or "Clear my week"

### Interactive Conflict Resolution (NEW)

Unlike the previous auto-rescheduling behavior, the system now **pauses** and asks for your input when a conflict occurs:

```
┌─────────────────────────────────────────────────────────────────┐
│  EXISTING: Monday Schedule                                       │
│  └── 2pm-3pm: Meeting (Fixed)                                    │
└─────────────────────────────────────────────────────────────────┘
                              │
            User: "Add dentist at 2pm on Monday"
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  CONFLICT DETECTION                                              │
│  ⚠️ "Dentist" overlaps with "Meeting"                           │
│  → System identifies conflict                                    │
│  → System finds next available slot (e.g., 3pm)                  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  RESPONSE (No changes made yet)                                  │
│  "I couldn't add 'Dentist' because it conflicts with your       │
│   fixed event 'Meeting' at 2:00 PM.                             │
│   I recommend moving it to 3:00 PM."                             │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
            User: "Okay, move it to 3pm"
```

**Conflict Resolution Rules:**

| Scenario                          | Behavior                                        |
| --------------------------------- | ----------------------------------------------- |
| New event conflicts with existing | System **recommends** a new time, does NOT save |
| Ambiguous time ("later")          | System asks **clarification** question          |
| Missing day/time                  | System asks **clarification** question          |

**Test conflict detection:**

```bash
# 1. Add first event
curl -X POST http://localhost:5000/api/process_transcript \
  -H "Content-Type: application/json" \
  -d '{"transcript": "Add meeting Monday at 2pm"}'

# 2. Add conflicting event (same time!)
curl -X POST http://localhost:5000/api/process_transcript \
  -H "Content-Type: application/json" \
  -d '{"transcript": "Add dentist Monday at 2pm"}'

# Response: "Rescheduled dentist to 3:00 PM on Monday due to a conflict."

# 3. Verify
curl http://localhost:5000/api/schedule/monday
```

## 🐛 Troubleshooting

### "OPENAI_API_KEY not set"

```bash
# Create .env file with your API key
echo "OPENAI_API_KEY=sk-your-key-here" > .env
```

### Whisper model download slow

First run downloads the Whisper model (~150MB). This is normal.

### Port 5000 already in use

```bash
lsof -i :5000  # Find what's using it
# Kill the process or change PORT in code
```

### OR-Tools scheduler errors

Usually means the day is too packed to fit all events. The system now auto-reschedules conflicts, but if there's truly no room, you'll see "No feasible schedule found." Try:

-   Deleting some events to make room
-   Moving events to different days
-   Using shorter durations

## 💡 Tips

-   **Virtual environment:** Always activate before running!
-   **View logs:** Server prints detailed processing steps
-   **Test without ESP32:** Use `/api/process_transcript` endpoint
-   **First run is slow:** Whisper model needs to download

## 📊 Sample Output

### Add Event Response

```json
{
	"success": true,
	"transcript": "Add a meeting on Monday at 2pm",
	"intent": "modify_schedule",
	"intent_confidence": 0.95,
	"response_text": "Added meeting on Monday at 2pm.",
	"changes_made": {
		"added": [["monday", "meeting"]],
		"deleted": [],
		"modified": []
	},
	"affected_days": ["monday"],
	"summary_audio_available": true,
	"processing_time_ms": 2500
}
```

### Query Day Response

```json
{
	"success": true,
	"transcript": "What do I have on Monday?",
	"intent": "query_day",
	"intent_confidence": 0.92,
	"response_text": "On Monday, you have a meeting at 2pm and a dentist appointment at 4pm.",
	"schedule": {
		"day": "monday",
		"events": [
			{
				"name": "meeting",
				"type": "fixed",
				"start": "2024-12-09T14:00:00",
				"end": "2024-12-09T15:00:00",
                "_calendar_id": "abc123xyz",
                "_calendar_htmlLink": "https://www.google.com/calendar/event?eid=..."
			},
			{
				"name": "dentist appointment",
				"type": "fixed",
				"start": "2024-12-09T16:00:00",
				"end": "2024-12-09T17:00:00",
                "_calendar_id": "def456uvw",
                "_calendar_htmlLink": "https://www.google.com/calendar/event?eid=..."
			}
		]
	},
	"summary_audio_available": true,
	"processing_time_ms": 1800
}
```

### Week Schedule Response (via `/api/schedule/week`)

```json
{
  "success": true,
  "week_start": "2024-12-02",
  "last_modified": "2024-12-07T14:30:00",
  "total_events": 5,
  "days": {
    "monday": {
      "event_count": 2,
      "events": [...],
      "last_updated": "2024-12-07T14:30:00"
    },
    "tuesday": {
      "event_count": 1,
      "events": [...],
      "last_updated": "2024-12-07T12:00:00"
    },
    "wednesday": { "event_count": 0, "events": [] },
    "thursday": { "event_count": 2, "events": [...] },
    "friday": { "event_count": 0, "events": [] },
    "saturday": { "event_count": 0, "events": [] },
    "sunday": { "event_count": 0, "events": [] }
  }
}
```

## 🧪 Quick Test Sequence

Run these commands in order to verify the system works:

```bash
# 1. Clear any existing schedule
curl -X DELETE http://localhost:5000/api/schedule/week

# 2. Add some events
curl -X POST http://localhost:5000/api/process_transcript \
  -H "Content-Type: application/json" \
  -d '{"transcript": "Add team meeting Monday at 10am"}'

curl -X POST http://localhost:5000/api/process_transcript \
  -H "Content-Type: application/json" \
  -d '{"transcript": "Schedule lunch with Sarah on Tuesday at noon"}'

curl -X POST http://localhost:5000/api/process_transcript \
  -H "Content-Type: application/json" \
  -d '{"transcript": "Add dentist appointment Wednesday at 3pm"}'

# 3. Query the schedule
curl -X POST http://localhost:5000/api/process_transcript \
  -H "Content-Type: application/json" \
  -d '{"transcript": "What does my week look like?"}'

# 4. Query a specific day
curl -X POST http://localhost:5000/api/process_transcript \
  -H "Content-Type: application/json" \
  -d '{"transcript": "What do I have on Monday?"}'

# 5. Delete an event
curl -X POST http://localhost:5000/api/process_transcript \
  -H "Content-Type: application/json" \
  -d '{"transcript": "Cancel the dentist appointment"}'

# 6. Verify deletion
curl http://localhost:5000/api/schedule/wednesday

# 7. Check week via API
curl http://localhost:5000/api/schedule/week

# 8. Clear a day
curl -X POST http://localhost:5000/api/process_transcript \
  -H "Content-Type: application/json" \
  -d '{"transcript": "Clear Monday"}'

# 9. Start fresh
curl -X POST http://localhost:5000/api/process_transcript \
  -H "Content-Type: application/json" \
  -d '{"transcript": "Start fresh"}'
```

---

For complete documentation, see the [main README](../README.md).
