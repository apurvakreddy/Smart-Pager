# smartPager/server/modules/__init__.py
"""
SmartPager Server Modules

This package contains the audio processing pipeline components:
- whisper_handler: Speech-to-text using Whisper
- llm_interpreter: Natural language to structured schedule using GPT
- scheduler: OR-Tools based schedule optimization
- summary_generator: Natural language summary generation
- tts_handler: Text-to-speech using Piper
- audio_pipeline: Intent-based processing pipeline
- intent_router: LLM-based intent classification
- schedule_manager: Persistent weekly schedule storage
"""

from .audio_pipeline import (
    process_audio_file,
    process_transcript_only,
    ProcessingResult,
    AudioProcessingResult  # Backwards compatibility alias
)

from .schedule_manager import (
    get_schedule_manager,
    ScheduleManager,
    DaySchedule,
    DAYS_OF_WEEK
)

from .intent_router import (
    classify_intent,
    Intent,
    IntentResult
)

__all__ = [
    # Pipeline
    'process_audio_file',
    'process_transcript_only',
    'ProcessingResult',
    'AudioProcessingResult',
    # Schedule management
    'get_schedule_manager',
    'ScheduleManager', 
    'DaySchedule',
    'DAYS_OF_WEEK',
    # Intent routing
    'classify_intent',
    'Intent',
    'IntentResult',
]

# example server startup snippet
# from modules.calendar_sync import CalendarPoller
# from modules.schedule_manager import get_schedule_manager, DaySchedule, DAYS_OF_WEEK, get_date_for_day
# from dateutil import parser
# from dateutil.relativedelta import relativedelta
# from dateutil import tz
# 
# def calendar_delta_callback(delta):
#     manager = get_schedule_manager()
#     # Added events -> map them into DaySchedule and insert
#     for ev in delta.get("added", []):
#         # parse start -> day name
#         start = ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date")
#         if not start:
#             continue
#         # determine day name (lowercase)
#         dt = dateutil.parser.isoparse(start)
#         day_name = dt.strftime("%A").lower()
#         # Convert to your local event format
#         local_event = {
#             "name": ev.get("summary", "event"),
#             "start": start,
#             "end": ev.get("end", {}).get("dateTime") or ev.get("end", {}).get("date"),
#             "_calendar_id": ev.get("id"),
#             "_calendar_htmlLink": ev.get("htmlLink")
#         }
#         # Load day, append, and save
#         ds = manager.get_day_schedule(day_name)
#         ds.events.append(local_event)
#         manager.save_day_schedule(ds)
#         # Optionally generate a spoken notification or push to device
# 
#     # Handle updated & deleted similarily...
#     # For deletes, find the event by _calendar_id in day schedules and remove it.
# 
# # instantiate and start
# # poller = CalendarPoller(calendar_delta_callback, interval_seconds=300)
# # poller.start()

