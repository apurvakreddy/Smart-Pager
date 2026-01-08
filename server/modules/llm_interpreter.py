# smartPager/server/modules/llm_interpreter.py
"""
LLM-based schedule interpreter.

Converts intent router operations into scheduler-compatible format.
Also provides legacy single-day interpretation for backwards compatibility.
"""

import os
import json
from datetime import datetime, timedelta
from openai import OpenAI
from typing import Optional, Dict, Any, List
from pathlib import Path

from .schedule_manager import DAYS_OF_WEEK, normalize_day_name

BASE_DIR = Path(__file__).resolve().parent

PROMPT_DIR = BASE_DIR.parent / "prompts"
SYSTEM_PROMPT_PATH = PROMPT_DIR / "schedule_interpreter_system.txt"

def load_system_prompt() -> str:
    with open(SYSTEM_PROMPT_PATH, "r") as f:
        return f.read()
    

LEGACY_SYSTEM_PROMPT = load_system_prompt()

def get_openai_client() -> OpenAI:
    """Get OpenAI client with API key from environment"""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set in environment variables.")
    return OpenAI(api_key=api_key)


def call_chatgpt(client: OpenAI, transcript_text: str) -> str:
    """
    Sends the transcript + system instructions to ChatGPT.
    Returns raw content of assistant response.
    """
    # Add today's date to help with scheduling
    today = datetime.now().strftime("%Y-%m-%d")
    user_message = f"Today's date is {today}.\n\nUser transcript:\n{transcript_text}"

    response = client.chat.completions.create(
        model="gpt-4o-mini",  # Using gpt-4o-mini for cost efficiency
        messages=[
            {"role": "system", "content": LEGACY_SYSTEM_PROMPT},
            {"role": "user", "content": user_message}
        ],
        temperature=0.0,
    )

    return response.choices[0].message.content


def parse_json_response(raw_text: str) -> Dict[str, Any]:
    """
    Converts the raw JSON string from ChatGPT into a Python dictionary.
    Handles markdown code blocks if present.
    """
    # Remove markdown code blocks if present
    clean_text = raw_text.strip()
    if clean_text.startswith("```json"):
        clean_text = clean_text[7:]
    if clean_text.startswith("```"):
        clean_text = clean_text[3:]
    if clean_text.endswith("```"):
        clean_text = clean_text[:-3]
    clean_text = clean_text.strip()
    
    try:
        return json.loads(clean_text)
    except json.JSONDecodeError as e:
        print(f"[llm_interpreter] Invalid JSON returned by ChatGPT: {e}")
        print(f"[llm_interpreter] Raw response: {raw_text[:500]}")
        raise


def interpret_transcript(transcript_text: str) -> Optional[Dict[str, Any]]:
    """
    Legacy function to interpret a transcript and return structured schedule data.
    Used for backwards compatibility with single-day processing.
    
    Args:
        transcript_text: The raw transcript text from Whisper
        
    Returns:
        Dictionary with schedule data or None if failed
    """
    if not transcript_text or not transcript_text.strip():
        print("[llm_interpreter] Empty transcript provided")
        return None
        
    print(f"[llm_interpreter] Interpreting transcript: '{transcript_text[:100]}...'")
    
    try:
        client = get_openai_client()
        raw_json = call_chatgpt(client, transcript_text)
        structured_data = parse_json_response(raw_json)
        print(f"[llm_interpreter] Successfully parsed schedule data")
        return structured_data
    except Exception as e:
        print(f"[llm_interpreter] Error interpreting transcript: {e}")
        return None


# ==================== NEW MULTI-DAY FUNCTIONS ====================

