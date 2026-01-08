# smartPager/server/modules/audio_pipeline.py
"""
Intent-based audio processing pipeline.

Handles the complete flow:
Audio → Whisper STT → Intent Classification → Route to Handler → Response → TTS

Supports multiple intents:
- MODIFY_SCHEDULE: Add, edit, delete events
- QUERY_DAY: Get schedule for a specific day
- QUERY_WEEK: Get week overview
- CLEAR_DAY: Clear a specific day
- CLEAR_WEEK: Clear entire week
- HELP: Get help information
"""

import os
import json
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime
from dataclasses import dataclass, field

from .whisper_handler import transcribe_audio_file
from .intent_router import classify_intent, Intent, IntentResult, get_help_response, validate_operations
from .llm_interpreter import (
    operations_to_scheduler_format, 
    operation_to_scheduler_event,
    get_date_for_day
)
from .schedule_manager import (
    get_schedule_manager, 
    ScheduleManager,
    DaySchedule,
    DAYS_OF_WEEK,
    normalize_day_name,
    get_day_from_datetime
)
from .scheduler import merge_and_optimize_events, optimize_day_events
from .summary_generator import (
    generate_summary_text,
    generate_agenda_for_esp32,
    generate_day_summary,
    generate_week_summary,
    generate_changes_summary,
    generate_changes_summary_with_conflicts,
    generate_clear_confirmation,
    generate_query_response
)
from .tts_handler import synthesize_speech, is_tts_available
from .context_manager import get_context_manager, ContextState
from .simple_calendar import create_event, update_event, delete_event, find_event_by_details


