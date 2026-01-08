---
trigger: always_on
---

# SmartPager Project

## Overview

SmartPager is an IoT voice-controlled scheduling system with two components:

### Raspberry Pi Client (`pi/`)

-   Records voice commands via **I2S SPH0645 MEMS microphone**
-   Uploads audio to server for processing
-   Receives and plays TTS schedule summaries via **I2S MAX98357A amplifier** to speaker
-   Button-triggered recording with LED status indicator

### Server (`server/`)

-   Receives audio uploads from Raspberry Pi
-   Transcribes speech using Whisper (STT)
-   Interprets schedule with LLM (GPT-4)
-   Optimizes daily calendar using OR-Tools constraint solver
-   Generates natural language summary
-   Returns TTS audio (Piper) and optimized schedule to client

## Hardware Configuration

| Component   | Interface  | Purpose                |
| ----------- | ---------- | ---------------------- |
| SPH0645     | I2S Input  | MEMS microphone (3.3V) |
| MAX98357A   | I2S Output | Mono amplifier (5V)    |
| 8Ω Speaker  | Analog     | TTS audio playback     |
| Push Button | GPIO 11    | Recording trigger      |
| LED         | GPIO 26    | Status indicator       |

## Development Guidelines

1. **Audio Pipeline**: All audio capture uses I2S (no USB mic assumptions)
2. **Server Communication**: REST API over HTTP, audio as base64 in JSON responses
3. **TTS Integration**: Server generates TTS, client plays through I2S amp
4. **Error Handling**: Graceful degradation if TTS unavailable
5. **Resource Management**: Clean up temp files after TTS playback


Here is the flow of data through the system when an audio file is uploaded:

High-Level Flow
Entry Point: 
audioCapture_server.py
 receives the POST request.
Orchestrator: 
modules/audio_pipeline.py
 manages the step-by-step execution.
Processing: Audio is transcribed, interpreted, and executed (updating both local JSON and Google Calendar).
Output: A spoken response is generated (TTS) and sent back to the client.
Visual Flowchart
mermaid
graph TD
    Client[Client (ESP32)] -->|POST /upload| Server[audioCapture_server.py]
    Server -->|process_audio_file| Pipeline[modules/audio_pipeline.py]
    
    subgraph "Audio Processing Pipeline"
        Pipeline -->|1. Transcribe| Whisper[modules/whisper_handler.py]
        Whisper -->|Transcript| Pipeline
        
        Pipeline -->|2. Classify| Router[modules/intent_router.py]
        Router -->|Intent & Params| Pipeline
        
        Pipeline -->|3. Handle Intent| Handler{route_intent}
        
        subgraph "Modify Schedule Logic"
            Handler -->|Parse Details| Interpreter[modules/llm_interpreter.py]
            Handler -->|Optimize Time| Scheduler[modules/scheduler.py]
            Handler -->|Save to Disk| Manager[modules/schedule_manager.py]
            Handler -->|Sync to Cloud| Calendar[modules/calendar_utils.py]
        end
        
        Pipeline -->|4. Generate Audio| TTS[modules/tts_handler.py]
    end
    
    TTS -->|WAV Audio| Pipeline
    Pipeline -->|JSON + Audio| Server
    Server -->|Response| Client


Module Breakdown
audioCapture_server.py
: The Flask web server. It handles the HTTP request, saves the raw audio file to disk, and calls the pipeline.
modules/audio_pipeline.py
: The "brain" that orchestrates the entire process. It calls the other modules in order.
modules/whisper_handler.py: Uses OpenAI Whisper to convert the raw audio file into text (Transcript).
modules/intent_router.py: Uses an LLM to decide what the user wants (e.g., MODIFY_SCHEDULE, QUERY_DAY) and extracts parameters.
modules/llm_interpreter.py
: Helper that converts the LLM's output into structured data objects (e.g., converting "tomorrow at 2" into a specific 
datetime
).
modules/scheduler.py: Contains logic to check for conflicts and optimize the schedule (e.g., fitting a flexible task into a free slot).
modules/schedule_manager.py
: Manages the local persistence layer. It reads/writes the JSON files in the 
schedule/
 directory.
modules/calendar_utils.py
: (New) Handles the integration with Google Calendar API to create, update, or delete real calendar events.
modules/tts_handler.py: Converts the final text response (e.g., "Added meeting for Tuesday") into a speech audio file using Piper TTS.