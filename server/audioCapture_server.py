"""
SmartPager - Audio Capture & Processing Server
Local development server for ESP32 voice-controlled scheduling

Features:
- Receives WAV files from ESP32 via POST endpoint
- Transcribes audio using Whisper
- Interprets schedule with LLM (GPT-4)
- Optimizes schedule with OR-Tools
- Generates natural language summary
- Optional TTS output
- Web interface for recordings and schedules

Run with: python audioCapture_server.py
Then access at: http://localhost:5000
"""

from dotenv import load_dotenv
import os
load_dotenv()  # Load environment variables from .env file

# Ensure credentials path is set for calendar integration
# (Handled by simple_calendar module now)

from flask import Flask, request, jsonify, send_file, render_template_string, Response
import json
import base64
from datetime import datetime
from pathlib import Path
import threading
import shutil
from typing import Optional

# Import processing modules (lazy load for faster startup)
_pipeline_loaded = False
_schedule_manager = None

def _load_pipeline():
    global _pipeline_loaded, process_audio_file, process_transcript_only
    if not _pipeline_loaded:
        from modules import process_audio_file, process_transcript_only
        _pipeline_loaded = True
    return process_audio_file, process_transcript_only

def _get_schedule_manager():
    global _schedule_manager
    if _schedule_manager is None:
        from modules import get_schedule_manager
        _schedule_manager = get_schedule_manager()
    return _schedule_manager

app = Flask(__name__)

# Configuration
AUDIO_DIR = "recordings"
OUTPUT_DIR = "output"
PORT = 8000
TIMEZONE = os.getenv("TIMEZONE", "America/New_York")