@dataclass
class ProcessingResult:
    """Container for the complete processing result"""
    
    success: bool = False
    error: Optional[str] = None
    
    # Input data
    transcript: Optional[str] = None
    client_datetime: Optional[str] = None
    
    # Intent classification
    intent: Optional[str] = None
    intent_confidence: float = 0.0
    intent_parameters: Dict[str, Any] = field(default_factory=dict)
    
    # Processing results
    changes_made: Dict[str, Any] = field(default_factory=dict)
    affected_days: List[str] = field(default_factory=list)
    calendar_debug: List[str] = field(default_factory=list)
    
    # Output
    response_text: Optional[str] = None  # Natural language response
    summary_audio_path: Optional[str] = None  # TTS audio file
    
    # Schedule data (for API responses)
    schedule_data: Optional[Dict[str, Any]] = None
    agenda: Optional[Dict[str, Any]] = None
    
    # Timing
    processing_time_ms: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON response"""
        return {
            "success": self.success,
            "error": self.error,
            "transcript": self.transcript,
            "intent": self.intent,
            "intent_confidence": self.intent_confidence,
            "response_text": self.response_text,
            "summary_audio_available": self.summary_audio_path is not None,
            "changes_made": self.changes_made,
            "affected_days": self.affected_days,
            "calendar_debug": self.calendar_debug,
            "schedule": self.schedule_data,
            "agenda": self.agenda,
            "processing_time_ms": self.processing_time_ms
        }


# Keep old class name for backwards compatibility
AudioProcessingResult = ProcessingResult


def process_audio_file(
    audio_path: str,
    output_dir: str = None,
    client_datetime: datetime = None,
    generate_tts: bool = True
) -> ProcessingResult:
    """
    Complete intent-based audio processing pipeline.
    
    Args:
        audio_path: Path to the uploaded audio file
        output_dir: Directory for output files (transcript, summary audio)
        client_datetime: Current datetime from client (for day resolution)
        generate_tts: Whether to generate TTS audio output
        
    Returns:
        ProcessingResult with all processing outputs
    """
    result = ProcessingResult()
    start_time = datetime.now()
    
    # Ignore client_datetime to enforce server-side time authority
    # This prevents timezone confusion if client sends offset-aware or incorrect times
    client_datetime = datetime.now()
    result.client_datetime = client_datetime.isoformat()
    
    # Setup output directory
    if output_dir is None:
        output_dir = Path(audio_path).parent / "output"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"\n{'='*60}")
    print(f"[pipeline] Starting intent-based audio processing")
    print(f"[pipeline] Input: {audio_path}")
    print(f"[pipeline] Client datetime: {client_datetime}")
    print(f"{'='*60}")
    
    def _looks_like_clarification(text: str) -> bool:
        """Heuristic: quick replies that likely complete a prior request."""
        if not text:
            return False
        t = text.lower().strip()
        # Very short replies (single phrase) are likely clarifications
        if len(t) <= 60:
            return True
        clarification_tokens = [
            "at ", "on ", "for ", "after ", "before ", "move", "make it", "change to",
            "pm", "am", "tomorrow", "today", "next", "this", "that time"
        ]
        return any(tok in t for tok in clarification_tokens)
    
    # Step 0: Check Context
    ctx_mgr = get_context_manager()
    context = ctx_mgr.get_context()
    
    if context["state"] == ContextState.AWAITING_CLARIFICATION:
        print(f"[pipeline] Context active: AWAITING_CLARIFICATION")
        print(f"[pipeline] Previous transcript: '{context['last_transcript']}'")
    elif context["state"] == ContextState.AWAITING_CONFLICT_RESOLUTION:
        print(f"[pipeline] Context active: AWAITING_CONFLICT_RESOLUTION")
        print(f"[pipeline] Pending event: {context['pending_event'].get('name')}")
    
    # Step 1: Transcribe audio with Whisper
    print("\n[pipeline] Step 1: Transcribing audio with Whisper...")
    try:
        transcript = transcribe_audio_file(audio_path)
        if transcript is None:
            result.error = "Failed to transcribe audio"
            return result
            
        print(f"[pipeline] Raw Transcript: '{transcript}'")
        
        # Apply Context Logic
        if context["state"] == ContextState.AWAITING_CLARIFICATION:
            # Merge transcripts
            if _looks_like_clarification(transcript):
                original = context["last_transcript"]
                transcript = f"{original} {transcript}"
                print(f"[pipeline] Merged Transcript: '{transcript}'")
            else:
                print("[pipeline] New command detected; skipping clarification merge.")
            # Clear context either way so it doesn't linger
            ctx_mgr.reset()
            
        elif context["state"] == ContextState.AWAITING_CONFLICT_RESOLUTION:
            # Check for confirmation
            lower_trans = transcript.lower()
            confirmation_keywords = [
                "yes", "okay", "sure", "do it", "confirm", 
                "suggested", "suggestion", "recommendation", "that time", "great"
            ]
            if any(word in lower_trans for word in confirmation_keywords):
                # User confirmed the recommendation
                pending = context["pending_event"]
                rec = context["recommendation"]
                
                def _day_from_iso(dt_str: str) -> str:
                    """Return lowercase weekday name from ISO datetime string."""
                    try:
                        return datetime.fromisoformat(dt_str).strftime("%A").lower()
                    except Exception:
                        return "today"
                
                if rec:
                    # Apply recommendation
                    print(f"[pipeline] User confirmed recommendation. Updating start/end.")
                    pending["start"] = rec["start"]
                    pending["end"] = rec["end"]
                    day_name = _day_from_iso(rec["start"])
                    transcript = f"Add {pending['name']} on {day_name} at {datetime.fromisoformat(rec['start']).strftime('%I:%M %p')}"
                else:
                    # Just retry the original (might still conflict, but user insisted?)
                    # Or maybe we interpret "yes" as "force it"?
                    # For now, let's construct a new command that is explicit
                    day_name = _day_from_iso(pending["start"])
                    transcript = f"Add {pending['name']} on {day_name} at {datetime.fromisoformat(pending['start']).strftime('%I:%M %p')}"
                
                print(f"[pipeline] Constructed Transcript from Confirmation: '{transcript}'")
                ctx_mgr.reset()
            else:
                # User might be providing a new time: "No, make it 4pm"
                # We can try to append this to the original request minus the time?
                # Or just treat it as a new command. 
                # If they say "Make it 4pm", the intent classifier might handle it if we contextually merge?
                # Let's try merging with the event name: "Add [Event] [New Input]"
                pending = context["pending_event"]
                
                def _day_from_iso(dt_str: str) -> str:
                    try:
                        return datetime.fromisoformat(dt_str).strftime("%A").lower()
                    except Exception:
                        return "today"
                
                day_name = _day_from_iso(pending.get("start", datetime.now().isoformat()))
                transcript = f"Add {pending['name']} on {day_name} {transcript}"
                print(f"[pipeline] Merged Conflict Response: '{transcript}'")
                ctx_mgr.reset()

        result.transcript = transcript
        
        # Save transcript
        transcript_path = output_dir / "transcript.txt"
        with open(transcript_path, 'w') as f:
            f.write(transcript)
            
    except Exception as e:
        result.error = f"Transcription error: {str(e)}"
        print(f"[pipeline] Error: {result.error}")
        return result
    
    # Step 2: Classify intent
    print("\n[pipeline] Step 2: Classifying intent...")
    try:
        intent_result = classify_intent(transcript, client_datetime)
        result.intent = intent_result.intent.value
        result.intent_confidence = intent_result.confidence
        result.intent_parameters = intent_result.parameters
        print(f"[pipeline] Intent: {result.intent} (confidence: {result.intent_confidence})")
        
    except Exception as e:
        result.error = f"Intent classification error: {str(e)}"
        print(f"[pipeline] Error: {result.error}")
        return result
    
    # Step 3: Route to appropriate handler
    print(f"\n[pipeline] Step 3: Handling {result.intent} intent...")
    try:
        handler_result = route_intent(
            intent_result, 
            client_datetime, 
            output_dir
        )
        
        result.response_text = handler_result.get("response_text", "")
        result.changes_made = handler_result.get("changes_made", {})
        result.affected_days = handler_result.get("affected_days", [])
        result.calendar_debug = handler_result.get("calendar_debug", [])
        result.schedule_data = handler_result.get("schedule_data")
        result.agenda = handler_result.get("agenda")
        
        if handler_result.get("error"):
            result.error = handler_result["error"]
            
    except Exception as e:
        result.error = f"Handler error: {str(e)}"
        print(f"[pipeline] Error: {result.error}")
        import traceback
        traceback.print_exc()
        traceback.print_exc()
        return result
    
    # Step 3.5: Update Context based on result
    ctx_mgr = get_context_manager()
    
    if result.error == "Clarification needed":
        ctx_mgr.set_clarification_state(result.transcript)
    elif result.error == "Conflict detected":
        # We need to extract the pending event and recommendation from the handler result
        # The handler (handle_modify_schedule) returns these in the error response text? 
        # No, we need to pass them out.
        # Let's update handle_modify_schedule to return them in the result dict.
        
        # For now, we can't easily get the object back unless we modify the return signature.
        # See modification below in handle_modify_schedule
        
        # If we successfully extracted them:
        if result.schedule_data and "conflicts" in result.schedule_data:
             # Take the first conflict for context
             conflict = result.schedule_data["conflicts"][0]
             ctx_mgr.set_conflict_state(conflict["new_event"], conflict["recommendation"])
    else:
        # Success or other error -> Clear context
        ctx_mgr.reset()

    # Step 4: Generate TTS audio
    if generate_tts and is_tts_available() and result.response_text:
        print("\n[pipeline] Step 4: Synthesizing speech...")
        try:
            audio_output_path = str(output_dir / "response.wav")
            result.summary_audio_path = synthesize_speech(result.response_text, audio_output_path)
        except Exception as e:
            print(f"[pipeline] TTS warning: {str(e)}")
            # TTS failure is not critical
    else:
        print("\n[pipeline] Step 4: Skipping TTS")
    
    # Calculate processing time
    result.processing_time_ms = int((datetime.now() - start_time).total_seconds() * 1000)
    result.success = result.error is None
    
    print(f"\n{'='*60}")
    print(f"[pipeline] Processing complete!")
    print(f"[pipeline] Response: '{result.response_text[:100]}...' " if result.response_text and len(result.response_text) > 100 else f"[pipeline] Response: '{result.response_text}'")
    print(f"[pipeline] Total time: {result.processing_time_ms}ms")
    print(f"{'='*60}\n")
    
    # Save complete result to JSON
    try:
        result_json_path = output_dir / "result.json"
        with open(result_json_path, 'w') as f:
            json.dump(result.to_dict(), f, indent=2)
        print(f"[pipeline] Saved result to {result_json_path}")
    except Exception as e:
        print(f"[pipeline] Warning: Failed to save result JSON: {e}")

    return result


def route_intent(
    intent_result: IntentResult, 
    reference_datetime: datetime,
    output_dir: Path
) -> Dict[str, Any]:
    """
    Route the classified intent to the appropriate handler.
    
    Args:
        intent_result: Classified intent with parameters
        reference_datetime: Reference datetime for day resolution
        output_dir: Output directory for saving files
        
    Returns:
        Dictionary with handler results
    """
    intent = intent_result.intent
    params = intent_result.parameters
    
    if intent == Intent.MODIFY_SCHEDULE:
        return handle_modify_schedule(params, reference_datetime, output_dir)
    
    elif intent == Intent.QUERY_DAY:
        return handle_query_day(params, reference_datetime)
    
    elif intent == Intent.QUERY_WEEK:
        return handle_query_week()
    
    elif intent == Intent.CLEAR_DAY:
        return handle_clear_day(params, reference_datetime)
    
    elif intent == Intent.CLEAR_WEEK:
        return handle_clear_week()
    
    elif intent == Intent.HELP:
        return handle_help()
    
    elif intent == Intent.CLARIFICATION_NEEDED:
        return handle_clarification(params)
    
    else:  # UNKNOWN
        return {
            "response_text": "I'm sorry, I didn't understand that. Try saying things like 'Add a meeting on Monday at 2pm' or 'What's my schedule for tomorrow?'",
            "error": None
        }


# ==================== INTENT HANDLERS ====================

def handle_modify_schedule(
    params: Dict[str, Any], 
    reference_datetime: datetime,
    output_dir: Path
) -> Dict[str, Any]:
    """
    Handle MODIFY_SCHEDULE intent: add, edit, delete events.
    Automatically detects and resolves scheduling conflicts.
    Supports all-day and recurring events with calendar sync.
    """
    operations = params.get("operations", [])
    
    if not operations:
        return {
            "response_text": "I couldn't identify any schedule changes from your request. Please try again with specific events and times.",
            "error": "No operations found"
        }
    
    # Validate operations
    valid_ops, errors = validate_operations(operations)
    if errors:
        print(f"[pipeline] Validation warnings: {errors}")
    
    if not valid_ops:
        return {
            "response_text": "I couldn't understand the events you mentioned. Please include event names and times.",
            "error": "No valid operations"
        }
    
    manager = get_schedule_manager()
    
    # Check for week reset
    manager.check_and_reset_if_new_week()
    
    # Group operations by day
    ops_by_day = operations_to_scheduler_format(valid_ops, reference_datetime)
    
    # Track changes
    changes_made = {"added": [], "deleted": [], "modified": [], "rescheduled": []}
    affected_days = []
    calendar_debug = []
    
    # Process each affected day
    for day_name in DAYS_OF_WEEK:
        day_ops = ops_by_day.get(day_name, {"add": [], "edit": [], "delete": []})
        
        if not day_ops["add"] and not day_ops["edit"] and not day_ops["delete"]:
            continue
        
        affected_days.append(day_name)
        print(f"[pipeline] Processing {day_name}: +{len(day_ops['add'])} -{len(day_ops['delete'])} ~{len(day_ops['edit'])}")
        
        existing_schedule = manager.get_day_schedule(day_name)
        day_date = get_date_for_day(day_name, reference_datetime)
        
        optimized = merge_and_optimize_events(
            existing_events=existing_schedule.events,
            new_events=day_ops["add"],
            delete_names=day_ops["delete"],
            edit_events=day_ops["edit"],
            day_date=day_date
        )
        
        # If optimizer failed (e.g., no feasible slot), report back immediately
        if optimized.get("error"):
            return {
                "response_text": f"I couldn't fit everything into {day_name.capitalize()}. The scheduler reported: {optimized['error']}",
                "changes_made": {},
                "affected_days": [],
                "error": "Scheduling impossible"
            }
        
        # Check for conflicts
        conflicts = optimized.get("conflicts", [])
        if conflicts:
            conflict_responses = []
            for conflict in conflicts:
                new_evt = conflict["new_event"]
                exist_evt = conflict["existing_event"]
                exist_type = conflict["existing_type"]
                rec = conflict["recommendation"]
                new_name = new_evt.get("name", "event")
                exist_name = exist_evt.get("name", "event")
                try:
                    conflict_time = datetime.fromisoformat(exist_evt["start"]).strftime("%I:%M %p").lstrip("0")
                except:
                    conflict_time = "that time"
                msg = f"I couldn't add '{new_name}' because it conflicts with your {exist_type} event '{exist_name}' at {conflict_time}."
                if rec:
                    try:
                        rec_time = datetime.fromisoformat(rec["start"]).strftime("%I:%M %p").lstrip("0")
                        msg += f" I recommend moving it to {rec_time}."
                    except:
                        pass
                conflict_responses.append(msg)
            
            return {
                "response_text": " ".join(conflict_responses) + " Would you like to do that?",
                "changes_made": {},
                "affected_days": [],
                "schedule_data": {"conflicts": conflicts},
                "error": "Conflict detected"
            }
        
        # Save the updated schedule - MOVED TO AFTER SYNC
        updated_schedule = DaySchedule(day=day_name, events=optimized.get("events", []))
        # manager.save_day_schedule(updated_schedule)

        # === Calendar sync: create/update/delete events ===
        # We sync AFTER optimization to ensure we use the final scheduled times.
        # We must update the 'optimized' event objects with the new _calendar_id so it gets saved.
        
        optimized_events = optimized.get("events", [])
        
        # 1. Handle ADDs
        for new_event in day_ops["add"]:
            # Find the corresponding event in the optimized list
            # We match by name. (Assumption: names are unique enough for this batch)
            target_event = None
            for opt_event in optimized_events:
                if opt_event.get("name") == new_event.get("name"):
                    target_event = opt_event
                    break
            
            if target_event:
                title = target_event.get("name") or target_event.get("title") or "event"
                start_iso = target_event.get("start")
                end_iso = target_event.get("end")
                all_day = target_event.get("all_day", False)
                recurrence = target_event.get("recurrence")

                cal_res = create_event(
                    title=title,
                    start=start_iso,
                    end=end_iso,
                    all_day=all_day,
                    recurrence=recurrence
                )
                if cal_res.get("status") == "success":
                    created = cal_res["event"]
                    # Update the OPTIMIZED event object so it gets saved to disk
                    target_event["_calendar_id"] = created.get("id")
                    target_event["_calendar_htmlLink"] = created.get("htmlLink")
                    debug_msg = cal_res.get("debug_message", f"Synced new event '{title}' to Calendar")
                    calendar_debug.append(debug_msg)
                    print(f"[pipeline] {debug_msg}")
                else:
                    err_msg = cal_res.get("error", "Unknown error")
                    calendar_debug.append(f"Failed to create calendar event '{title}': {err_msg}")
                    print("[pipeline] Warning: failed to create calendar event:", err_msg)
            else:
                print(f"[pipeline] Warning: Could not find optimized event for '{new_event.get('name')}' to sync.")

        # 2. Handle EDITs
        for event in day_ops["edit"]:
            # For edits, the ID should be in the event object if it existed before.
            # We need to find the optimized version to get the new times.
            # We need to find the optimized version to get the new times AND the calendar ID.
            target_event = None
            for opt_event in optimized_events:
                # Match by name (case-insensitive)
                if opt_event.get("name", "").lower() == event.get("name", "").lower():
                    target_event = opt_event
                    break
            
            if not target_event:
                print(f"[pipeline] Warning: Could not find optimized event for edit '{event.get('name')}'")
                continue
            
            print(f"[pipeline] DEBUG: Found target_event for edit: {target_event.get('name')}")
            print(f"[pipeline] DEBUG: target_event keys: {list(target_event.keys())}")
            print(f"[pipeline] DEBUG: _calendar_id: {target_event.get('_calendar_id')}")
            
            if target_event and target_event.get("_calendar_id"):
                upd_res = update_event(
                    event_id=target_event["_calendar_id"],
                    title=target_event.get("name"),
                    start=target_event.get("start"),
                    end=target_event.get("end"),
                    all_day=target_event.get("all_day", False),
                    recurrence=target_event.get("recurrence")
                )
                debug_msg = upd_res.get("debug_message", f"Synced update for '{target_event.get('name')}'")
                calendar_debug.append(debug_msg)
                print(f"[pipeline] {debug_msg}")
            else:
                msg = f"No calendar ID found for '{target_event.get('name')}'. Attempting recovery..."
                print(f"[pipeline] {msg}")
                
                # Attempt to recover ID from Google Calendar
                found_evt = find_event_by_details(target_event.get("name"), target_event.get("start"))
                
                if found_evt and found_evt.get("id"):
                    print(f"[pipeline] Recovered event ID: {found_evt.get('id')}")
                    target_event["_calendar_id"] = found_evt.get("id")
                    target_event["_calendar_htmlLink"] = found_evt.get("htmlLink")
                    
                    # Now update it
                    upd_res = update_event(
                        event_id=target_event["_calendar_id"],
                        title=target_event.get("name"),
                        start=target_event.get("start"),
                        end=target_event.get("end"),
                        all_day=target_event.get("all_day", False),
                        recurrence=target_event.get("recurrence")
                    )
                    debug_msg = upd_res.get("debug_message", f"Synced update for '{target_event.get('name')}' (Recovered ID)")
                    calendar_debug.append(debug_msg)
                    print(f"[pipeline] {debug_msg}")
                else:
                    msg = f"Could not recover calendar ID for '{target_event.get('name')}'. Skipping sync."
                    calendar_debug.append(msg)
                    print(f"[pipeline] {msg}")

        # 3. Handle DELETEs
        # We need the OLD schedule to find the ID of the event we just deleted
        # Use existing_schedule which was loaded BEFORE the changes were saved
        
        old_events = existing_schedule.events
        for del_op in day_ops["delete"]:
            # del_op might be just a name or a dict with name
            del_name = del_op.get("name") if isinstance(del_op, dict) else str(del_op)
            
            # Find this event in the OLD schedule to get its ID
            target_old_event = None
            for old_evt in old_events:
                if old_evt.get("name", "").lower() == del_name.lower():
                    target_old_event = old_evt
                    break
            
            cal_id = target_old_event.get("_calendar_id") if target_old_event else None
            
            # If we found the event locally but it has no ID, try to recover it
            if target_old_event and not cal_id:
                print(f"[pipeline] No ID for deletion of '{del_name}'. Attempting recovery...")
                found_evt = find_event_by_details(del_name, target_old_event.get("start"))
                if found_evt:
                    cal_id = found_evt.get("id")
                    print(f"[pipeline] Recovered ID for deletion: {cal_id}")

            if cal_id:
                del_res = delete_event(event_id=cal_id)
                debug_msg = del_res.get("debug_message", f"Deleted calendar event ID {cal_id}")
                calendar_debug.append(debug_msg)
                print(f"[pipeline] {debug_msg}")
            else:
                msg = f"Skipping deletion for '{del_name}', no calendar ID found in previous schedule"
                calendar_debug.append(msg)
                print(f"[pipeline] {msg}")
        
        # Save the schedule AGAIN to persist the _calendar_ids we just added (if any from adds/edits)
        # (Although we already saved it above, this second save might be redundant unless we updated IDs in the objects...
        #  Actually, for ADDs/EDITs we updated 'target_event' which is a reference to an object in 'optimized['events']'.
        #  So the first save at line 474 saved the objects *before* they had IDs populated in the loop?
        #  Wait, line 474: updated_schedule = DaySchedule(..., events=optimized.get("events"))
        #  Then we iterate over optimized_events (which are the SAME objects) and update them.
        #  So we DO need to save again to persist the IDs.)
        manager.save_day_schedule(updated_schedule)

        # Track what changed
        for event in day_ops["add"]:
            changes_made["added"].append((day_name, event.get("name", "event")))
        for event_info in day_ops["delete"]:
            changes_made["deleted"].append((day_name, event_info.get("name", "event")))
        for event in day_ops["edit"]:
            changes_made["modified"].append((day_name, event.get("name", "event")))
        
        schedule_path = output_dir / f"schedule_{day_name}.json"
        with open(schedule_path, 'w') as f:
            json.dump(optimized, f, indent=2)
    
    response_text = generate_changes_summary(changes_made)
    today = get_day_from_datetime(reference_datetime)
    today_schedule = manager.get_day_schedule(today)
    agenda = generate_agenda_for_esp32({"events": today_schedule.events})
    
    return {
        "response_text": response_text,
        "changes_made": changes_made,
        "affected_days": affected_days,
        "calendar_debug": calendar_debug,
        "schedule_data": manager.get_week_summary_data(),
        "agenda": agenda,
        "error": None
    }



def handle_query_day(params: Dict[str, Any], reference_datetime: datetime) -> Dict[str, Any]:
    """
    Handle QUERY_DAY intent: get schedule for a specific day.
    """
    day_param = params.get("day", "today")
    day_name = normalize_day_name(day_param, reference_datetime)
    
    manager = get_schedule_manager()
    manager.check_and_reset_if_new_week()
    
    schedule = manager.get_day_schedule(day_name)
    
    # Check if this is today
    today = get_day_from_datetime(reference_datetime)
    is_today = (day_name == today)
    
    response_text = generate_query_response(day_name, schedule.events, is_today)
    agenda = generate_agenda_for_esp32({"events": schedule.events})
    
    return {
        "response_text": response_text,
        "schedule_data": {"day": day_name, "events": schedule.events},
        "agenda": agenda,
        "error": None
    }


def handle_query_week() -> Dict[str, Any]:
    """
    Handle QUERY_WEEK intent: get overview of entire week.
    """
    manager = get_schedule_manager()
    manager.check_and_reset_if_new_week()
    
    week_data = manager.get_week_summary_data()
    response_text = generate_week_summary(week_data["days"])
    
    return {
        "response_text": response_text,
        "schedule_data": week_data,
        "error": None
    }


def handle_clear_day(params: Dict[str, Any], reference_datetime: datetime) -> Dict[str, Any]:
    """
    Handle CLEAR_DAY intent: clear all events for a specific day.
    """
    day_param = params.get("day", "today")
    day_name = normalize_day_name(day_param, reference_datetime)
    
    manager = get_schedule_manager()
    
    # Get existing events BEFORE clearing to find IDs
    existing_schedule = manager.get_day_schedule(day_name)
    events_to_delete = existing_schedule.events
    
    had_events = manager.clear_day(day_name)
    
    calendar_debug = []
    
    # Sync deletions to Google Calendar
    if had_events:
        for evt in events_to_delete:
            cal_id = evt.get("_calendar_id")
            
            # ID Recovery
            if not cal_id:
                print(f"[pipeline] No ID for deletion of '{evt.get('name')}'. Attempting recovery...")
                found_evt = find_event_by_details(evt.get("name"), evt.get("start"))
                if found_evt:
                    cal_id = found_evt.get("id")
                    print(f"[pipeline] Recovered ID for deletion: {cal_id}")

            if cal_id:
                del_res = delete_event(event_id=cal_id)
                debug_msg = del_res.get("debug_message", f"Deleted calendar event ID {cal_id}")
                calendar_debug.append(debug_msg)
                print(f"[pipeline] {debug_msg}")
            else:
                msg = f"Skipping calendar deletion for '{evt.get('name')}', no ID found"
                calendar_debug.append(msg)
                print(f"[pipeline] {msg}")
    
    if had_events:
        response_text = generate_clear_confirmation("day", day_name)
    else:
        response_text = f"{day_name.capitalize()} was already clear."
    
    return {
        "response_text": response_text,
        "affected_days": [day_name],
        "changes_made": {"cleared_day": day_name},
        "calendar_debug": calendar_debug,
        "agenda": {"today": [], "next_item": None},
        "schedule_data": {"day": day_name, "events": []},
        "error": None
    }


def handle_clear_week() -> Dict[str, Any]:
    """
    Handle CLEAR_WEEK intent: clear entire week schedule.
    """
    manager = get_schedule_manager()
    
    # Get all events to delete
    week_schedule = manager.get_week_schedule()
    
    manager.clear_week()
    
    calendar_debug = []
    
    # Sync deletions
    for day, schedule in week_schedule.items():
        for evt in schedule.events:
            cal_id = evt.get("_calendar_id")
            
            # ID Recovery
            if not cal_id:
                print(f"[pipeline] No ID for deletion of '{evt.get('name')}'. Attempting recovery...")
                found_evt = find_event_by_details(evt.get("name"), evt.get("start"))
                if found_evt:
                    cal_id = found_evt.get("id")
                    print(f"[pipeline] Recovered ID for deletion: {cal_id}")

            if cal_id:
                del_res = delete_event(event_id=cal_id)
                debug_msg = del_res.get("debug_message", f"Deleted calendar event ID {cal_id}")
                calendar_debug.append(debug_msg)
                print(f"[pipeline] {debug_msg}")
            else:
                msg = f"Skipping calendar deletion for '{evt.get('name')}', no ID found"
                calendar_debug.append(msg)
                print(f"[pipeline] {msg}")
    
    response_text = generate_clear_confirmation("week")
    
    return {
        "response_text": response_text,
        "affected_days": DAYS_OF_WEEK.copy(),
        "changes_made": {"cleared_week": True},
        "calendar_debug": calendar_debug,
        "agenda": {"today": [], "next_item": None},
        "schedule_data": {"days": {}},
        "error": None
    }


def handle_help() -> Dict[str, Any]:
    """
    Handle HELP intent: provide usage information.
    """
    return {
        "response_text": get_help_response(),
        "error": None
    }


def handle_clarification(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle CLARIFICATION_NEEDED intent.
    """
    missing = params.get("missing_info", "details")
    
    # Generate a natural question
    if "time" in missing.lower() and "day" in missing.lower():
        response = "When would you like to schedule that?"
    elif "time" in missing.lower():
        response = "What time would you like to schedule that?"
    elif "day" in missing.lower():
        response = "Which day would you like to schedule that?"
    else:
        response = f"Could you please provide more details about the {missing}?"
        
    return {
        "response_text": response,
        "error": "Clarification needed"
    }