def get_date_for_day(day_name: str, reference_date: datetime) -> datetime:
    """
    Get the datetime for a specific day name relative to reference date.
    
    Args:
        day_name: Normalized day name (e.g., "monday")
        reference_date: Reference datetime (usually current time)
        
    Returns:
        datetime object for that day in the current/next week
    """
    day_name = day_name.lower()
    
    # Debug logging to trace date issues
    print(f"[llm_interpreter] Calculating date for '{day_name}' relative to {reference_date}")
    
    if day_name == "today":
        return reference_date
    
    if day_name == "tomorrow":
        return reference_date + timedelta(days=1)
    
    # Find the day index
    try:
        target_day_idx = DAYS_OF_WEEK.index(day_name)
    except ValueError:
        # If invalid day name, return reference date
        return reference_date
    
    current_day_idx = reference_date.weekday()
    
    # Calculate days until target day
    days_ahead = target_day_idx - current_day_idx
    if days_ahead < 0:
        # Target day already passed this week, go to next week
        days_ahead += 7
    
    return reference_date + timedelta(days=days_ahead)


def parse_time_to_datetime(time_str: str, target_date: datetime) -> datetime:
    """
    Parse a time string (HH:MM) and combine with target date.
    
    Args:
        time_str: Time string like "14:00" or "2:30"
        target_date: The date to combine with
        
    Returns:
        datetime object with date and time
    """
    try:
        # Handle various time formats
        time_str = time_str.strip()
        
        # Try HH:MM format
        if ":" in time_str:
            parts = time_str.split(":")
            hour = int(parts[0])
            minute = int(parts[1]) if len(parts) > 1 else 0
        else:
            # Just hours
            hour = int(time_str)
            minute = 0
        
        return target_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
    except (ValueError, IndexError):
        # Default to 9am if parsing fails
        return target_date.replace(hour=9, minute=0, second=0, microsecond=0)


def estimate_end_time(start_time: datetime, event_name: str, duration_minutes: int = None) -> datetime:
    """
    Estimate end time based on event type if not provided.
    
    Args:
        start_time: Event start datetime
        event_name: Name of the event (for heuristics)
        duration_minutes: Explicit duration if provided
        
    Returns:
        Estimated end datetime
    """
    if duration_minutes:
        return start_time + timedelta(minutes=duration_minutes)
    
    # Heuristics based on event name
    name_lower = event_name.lower()
    
    if any(word in name_lower for word in ["meeting", "call", "standup", "sync"]):
        duration = 60  # 1 hour
    elif any(word in name_lower for word in ["lunch", "dinner", "breakfast", "coffee"]):
        duration = 60  # 1 hour
    elif any(word in name_lower for word in ["gym", "workout", "exercise", "yoga"]):
        duration = 90  # 1.5 hours
    elif any(word in name_lower for word in ["dentist", "doctor", "appointment"]):
        duration = 60  # 1 hour
    elif any(word in name_lower for word in ["class", "lecture", "seminar"]):
        duration = 90  # 1.5 hours
    else:
        duration = 60  # Default 1 hour
    
    return start_time + timedelta(minutes=duration)