# Create directories if they don't exist
os.makedirs(AUDIO_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Store processing results
processing_results = {}

# Global flag to disable TTS audio in response (for cleaner terminal output)
DISABLE_TTS_RESPONSE = False


def cleanup_on_startup():
    """
    Clean up schedule data and output transcripts on server startup.
    """
    print("\n🧹 Running startup cleanup...")
    
    # 1. Clear output directory
    if os.path.exists(OUTPUT_DIR):
        try:
            # Remove all contents of OUTPUT_DIR but keep the directory itself
            for item in os.listdir(OUTPUT_DIR):
                item_path = os.path.join(OUTPUT_DIR, item)
                if os.path.isfile(item_path) or os.path.islink(item_path):
                    os.unlink(item_path)
                elif os.path.isdir(item_path):
                    shutil.rmtree(item_path)
            print(f"✅ Cleared output directory: {OUTPUT_DIR}")
        except Exception as e:
            print(f"❌ Error clearing output directory: {e}")
    
    # 2. Clear schedule data
    try:
        manager = _get_schedule_manager()
        manager.clear_week()
        print(f"✅ Cleared weekly schedule data")
    except Exception as e:
        print(f"❌ Error clearing schedule data: {e}")
    
    print("✨ Cleanup complete\n")

# Run cleanup immediately
cleanup_on_startup()


def get_tts_audio_base64(result) -> Optional[str]:
    """
    Read the TTS audio file and return as base64 encoded string.
    Returns None if TTS audio is not available.
    """
    if result.summary_audio_path and os.path.exists(result.summary_audio_path):
        try:
            with open(result.summary_audio_path, 'rb') as f:
                audio_data = f.read()
            return base64.b64encode(audio_data).decode('utf-8')
        except Exception as e:
            print(f"[TTS] Error reading audio file: {e}")
            return None
    return None


def build_response_with_tts(result, extra_data: dict = None) -> dict:
    """
    Build a response dictionary that includes TTS audio if available.
    
    The TTS audio is included as base64-encoded WAV data in the 'tts_audio' field.
    This allows the Raspberry Pi to decode and play it directly.
    """
    response = result.to_dict()
    
    # Add any extra data (like upload info)
    if extra_data:
        response.update(extra_data)
    
    # Add TTS audio if available
    tts_audio = get_tts_audio_base64(result)
    if tts_audio and not DISABLE_TTS_RESPONSE:
        response['tts_audio'] = tts_audio
        response['tts_audio_format'] = 'wav'
        print(f"[TTS] Included {len(tts_audio)} bytes of base64 audio in response")
    
    # Backwards compatibility: also include summary field if response_text exists
    if hasattr(result, 'response_text') and result.response_text:
        response['summary'] = result.response_text
    
    return response

def get_next_filename():
    """Get the next numbered filename"""
    existing_files = list(Path(AUDIO_DIR).glob("recording_*.wav"))
    if not existing_files:
        return "recording_001.wav"
    
    # Extract numbers from existing files
    numbers = []
    for f in existing_files:
        try:
            num = int(f.stem.split('_')[1])
            numbers.append(num)
        except (IndexError, ValueError):
            continue
    
    next_num = max(numbers) + 1 if numbers else 1
    return f"recording_{next_num:03d}.wav"

@app.route('/')
def index():
    """Web interface"""
    from flask import render_template
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_audio():
    """
    Receive audio file from Raspberry Pi and automatically process it.
    Supports both multipart form data and raw streaming.
    
    Pipeline: Upload → Save → Whisper STT → Intent Classification → Handler → TTS
    
    Optional parameters:
    - client_datetime: ISO-8601 timestamp from client (for day resolution)
    """
    try:
        # Extract client datetime if provided
        client_datetime = None
        client_dt_str = request.headers.get('X-Client-Datetime') or request.args.get('client_datetime')
        if client_dt_str:
            try:
                client_datetime = datetime.fromisoformat(client_dt_str.replace('Z', '+00:00'))
                
                # Validate year - if client thinks it's 2024 or earlier when server is 2025+, ignore it
                from datetime import timezone as dt_timezone
                server_now = datetime.now(dt_timezone.utc) # Compare in UTC to be safe, or local
                # Better: use the configured timezone for "server now"
                import pytz
                tz = pytz.timezone(TIMEZONE)
                server_now = datetime.now(tz)
                
                # Ensure client_datetime is timezone aware
                if client_datetime.tzinfo is None:
                    client_datetime = tz.localize(client_datetime)
                
                if client_datetime.year < server_now.year:
                    print(f"⚠️ Client time {client_datetime} is in the past year. Using server time: {server_now}")
                    client_datetime = server_now
                else:
                    print(f"📅 Client datetime: {client_datetime}")
            except ValueError:
                print(f"⚠️ Invalid client datetime: {client_dt_str}, using server time")
        
        # Check if it's multipart form data
        if 'audio' in request.files:
            file = request.files['audio']
            
            if file.filename == '':
                return jsonify({
                    'success': False,
                    'error': 'Empty filename'
                }), 400
            
            # Check for client_datetime in form data
            if not client_datetime and 'client_datetime' in request.form:
                try:
                    client_datetime = datetime.fromisoformat(request.form['client_datetime'])
                except ValueError:
                    pass
            
            # Get next filename
            filename = get_next_filename()
            filepath = os.path.join(AUDIO_DIR, filename)
            
            # Save file
            file.save(filepath)
            file_size = os.path.getsize(filepath)
            
            print(f"✅ Received (multipart): {filename} ({file_size} bytes)")
        
        # Otherwise, it's streaming raw data
        else:
            # Get next filename
            filename = get_next_filename()
            filepath = os.path.join(AUDIO_DIR, filename)
            
            # Stream data directly to file
            print(f"📥 Receiving streamed upload: {filename}")
            
            file_size = 0
            with open(filepath, 'wb') as f:
                # Read in chunks from request stream
                while True:
                    chunk = request.stream.read(4096)
                    if not chunk:
                        break
                    f.write(chunk)
                    file_size += len(chunk)
            
            print(f"✅ Received (streamed): {filename} ({file_size} bytes)")
        
        # ============================================================
        # INTENT-BASED PROCESSING PIPELINE
        # ============================================================
        print(f"\n🎙️ Processing: {filename}")
        
        # Load pipeline modules (lazy load for faster startup)
        process_audio_file, _ = _load_pipeline()
        
        # Create output directory for this file
        file_output_dir = os.path.join(OUTPUT_DIR, Path(filename).stem)
        
        # Process the audio through the intent-based pipeline
        result = process_audio_file(
            filepath, 
            file_output_dir, 
            client_datetime=client_datetime
        )
        
        # Store result for later retrieval
        processing_results[filename] = result.to_dict()
        
        # Build response with both upload and processing info (includes TTS audio)
        response = build_response_with_tts(result, {
            'upload': {
                'filename': filename,
                'size_bytes': file_size
            }
        })
        response['success'] = result.success
        
        # Determine if this is a "successful" failure (interactive state)
        interactive_errors = ["Clarification needed", "Conflict detected"]
        is_interactive = result.error in interactive_errors
        
        if result.success or is_interactive:
            print(f"✅ Processing complete for {filename}")
            if is_interactive:
                print(f"   Interactive state: {result.error}")
            else:
                print(f"   Intent: {result.intent} | Response: {result.response_text[:80] if result.response_text else 'N/A'}...")
            status_code = 200
        else:
            print(f"⚠️ Processing had issues for {filename}: {result.error}")
            status_code = 500
        
        return jsonify(response), status_code
    
    except Exception as e:
        print(f"❌ Error in upload/process: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/recordings', methods=['GET'])
def list_recordings():
    """API endpoint to list all recordings"""
    try:
        recordings = []
        total_size = 0
        
        for filename in sorted(Path(AUDIO_DIR).glob("recording_*.wav"), reverse=True):
            stat = filename.stat()
            size_bytes = stat.st_size
            total_size += size_bytes
            
            # Estimate duration - read sample rate from WAV header
            # Default: 16kHz, 16-bit, mono = 32000 bytes/sec
            bytes_per_sec = 32000
            
            # Try to read actual sample rate from WAV header
            try:
                with open(filename, 'rb') as f:
                    f.seek(24)  # Sample rate is at byte 24-27
                    sample_rate_bytes = f.read(4)
                    sample_rate = int.from_bytes(sample_rate_bytes, 'little')
                    if sample_rate > 0:
                        bytes_per_sec = sample_rate * 2  # 16-bit mono
            except:
                pass  # Use default if can't read
            
            duration_sec = max(0, size_bytes - 44) / bytes_per_sec  # Subtract WAV header
            
            # Try to read processing result
            result_data = {}
            try:
                # Assuming output dir name matches filename stem
                output_dir = os.path.join(OUTPUT_DIR, filename.stem)
                result_json_path = os.path.join(output_dir, "result.json")
                
                if os.path.exists(result_json_path):
                    with open(result_json_path, 'r') as f:
                        result_data = json.load(f)
            except Exception as e:
                print(f"Error reading result for {filename}: {e}")

            recordings.append({
                'filename': filename.name,
                'size_kb': size_bytes / 1024,
                'timestamp': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                'duration_estimate_sec': duration_sec,
                'transcript': result_data.get('transcript'),
                'intent': result_data.get('intent'),
                'summary': result_data.get('response_text')
            })
        
        return jsonify({
            'recordings': recordings,
            'total_size_mb': total_size / (1024 * 1024)
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/audio/<filename>')
def serve_audio(filename):
    """Serve audio file for playback"""
    try:
        filepath = os.path.join(AUDIO_DIR, filename)
        if not os.path.exists(filepath):
            return jsonify({'error': 'File not found'}), 404
        return send_file(filepath, mimetype='audio/wav')
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/download/<filename>')
def download_audio(filename):
    """Download audio file"""
    try:
        filepath = os.path.join(AUDIO_DIR, filename)
        if not os.path.exists(filepath):
            return jsonify({'error': 'File not found'}), 404
        return send_file(filepath, as_attachment=True, download_name=filename)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==================== PROCESSING ENDPOINTS ====================

@app.route('/api/process/<filename>', methods=['POST'])
def process_recording(filename):
    """
    Process a specific recording through the full pipeline:
    Audio → Whisper → LLM → Scheduler → Summary → TTS
    
    Returns JSON with processing results and TTS audio (base64-encoded WAV).
    """
    try:
        filepath = os.path.join(AUDIO_DIR, filename)
        if not os.path.exists(filepath):
            return jsonify({'error': 'File not found'}), 404
        
        # Load pipeline modules
        process_audio_file, _ = _load_pipeline()
        
        # Create output directory for this file
        file_output_dir = os.path.join(OUTPUT_DIR, Path(filename).stem)
        
        # Process the audio
        print(f"\n🎙️ Processing: {filename}")
        result = process_audio_file(filepath, file_output_dir)
        
        # Store result
        processing_results[filename] = result.to_dict()
        
        # Return with TTS audio included
        response = build_response_with_tts(result)
        return jsonify(response), 200 if result.success else 500
    
    except Exception as e:
        print(f"❌ Processing error: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/process_transcript', methods=['POST'])
def process_transcript():
    """
    Process a transcript directly (skip Whisper).
    Useful for testing or when transcript is pre-existing.
    
    Request body: 
    { 
        "transcript": "Add a meeting on Monday at 3pm...",
        "client_datetime": "2024-12-07T14:30:00" (optional)
    }
    
    Returns JSON with processing results and TTS audio (base64-encoded WAV).
    """
    try:
        data = request.get_json()
        if not data or 'transcript' not in data:
            return jsonify({
                'error': 'Missing transcript in request body'
            }), 400
        
        transcript = data['transcript']
        
        # Parse client datetime if provided
        client_datetime = None
        if 'client_datetime' in data:
            try:
                client_datetime = datetime.fromisoformat(data['client_datetime'])
            except ValueError:
                pass
        
        # Load pipeline modules
        _, process_transcript_only = _load_pipeline()
        
        # Import TTS handler for transcript processing
        from modules.tts_handler import synthesize_speech, is_tts_available
        
        # Process the transcript with intent-based routing
        print(f"\n📝 Processing transcript: '{transcript[:50]}...'")
        result = process_transcript_only(transcript, client_datetime)
        
        # Generate TTS if available and processing succeeded
        if result.success and is_tts_available() and result.response_text:
            # Create a temp output directory for TTS
            import tempfile
            tts_output_path = os.path.join(tempfile.gettempdir(), "smartpager_tts_transcript.wav")
            result.summary_audio_path = synthesize_speech(result.response_text, tts_output_path)
        
        # Return with TTS audio included
        response = build_response_with_tts(result)
        # Determine if this is a "successful" failure (interactive state)
        interactive_errors = ["Clarification needed", "Conflict detected"]
        is_interactive = result.error in interactive_errors
        
        status_code = 200 if (result.success or is_interactive) else 500
        return jsonify(response), status_code
    
    except Exception as e:
        print(f"❌ Processing error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/process_latest', methods=['POST'])
def process_latest():
    """
    Process the most recently uploaded recording.
    Returns JSON with processing results and TTS audio (base64-encoded WAV).
    """
    try:
        # Find the most recent recording
        recordings = sorted(Path(AUDIO_DIR).glob("recording_*.wav"), 
                           key=lambda x: x.stat().st_mtime, reverse=True)
        
        if not recordings:
            return jsonify({'error': 'No recordings found'}), 404
        
        latest = recordings[0]
        print(f"\n🎙️ Processing latest: {latest.name}")
        
        # Load pipeline modules
        process_audio_file, _ = _load_pipeline()
        
        # Create output directory
        file_output_dir = os.path.join(OUTPUT_DIR, latest.stem)
        
        # Process the audio
        result = process_audio_file(str(latest), file_output_dir)
        
        # Store result
        processing_results[latest.name] = result.to_dict()
        
        # Return with TTS audio included
        response = build_response_with_tts(result, {'filename': latest.name})
        
        return jsonify(response), 200 if result.success else 500
    
    except Exception as e:
        print(f"❌ Processing error: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/results/<filename>')
def get_results(filename):
    """Get processing results for a specific recording"""
    if filename in processing_results:
        return jsonify(processing_results[filename])
    
    # Check if output directory exists with results
    output_dir = os.path.join(OUTPUT_DIR, Path(filename).stem)
    if os.path.exists(output_dir):
        result = {
            'filename': filename,
            'processed': True,
            'output_dir': output_dir
        }
        
        # Read transcript if available
        transcript_path = os.path.join(output_dir, 'transcript.txt')
        if os.path.exists(transcript_path):
            with open(transcript_path, 'r') as f:
                result['transcript'] = f.read()
        
        # Read summary if available
        summary_path = os.path.join(output_dir, 'summary.txt')
        if os.path.exists(summary_path):
            with open(summary_path, 'r') as f:
                result['summary'] = f.read()
        
        # Read schedule JSON if available
        schedule_path = os.path.join(output_dir, 'schedule.json')
        if os.path.exists(schedule_path):
            with open(schedule_path, 'r') as f:
                result['schedule'] = json.load(f)
        
        return jsonify(result)
    
    return jsonify({'error': 'No results found for this recording'}), 404


@app.route('/api/tts/<filename>')
def get_tts_audio(filename):
    """
    Get the TTS audio file for a specific recording.
    Returns the WAV file directly for playback.
    """
    try:
        # Strip extension if provided
        base_name = Path(filename).stem
        tts_path = os.path.join(OUTPUT_DIR, base_name, "summary.wav")
        
        if not os.path.exists(tts_path):
            return jsonify({'error': 'TTS audio not found for this recording'}), 404
        
        return send_file(tts_path, mimetype='audio/wav')
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/agenda/today')
def get_agenda_today():
    """
    Get today's agenda from the most recent processing result.
    This endpoint is designed for ESP32 consumption.
    """
    # Find the most recent result with an agenda
    for filename in sorted(processing_results.keys(), reverse=True):
        result = processing_results[filename]
        if result.get('success') and result.get('agenda'):
            return jsonify(result['agenda'])
    
    return jsonify({
        'next_item': None,
        'today': [],
        'message': 'No schedule processed yet'
    })


@app.route('/api/agenda/next')
def get_agenda_next():
    """
    Get the next upcoming event.
    This endpoint is designed for ESP32 consumption.
    """
    for filename in sorted(processing_results.keys(), reverse=True):
        result = processing_results[filename]
        if result.get('success') and result.get('agenda'):
            agenda = result['agenda']
            if agenda.get('next_item'):
                return jsonify(agenda['next_item'])
    
    return jsonify({
        'title': 'No upcoming events',
        'start': None,
        'end': None
    })


# ==================== WEEKLY SCHEDULE API ENDPOINTS ====================

@app.route('/api/schedule/week', methods=['GET'])
def get_week_schedule():
    """
    Get the entire week's schedule.
    
    Returns:
        JSON with all days' schedules and metadata
    """
    try:
        manager = _get_schedule_manager()
        manager.check_and_reset_if_new_week()
        
        week_data = manager.get_week_summary_data()
        return jsonify({
            'success': True,
            **week_data
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/schedule/<day>', methods=['GET'])
def get_day_schedule(day):
    """
    Get schedule for a specific day.
    
    Args:
        day: Day name (monday, tuesday, etc.) or 'today', 'tomorrow'
    
    Returns:
        JSON with day's events
    """
    try:
        manager = _get_schedule_manager()
        manager.check_and_reset_if_new_week()
        
        # Normalize day name
        from modules.schedule_manager import normalize_day_name, DAYS_OF_WEEK
        day_name = normalize_day_name(day, datetime.now())
        
        if day_name not in DAYS_OF_WEEK:
            return jsonify({
                'success': False,
                'error': f'Invalid day: {day}'
            }), 400
        
        schedule = manager.get_day_schedule(day_name)
        
        return jsonify({
            'success': True,
            'day': day_name,
            'events': schedule.events,
            'last_updated': schedule.last_updated
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/schedule/<day>', methods=['DELETE'])
def clear_day_schedule(day):
    """
    Clear all events for a specific day.
    
    Args:
        day: Day name (monday, tuesday, etc.)
    
    Returns:
        JSON confirmation
    """
    try:
        manager = _get_schedule_manager()
        
        from modules.schedule_manager import normalize_day_name, DAYS_OF_WEEK
        day_name = normalize_day_name(day, datetime.now())
        
        if day_name not in DAYS_OF_WEEK:
            return jsonify({
                'success': False,
                'error': f'Invalid day: {day}'
            }), 400
        
        had_events = manager.clear_day(day_name)
        
        return jsonify({
            'success': True,
            'day': day_name,
            'had_events': had_events,
            'message': f'Cleared {day_name} schedule'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/schedule/week', methods=['DELETE'])
def clear_week_schedule():
    """
    Clear the entire week's schedule.
    
    Returns:
        JSON confirmation
    """
    try:
        manager = _get_schedule_manager()
        manager.clear_week()
        
        return jsonify({
            'success': True,
            'message': 'Cleared entire week schedule'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/schedule/<day>/event', methods=['POST'])
def add_event_to_day(day):
    """
    Add an event to a specific day (direct API, bypasses voice).
    
    Request body:
    {
        "name": "Event name",
        "start": "14:00",
        "end": "15:00",
        "type": "fixed" (optional, default: fixed)
    }
    
    Returns:
        JSON with updated day schedule
    """
    try:
        manager = _get_schedule_manager()
        
        from modules.schedule_manager import normalize_day_name, DAYS_OF_WEEK
        day_name = normalize_day_name(day, datetime.now())
        
        if day_name not in DAYS_OF_WEEK:
            return jsonify({
                'success': False,
                'error': f'Invalid day: {day}'
            }), 400
        
        data = request.get_json()
        if not data or not data.get('name'):
            return jsonify({
                'success': False,
                'error': 'Missing event name'
            }), 400
        
        # Build event with proper datetime
        from modules.llm_interpreter import get_date_for_day, parse_time_to_datetime, estimate_end_time
        
        day_date = get_date_for_day(day_name, datetime.now())
        
        start_str = data.get('start', '09:00')
        start_dt = parse_time_to_datetime(start_str, day_date)
        
        end_str = data.get('end')
        if end_str:
            end_dt = parse_time_to_datetime(end_str, day_date)
        else:
            end_dt = estimate_end_time(start_dt, data['name'])
        
        event = {
            'name': data['name'],
            'type': data.get('type', 'fixed'),
            'start': start_dt.isoformat(),
            'end': end_dt.isoformat()
        }
        
        # Add and optimize
        from modules.scheduler import optimize_day_events
        
        schedule = manager.get_day_schedule(day_name)
        schedule.events.append(event)
        
        optimized = optimize_day_events(schedule.events, day_date)
        
        from modules.schedule_manager import DaySchedule
        updated_schedule = DaySchedule(day=day_name, events=optimized.get('events', []))
        manager.save_day_schedule(updated_schedule)
        
        return jsonify({
            'success': True,
            'day': day_name,
            'added_event': event,
            'events': updated_schedule.events
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/schedule/<day>/event/<event_name>', methods=['DELETE'])
def delete_event_from_day(day, event_name):
    """
    Delete an event from a specific day by name.
    
    Args:
        day: Day name
        event_name: Name of event to delete (partial match supported)
    
    Returns:
        JSON confirmation
    """
    try:
        manager = _get_schedule_manager()
        
        from modules.schedule_manager import normalize_day_name, DAYS_OF_WEEK
        day_name = normalize_day_name(day, datetime.now())
        
        if day_name not in DAYS_OF_WEEK:
            return jsonify({
                'success': False,
                'error': f'Invalid day: {day}'
            }), 400
        
        removed = manager.remove_event_from_day(day_name, event_name)
        
        if removed:
            return jsonify({
                'success': True,
                'day': day_name,
                'removed_event': removed
            })
        else:
            return jsonify({
                'success': False,
                'error': f'Event "{event_name}" not found on {day_name}'
            }), 404
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/schedule/summary', methods=['GET'])
def get_week_summary_text():
    """
    Get a natural language summary of the week.
    Useful for TTS or display.
    
    Returns:
        JSON with summary text
    """
    try:
        manager = _get_schedule_manager()
        manager.check_and_reset_if_new_week()
        
        week_data = manager.get_week_summary_data()
        
        from modules.summary_generator import generate_week_summary
        summary = generate_week_summary(week_data['days'])
        
        return jsonify({
            'success': True,
            'summary': summary,
            'total_events': week_data['total_events']
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# ==================== UPLOAD WITH AUTO-PROCESS ====================

@app.route('/upload_and_process', methods=['POST'])
def upload_and_process():
    """
    Upload audio and immediately process it.
    Returns both upload confirmation, processing results, and TTS audio (base64-encoded WAV).
    """
    try:
        # First, handle the upload (same as /upload)
        if 'audio' in request.files:
            file = request.files['audio']
            if file.filename == '':
                return jsonify({'success': False, 'error': 'Empty filename'}), 400
            
            filename = get_next_filename()
            filepath = os.path.join(AUDIO_DIR, filename)
            file.save(filepath)
            file_size = os.path.getsize(filepath)
            print(f"✅ Received (multipart): {filename} ({file_size} bytes)")
            
        else:
            filename = get_next_filename()
            filepath = os.path.join(AUDIO_DIR, filename)
            
            total_bytes = 0
            with open(filepath, 'wb') as f:
                while True:
                    chunk = request.stream.read(4096)
                    if not chunk:
                        break
                    f.write(chunk)
                    total_bytes += len(chunk)
            
            file_size = total_bytes
            print(f"✅ Received (streamed): {filename} ({total_bytes} bytes)")
        
        # Now process the audio
        process_audio_file, _ = _load_pipeline()
        file_output_dir = os.path.join(OUTPUT_DIR, Path(filename).stem)
        result = process_audio_file(filepath, file_output_dir)
        
        # Store result
        processing_results[filename] = result.to_dict()
        
        # Build response with TTS audio included
        response = build_response_with_tts(result, {
            'upload': {
                'success': True,
                'filename': filename,
                'size_bytes': file_size
            }
        })
        
        return jsonify(response), 200 if result.success else 500
    
    except Exception as e:
        print(f"❌ Upload/process error: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/test/calendar/add', methods=['POST'])
def test_calendar_add():
    """
    Test endpoint to add an event directly to Google Calendar.
    
    Request body:
    {
        "title": "Test Event",
        "start": "2023-12-01T10:00:00",
        "end": "2023-12-01T11:00:00",
        "description": "Optional description"
    }
    """
    try:
        data = request.get_json()
        if not data or not data.get('title') or not data.get('start') or not data.get('end'):
            return jsonify({
                'success': False,
                'error': 'Missing required fields: title, start, end'
            }), 400
        
        from modules.calendar_utils import create_event
        
        print(f"\n🧪 [TEST] Attempting to create calendar event: {data['title']}")
        print(f"   Start: {data['start']}")
        print(f"   End:   {data['end']}")
        
        result = create_event(
            title=data['title'],
            start=data['start'],
            end=data['end'],
            description=data.get('description', 'Created via test endpoint')
        )
        
        if result.get('status') == 'success':
            print(f"✅ [TEST] Event created successfully! ID: {result['event']['id']}")
            return jsonify({
                'success': True,
                'event': result['event']
            })
        else:
            print(f"❌ [TEST] Failed to create event: {result.get('error')}")
            return jsonify({
                'success': False,
                'error': result.get('error')
            }), 500
            
    except Exception as e:
        print(f"❌ [TEST] Exception: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


if __name__ == '__main__':
    import sys
    if "--no-audio" in sys.argv:
        DISABLE_TTS_RESPONSE = True
        print("🔇 TTS Audio response disabled via flag")

    print("=" * 70)
    print("🎤 SmartPager Audio Capture & Processing Server")
    print("=" * 70)
    print(f"📂 Recordings: {os.path.abspath(AUDIO_DIR)}")
    print(f"📁 Output: {os.path.abspath(OUTPUT_DIR)}")
    print(f"📅 Schedules: {os.path.abspath('schedule')}")
    print(f"🌐 Server: http://0.0.0.0:{PORT}")
    print("=" * 70)
    print("\n📡 AUDIO ENDPOINTS:")
    print(f"  POST /upload                  - Upload + auto-process audio ⭐")
    print(f"  POST /api/process/<file>      - Re-process existing file")
    print(f"  POST /api/process_latest      - Re-process most recent file")
    print(f"  POST /api/process_transcript  - Process text directly")
    print(f"  GET  /api/tts/<file>          - Get TTS audio for recording")
    print(f"  GET  /api/recordings          - List all recordings")
    print("=" * 70)
    print("\n📅 SCHEDULE ENDPOINTS:")
    print(f"  GET    /api/schedule/week          - Get entire week schedule")
    print(f"  DELETE /api/schedule/week          - Clear entire week")
    print(f"  GET    /api/schedule/<day>         - Get day schedule (monday, today, etc)")
    print(f"  DELETE /api/schedule/<day>         - Clear day schedule")
    print(f"  POST   /api/schedule/<day>/event   - Add event to day")
    print(f"  DELETE /api/schedule/<day>/event/<name> - Delete event")
    print(f"  GET    /api/schedule/summary       - Get week summary text")
    print(f"  GET    /api/agenda/today           - Get today's agenda (legacy)")
    print(f"  GET    /api/agenda/next            - Get next event (legacy)")
    print("=" * 70)
    print("\n🎯 SUPPORTED VOICE COMMANDS:")
    print("  • Add events:   'Add meeting Monday 2pm', 'Schedule lunch tomorrow'")
    print("  • Query day:    'What's on Monday?', 'What do I have today?'")
    print("  • Query week:   'What does my week look like?'")
    print("  • Delete:       'Cancel dentist appointment', 'Remove gym on Tuesday'")
    print("  • Clear:        'Clear Monday', 'Start fresh'")
    print("  • Help:         'What can you do?'")
    print("=" * 70)
    print("\n⚙️ REQUIREMENTS:")
    print("  - OpenAI API key in .env file (OPENAI_API_KEY=...)")
    print("  - Whisper: pip install openai-whisper")
    print("  - OR-Tools: pip install ortools")
    print("  - TTS: pip install piper-tts (+ download model)")
    print("=" * 70)
    print("\n🔄 PIPELINE: Audio → Whisper → Intent Classification → Handler → TTS")
    print("=" * 70)
    print("\nWaiting for recordings from Raspberry Pi...")
    print("Press Ctrl+C to stop\n")
    
    app.run(host='0.0.0.0', port=5000, debug=True)