# ==================== LEGACY SUPPORT ====================

def process_transcript_only(
    transcript: str,
    client_datetime: datetime = None
) -> ProcessingResult:
    """
    Process a pre-transcribed text (skip Whisper step).
    Useful for testing or when transcript is already available.
    
    Args:
        transcript: The transcript text
        client_datetime: Reference datetime
        
    Returns:
        ProcessingResult with processing outputs
    """
    result = ProcessingResult()
    result.transcript = transcript
    start_time = datetime.now()
    
    if client_datetime is None:
        client_datetime = datetime.now()
    result.client_datetime = client_datetime.isoformat()
    
    print(f"\n[pipeline] Processing transcript (Whisper skipped)")
    print(f"[pipeline] Transcript: '{transcript}'")
    
    try:
        # Classify intent
        intent_result = classify_intent(transcript, client_datetime)
        result.intent = intent_result.intent.value
        result.intent_confidence = intent_result.confidence
        result.intent_parameters = intent_result.parameters
        
        # Create temp output dir
        import tempfile
        output_dir = Path(tempfile.mkdtemp())
        
        # Route to handler
        handler_result = route_intent(intent_result, client_datetime, output_dir)
        
        result.response_text = handler_result.get("response_text", "")
        result.changes_made = handler_result.get("changes_made", {})
        result.affected_days = handler_result.get("affected_days", [])
        result.calendar_debug = handler_result.get("calendar_debug", [])
        result.schedule_data = handler_result.get("schedule_data")
        result.agenda = handler_result.get("agenda")
        
        result.success = True
        result.processing_time_ms = int((datetime.now() - start_time).total_seconds() * 1000)
        
    except Exception as e:
        result.error = str(e)
        import traceback
        traceback.print_exc()
    
    return result