def operation_to_scheduler_event(
    operation: Dict[str, Any], 
    reference_date: datetime
) -> Dict[str, Any]:
    """
    Convert an intent router operation to a scheduler-compatible event.
    
    Args:
        operation: Operation dict from intent router
        reference_date: Reference datetime for day calculation
        
    Returns:
        Event dict compatible with scheduler.py format
    """
    event = operation.get("event", {})
    day = operation.get("day", "today")
    
    # Normalize day name
    day_name = normalize_day_name(day, reference_date)
    
    # Get the target date
    target_date = get_date_for_day(day_name, reference_date)
    
    # Determine if fixed or flexible
    # If a duration is provided (task-like), default to flexible unless explicitly marked otherwise.
    # If no explicit time, give it a full-day window so the optimizer can place it.
    event_type = event.get("type")
    duration = event.get("durationMinutes")
    start_str = event.get("start")
    end_str = event.get("end")
    if not event_type:
        if duration:
            event_type = "flexible"
        else:
            event_type = "fixed"

    # If this is a flexible task with a duration but no explicit start/end,
    # give it a full-day window and let the optimizer place it.
    if event_type == "flexible" and duration and not start_str and not end_str:
        day_start = target_date.replace(hour=8, minute=0, second=0, microsecond=0)
        day_end = target_date.replace(hour=21, minute=0, second=0, microsecond=0)
        return {
            "name": event.get("name", "Unnamed Task"),
            "type": "flexible",
            "start": day_start.isoformat(),
            "end": day_end.isoformat(),
            "durationMinutes": duration,
            "earliestStart": day_start.isoformat(),
            "latestEnd": day_end.isoformat(),
            "day": day_name,
        }
    
    # Parse start time (default 09:00 if fixed or explicit start absent)
    start_dt = parse_time_to_datetime(start_str or "09:00", target_date)
    
    # Parse or estimate end time
    if end_str:
        end_dt = parse_time_to_datetime(end_str, target_date)
    else:
        end_dt = estimate_end_time(start_dt, event.get("name", ""), duration)
    
    return {
        "name": event.get("name", "Unnamed Event"),
        "_calendar_id": event.get("_calendar_id"),
        "type": event_type,
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "durationMinutes": duration,
        # If explicit times were provided for a flexible task, treat them as a window.
        "earliestStart": start_dt.isoformat() if event_type == "flexible" else None,
        "latestEnd": end_dt.isoformat() if event_type == "flexible" else None,
        "day": day_name,  # Keep track of which day this belongs to
    }


def operations_to_scheduler_format(
    operations: List[Dict[str, Any]], 
    reference_date: datetime
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Convert a list of operations to scheduler-compatible format, grouped by day.
    
    Args:
        operations: List of operations from intent router
        reference_date: Reference datetime
        
    Returns:
        Dictionary mapping day names to lists of events
    """
    # Group operations by day and action
    days_events = {day: {"add": [], "edit": [], "delete": []} for day in DAYS_OF_WEEK}
    
    for op in operations:
        action = op.get("action", "add").lower()
        day = normalize_day_name(op.get("day", "today"), reference_date)
        
        if action == "add":
            event = operation_to_scheduler_event(op, reference_date)
            days_events[day]["add"].append(event)
        elif action == "edit":
            event = operation_to_scheduler_event(op, reference_date)
            days_events[day]["edit"].append(event)
        elif action == "delete":
            # For delete, we just need the name
            event_info = op.get("event", {})
            event_name = event_info.get("name", "")
            if event_name:
                days_events[day]["delete"].append(event_info)
    
    return days_events


def build_day_schedule_for_optimizer(
    events: List[Dict[str, Any]], 
    day_date: datetime
) -> Dict[str, Any]:
    """
    Build a schedule structure that the OR-Tools scheduler can process.
    
    Args:
        events: List of events for this day
        day_date: The date of this day
        
    Returns:
        Schedule dict compatible with scheduler.schedule_day()
    """
    fixed_events = []
    flexible_tasks = []
    
    for event in events:
        if event.get("type") == "flexible":
            # Calculate duration from start/end
            start_dt = datetime.fromisoformat(event["start"])
            end_dt = datetime.fromisoformat(event["end"])
            duration = int((end_dt - start_dt).total_seconds() / 60)
            
            flexible_tasks.append({
                "name": event["name"],
                "type": "flexible",
                "durationMinutes": duration,
                # Optional constraints
                "earliestStart": event.get("start"),
                "latestEnd": event.get("end"),
            })
        else:
            fixed_events.append({
                "name": event["name"],
                "type": "fixed",
                "start": event["start"],
                "end": event["end"],
            })
    
    return {
        "timeZone": "America/New_York",
        "rules": {
            "dayStart": "08:00",
            "dayEnd": "21:00"
        },
        "events": fixed_events,
        "tasks": flexible_tasks,
        "date": day_date.isoformat(),
    }
