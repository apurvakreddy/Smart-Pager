# modules/scheduler.py
"""
OR-Tools based schedule optimization.

Handles:
- Non-overlapping constraint scheduling
- Fixed events (immovable) and flexible tasks (movable within time window)
- Per-day schedule optimization
"""

from datetime import datetime, time, timedelta
from typing import Dict, Any, List, Optional
from ortools.sat.python import cp_model

# Helper functions for time parsing and conversions
def parse_iso(ts: str) -> datetime:
    """
    Parse an ISO-8601 timestamp into a naive datetime object in local time.

    We intentionally drop timezone info for the MVP, assuming all times are in
    the same local timezone for scheduling purposes.
    """
    dt = datetime.fromisoformat(ts)
    # Drop tzinfo so it becomes "naive" and compatible with datetime.combine
    return dt.replace(tzinfo=None)


def parse_hhmm(hhmm: str) -> time:
    """
    Parse 'HH:MM' strings like '08:00' into a time object.
    """
    hour, minute = map(int, hhmm.split(":"))
    return time(hour=hour, minute=minute)


def minutes_since_day_start(dt: datetime, day_start_dt: datetime) -> int:
    """
    Given a datetime dt and the datetime representing start of the scheduling day,
    return the number of minutes between them.
    """
    delta: timedelta = dt - day_start_dt
    return int(delta.total_seconds() // 60)


def add_minutes_to_day_start(day_start_dt: datetime, minutes: int) -> datetime:
    """
    Convert 'minutes since dayStart' back into a datetime object.
    """
    return day_start_dt + timedelta(minutes=minutes)

# Main function to build the scheduling model

def build_schedule_model(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Main entry point for turning the LLM JSON dictionary into a solvable OR-Tools model.

    Returns:
        {
            "model": cp_model.CpModel(),
            "intervals": List[Tuple[str, cp_model.IntervalVar]],
            "start_vars": Dict[str, cp_model.IntVar],
            "duration_map": Dict[str, int],
            "day_start_dt": datetime
        }
    """
    model = cp_model.CpModel()

    # ------------------------
    # 1. Extract basic fields
    # ------------------------
    rules = data["rules"]
    day_start_str = rules["dayStart"]      # e.g. "08:00"
    day_end_str   = rules["dayEnd"]        # e.g. "21:00"

    # Convert "08:00" → time object
    day_start_time = parse_hhmm(day_start_str)
    day_end_time   = parse_hhmm(day_end_str)

    # We need the YEAR-MONTH-DAY for the schedule.
    if "date" in data:
        # Use explicit date if provided
        schedule_date = parse_iso(data["date"]).date()
    elif data.get("events"):
        # Get it from the first fixed event (fallback)
        first_event_start_iso = data["events"][0]["start"]
        first_event_dt = parse_iso(first_event_start_iso)
        schedule_date = first_event_dt.date()
    else:
        # If we have tasks but no fixed events and no date, we can't determine the day.
        # However, for MVP we might assume tasks have earliestStart that implies the day?
        # Or just fail.
        if data.get("tasks") and data["tasks"][0].get("earliestStart"):
             first_task_start = data["tasks"][0]["earliestStart"]
             schedule_date = parse_iso(first_task_start).date()
        else:
             raise ValueError("Cannot determine schedule date: no 'date' provided and no fixed events.")

    # Build datetime for dayStart and dayEnd
    day_start_dt = datetime.combine(schedule_date, day_start_time)
    day_end_dt   = datetime.combine(schedule_date, day_end_time)

    day_start_minutes = 0
    day_end_minutes = int((day_end_dt - day_start_dt).total_seconds() // 60)
    # The scheduling horizon is [0, day_end_minutes].

    intervals = []           # (name, interval_var)
    start_vars = {}          # task_name -> start_var (flexible tasks only)
    duration_map = {}        # task_name -> duration in minutes
    fixed_starts = {}      # event_name -> fixed start minute

    # ------------------------------------------------------
    # 2. Handle FIXED events
    # ------------------------------------------------------
    for event in data.get("events", []):
        name = event["name"]
        start_dt = parse_iso(event["start"])
        end_dt   = parse_iso(event["end"])

        start_min = minutes_since_day_start(start_dt, day_start_dt)
        end_min   = minutes_since_day_start(end_dt, day_start_dt)
        duration  = end_min - start_min

        # Save duration for later result-building
        duration_map[name] = duration
        fixed_starts[name] = start_min

        # Fixed intervals use: NewFixedSizeIntervalVar
        interval = model.NewFixedSizeIntervalVar(start_min, duration, name)
        intervals.append((name, interval))

    # ------------------------------------------------------
    # 3. Handle FLEXIBLE tasks
    # ------------------------------------------------------
    for task in data.get("tasks", []):
        name = task["name"]
        duration = task["durationMinutes"]
        duration_map[name] = duration

        # These may be missing; fall back to full day window.
        earliest_iso = task.get("earliestStart")
        latest_iso   = task.get("latestEnd")

        if earliest_iso is not None:
            earliest_dt = parse_iso(earliest_iso)
        else:
            # If no earliestStart given, use the beginning of the scheduling day.
            earliest_dt = day_start_dt

        if latest_iso is not None:
            latest_dt = parse_iso(latest_iso)
        else:
            # If no latestEnd given, use the end of the scheduling day.
            latest_dt = day_end_dt

        earliest_min = minutes_since_day_start(earliest_dt, day_start_dt)
        latest_min   = minutes_since_day_start(latest_dt,  day_start_dt)

        # Domain for start: earliest_start ≤ start ≤ latest_end - duration
        start_lb = earliest_min
        start_ub = latest_min - duration

        if start_ub < start_lb:
            # Window too tight for this duration; for MVP we just raise.
            raise RuntimeError(f"Task '{name}' has an impossible time window.")

        start_var = model.NewIntVar(start_lb, start_ub, f"{name}_start")
        end_var   = model.NewIntVar(start_lb + duration, latest_min, f"{name}_end")

        model.Add(end_var == start_var + duration)

        interval = model.NewIntervalVar(start_var, duration, end_var, name)

        intervals.append((name, interval))
        start_vars[name] = start_var


    # Return everything needed for solving
    return {
        "model": model,
        "intervals": intervals,
        "start_vars": start_vars,
        "duration_map": duration_map,
        "fixed_starts": fixed_starts,
        "day_start_dt": day_start_dt,
        "day_end_minutes": day_end_minutes,
        "event_map": {**{e["name"]: e for e in data.get("events", [])}, 
                      **{t["name"]: t for t in data.get("tasks", [])}}
    }

# Solve the scheduling model, convert results back to datetime

def solve_schedule(model_info: Dict[str, Any]) -> Dict[str, Any]:
    """
    Given a constructed model_info dict from build_schedule_model,
    solve the schedule and return a new schedule dictionary with concrete start/end times.

    Output format:
    {
        "events": [
            {"name": "...", "start": "...", "end": "..."},
            ...
        ]
    }
    """

    model = model_info["model"]
    intervals = model_info["intervals"]
    start_vars = model_info["start_vars"]
    duration_map = model_info["duration_map"]
    fixed_starts = model_info["fixed_starts"]
    day_start_dt = model_info["day_start_dt"]

    # ----------------------------------------------------
    # 1. Add the NoOverlap constraint
    # ----------------------------------------------------
    interval_vars = [interval for (_, interval) in intervals]
    model.AddNoOverlap(interval_vars)

    # ----------------------------------------------------
    # 2. Solve the model
    # ----------------------------------------------------
    solver = cp_model.CpSolver()
    solver_status = solver.Solve(model)

    if solver_status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise RuntimeError("No feasible schedule could be found.")

    # ----------------------------------------------------
    # 3. Build the result dictionary
    # ----------------------------------------------------
    result_events = []

        # Handle fixed events: start/end times are known from build_schedule_model.
    for (name, interval) in intervals:
        if name not in start_vars:
            # This is a fixed event
            start_min = fixed_starts[name]
            duration = duration_map[name]
            end_min = start_min + duration

            start_dt = add_minutes_to_day_start(day_start_dt, start_min)
            end_dt   = add_minutes_to_day_start(day_start_dt, end_min)

            # Retrieve original event data to preserve extra fields (like _calendar_id)
            original_data = model_info.get("event_map", {}).get(name, {})
            
            event_out = original_data.copy()
            event_out.update({
                "name": name,
                "type": "fixed",
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
            })
            result_events.append(event_out)


    # Handle flexible tasks
    for name, start_var in start_vars.items():
        start_min = solver.Value(start_var)
        duration = duration_map[name]
        end_min = start_min + duration

        start_dt = add_minutes_to_day_start(day_start_dt, start_min)
        end_dt   = add_minutes_to_day_start(day_start_dt, end_min)

        # Retrieve original event data
        original_data = model_info.get("event_map", {}).get(name, {})
        
        task_out = original_data.copy()
        task_out.update({
            "name": name,
            "type": "flexible",
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
        })
        result_events.append(task_out)

    # Sort output by start time (nice readability)
    result_events.sort(key=lambda e: e["start"])

    return {"events": result_events}

# Final wrapper function that we'll call from main.py, which executes the full scheduling process. 

def schedule_day(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Full MVP scheduling pipeline.
    Input: dictionary from llm_interpreter (as returned by ChatGPT)
    Output: dictionary of fully scheduled events/tasks
    """
    # Handle empty schedules gracefully
    if not data.get("events") and not data.get("tasks"):
        return {"events": []}
    
    # 1. Build the model and interval structures
    model_info = build_schedule_model(data)

    # 2. Solve and return the scheduled events
    return solve_schedule(model_info)


# ==================== NEW FUNCTIONS FOR WEEKLY SCHEDULING ====================

def check_time_overlap(event1: Dict[str, Any], event2: Dict[str, Any]) -> bool:
    """
    Check if two events overlap in time.
    
    Args:
        event1, event2: Event dicts with 'start' and 'end' ISO timestamps
        
    Returns:
        True if events overlap, False otherwise
    """
    try:
        # Parse and strip timezone info to enforce naive local time comparison
        start1 = datetime.fromisoformat(event1["start"]).replace(tzinfo=None)
        end1 = datetime.fromisoformat(event1["end"]).replace(tzinfo=None)
        start2 = datetime.fromisoformat(event2["start"]).replace(tzinfo=None)
        end2 = datetime.fromisoformat(event2["end"]).replace(tzinfo=None)
        
        # Events overlap if one starts before the other ends
        # NOT overlapping: end1 <= start2 OR end2 <= start1
        # Overlapping: NOT (end1 <= start2 OR end2 <= start1)
        return not (end1 <= start2 or end2 <= start1)
    except (KeyError, ValueError):
        return False


def find_conflicts(new_event: Dict[str, Any], existing_events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Find all existing events that conflict with a new event.
    
    Args:
        new_event: The event being added
        existing_events: List of existing events
        
    Returns:
        List of conflicting events
    """
    conflicts = []
    for existing in existing_events:
        if check_time_overlap(new_event, existing):
            conflicts.append(existing)
    return conflicts


def detect_and_resolve_conflicts(
    existing_events: List[Dict[str, Any]],
    new_events: List[Dict[str, Any]],
    day_date: datetime
) -> tuple:
    """
    Detect conflicts between new events and existing events.
    Returns a list of conflict details instead of auto-resolving.
    
    Args:
        existing_events: Current events for the day
        new_events: Events being added
        day_date: The date of this day
        
    Returns:
        Tuple of (events_to_add, conflicts)
    """
    events_to_add = []
    conflicts = []
    
    # Track what we've tentatively added to check for self-conflicts
    tentative_schedule = list(existing_events)
    
    for new_event in new_events:
        # Flexible tasks are placed by the optimizer; don't preemptively treat them as blocking conflicts.
        if new_event.get("type") == "flexible":
            events_to_add.append(new_event)
            tentative_schedule.append(new_event)
            print(f"[scheduler] Queued flexible task '{new_event.get('name')}' for optimization")
            continue
        
        # Check conflicts with everything (existing + previously processed new events)
        current_conflicts = find_conflicts(new_event, tentative_schedule)
        
        if current_conflicts:
            # Found conflicts!
            resolved_conflicts = []
            for conflict_event in current_conflicts:
                # If the new event is fixed and the conflict is flexible, prefer keeping the fixed event
                # and re-queue the flexible one for optimization instead of blocking.
                if new_event.get("type") == "fixed" and conflict_event.get("type") == "flexible":
                    # Remove the flexible from tentative and re-queue it
                    try:
                        tentative_schedule.remove(conflict_event)
                        print(f"[scheduler] Rescheduling flexible task '{conflict_event.get('name')}' due to fixed '{new_event.get('name')}'")
                    except ValueError:
                        pass
                    events_to_add.append(conflict_event)
                    continue
                
                # Otherwise, record the conflict
                try:
                    conflict_end = datetime.fromisoformat(conflict_event["end"])
                    rec_start = conflict_end
                    
                    # Calculate duration of new event
                    start_dt = datetime.fromisoformat(new_event["start"])
                    end_dt = datetime.fromisoformat(new_event["end"])
                    duration = (end_dt - start_dt).total_seconds() / 60
                    
                    rec_end = rec_start + timedelta(minutes=duration)
                    
                    recommendation = {
                        "start": rec_start.isoformat(),
                        "end": rec_end.isoformat()
                    }
                except:
                    recommendation = None

                conflicts.append({
                    "new_event": new_event,
                    "existing_event": conflict_event,
                    "existing_type": conflict_event.get("type", "fixed"),
                    "recommendation": recommendation
                })
                resolved_conflicts.append(conflict_event)
                print(f"[scheduler] Conflict detected: '{new_event.get('name')}' vs '{conflict_event.get('name')}'")

            # If all conflicts were flexible and re-queued, allow the fixed event through
            if not conflicts:
                events_to_add.append(new_event)
                tentative_schedule.append(new_event)
        else:
            # No conflict, add to tentative schedule
            events_to_add.append(new_event)
            tentative_schedule.append(new_event)
    
    return events_to_add, conflicts


def optimize_day_events(
    events: List[Dict[str, Any]], 
    day_date: datetime,
    day_start: str = "08:00",
    day_end: str = "21:00"
) -> Dict[str, Any]:
    """
    Optimize a list of events for a single day.
    
    This is a simpler interface for the weekly scheduler that takes
    events already in the storage format and optimizes them.
    
    Args:
        events: List of event dicts with name, type, start, end
        day_date: The date of this day (for building the schedule)
        day_start: Day start time (HH:MM)
        day_end: Day end time (HH:MM)
        
    Returns:
        Dictionary with optimized 'events' list
    """
    if not events:
        return {"events": []}
    
    # Separate fixed and flexible events
    fixed_events = []
    flexible_tasks = []
    
    for event in events:
        event_type = event.get("type", "fixed")
        
        if event_type == "flexible":
            # Calculate duration from start/end
            # Use explicit durationMinutes if provided; fall back to window duration.
            duration = event.get("durationMinutes")
            if not duration:
                try:
                    start_dt = datetime.fromisoformat(event["start"])
                    end_dt = datetime.fromisoformat(event["end"])
                    duration = int((end_dt - start_dt).total_seconds() / 60)
                except (KeyError, ValueError):
                    duration = 60  # Default 1 hour
            
            # Preserve all fields from original event
            task = event.copy()
            task.update({
                "type": "flexible",
                "durationMinutes": duration,
                "earliestStart": event.get("earliestStart"),
                "latestEnd": event.get("latestEnd"),
            })
            flexible_tasks.append(task)
        else:
            # Preserve all fields
            fixed = event.copy()
            fixed.update({
                "type": "fixed",
                "start": event["start"],
                "end": event["end"]
            })
            fixed_events.append(fixed)
    
    # Check for conflicts among fixed events themselves
    # If fixed events conflict, convert later ones to flexible
    if len(fixed_events) > 1:
        resolved_fixed = []
        for i, event in enumerate(sorted(fixed_events, key=lambda e: e["start"])):
            conflicts = find_conflicts(event, resolved_fixed)
            if conflicts:
                # Convert to flexible
                try:
                    start_dt = datetime.fromisoformat(event["start"])
                    end_dt = datetime.fromisoformat(event["end"])
                    duration = int((end_dt - start_dt).total_seconds() / 60)
                except (KeyError, ValueError):
                    duration = 60
                
                flexible_tasks.append({
                    "name": event["name"],
                    "type": "flexible",
                    "durationMinutes": duration,
                })
                print(f"[scheduler] Fixed event '{event['name']}' conflicts - converting to flexible")
            else:
                resolved_fixed.append(event)
        fixed_events = resolved_fixed
    
    # If only fixed events and no conflicts, just return sorted
    if not flexible_tasks:
        return {"events": sorted(fixed_events, key=lambda e: e["start"])}
    
    # Build the data structure for schedule_day
    schedule_data = {
        "timeZone": "America/New_York",
        "rules": {
            "dayStart": day_start,
            "dayEnd": day_end
        },
        "events": fixed_events,
        "tasks": flexible_tasks,
        "date": day_date.isoformat(),
    }
    
    try:
        return schedule_day(schedule_data)
    except RuntimeError as e:
        print(f"[scheduler] Optimization failed: {e}")
        # Signal failure so caller can inform the user
        return {"events": events, "error": str(e)}


def merge_and_optimize_events(
    existing_events: List[Dict[str, Any]],
    new_events: List[Dict[str, Any]],
    delete_names: List[str],
    edit_events: List[Dict[str, Any]],
    day_date: datetime
) -> Dict[str, Any]:
    """
    Merge existing events with new operations and optimize.
    Automatically detects conflicts and reschedules conflicting events.
    
    Args:
        existing_events: Current events for the day
        new_events: Events to add
        delete_names: Names of events to delete (partial match)
        edit_events: Events to edit/update (matched by name)
        day_date: The date of this day
        
    Returns:
        Dictionary with:
        - 'events': optimized events list
        - 'rescheduled': list of events that were rescheduled due to conflicts
        - 'conflict_messages': human-readable conflict descriptions
    """
    # Start with existing events
    result_events = list(existing_events)
    rescheduled = []
    conflict_messages = []
    
    # Process deletions first
    for delete_item in delete_names:
        if isinstance(delete_item, dict):
            delete_name = delete_item.get("name", "")
        else:
            delete_name = str(delete_item)
            
        delete_lower = delete_name.lower()
        result_events = [
            e for e in result_events 
            if delete_lower not in e.get("name", "").lower()
        ]
        print(f"[scheduler] Deleted events matching '{delete_name}'")
    
    # Process edits (find and update)
    for edit_event in edit_events:
        edit_name_lower = edit_event.get("name", "").lower()
        found = False
        
        for i, existing in enumerate(result_events):
            if edit_name_lower in existing.get("name", "").lower():
                # Update the existing event with new values
                result_events[i] = {**existing, **edit_event}
                found = True
                print(f"[scheduler] Updated event '{existing.get('name')}'")
                break
        
        if not found:
            # If edit target not found, treat as add
            new_events.append(edit_event)
            print(f"[scheduler] Event '{edit_event.get('name')}' not found, will add as new")
    
    # Detect conflicts with new events
    if new_events:
        events_to_add, conflicts = detect_and_resolve_conflicts(
            result_events, new_events, day_date
        )
        
        if conflicts:
            # If there are conflicts, WE STOP HERE.
            # We do NOT add the conflicting events.
            # We return the conflicts so the user can be notified.
            
            # Add the non-conflicting ones? 
            # Policy: If ANY conflict exists in the batch, we probably shouldn't apply ANY changes 
            # to avoid partial state, OR we apply the safe ones.
            # Let's apply the safe ones but return the conflicts.
            
            for event in events_to_add:
                result_events.append(event)
                print(f"[scheduler] Added non-conflicting event '{event.get('name')}'")
            
            # Optimize what we have so far
            optimized = optimize_day_events(result_events, day_date)
            if optimized.get("error"):
                optimized["conflicts"] = conflicts
                return optimized
            
            # Add conflict info
            optimized["conflicts"] = conflicts
            return optimized
            
        else:
            # No conflicts, add everything
            for event in events_to_add:
                result_events.append(event)
                print(f"[scheduler] Added new event '{event.get('name')}'")
    
    # Optimize the merged schedule
    optimized = optimize_day_events(result_events, day_date)
    if optimized.get("error"):
        return optimized
    
    return optimized


def validate_events_for_day(events: List[Dict[str, Any]], day_date: datetime) -> List[str]:
    """
    Validate events for a specific day.
    
    Args:
        events: List of events to validate
        day_date: The expected date for these events
        
    Returns:
        List of validation error messages (empty if valid)
    """
    errors = []
    
    for i, event in enumerate(events):
        name = event.get("name", f"Event {i+1}")
        
        # Check required fields
        if not event.get("name"):
            errors.append(f"Event {i+1}: Missing name")
        
        if not event.get("start"):
            errors.append(f"{name}: Missing start time")
            continue
        
        if not event.get("end"):
            errors.append(f"{name}: Missing end time")
            continue
        
        # Validate times
        try:
            start_dt = datetime.fromisoformat(event["start"])
            end_dt = datetime.fromisoformat(event["end"])
            
            if end_dt <= start_dt:
                errors.append(f"{name}: End time must be after start time")
            
            # Check if event is on the expected day
            if start_dt.date() != day_date.date():
                errors.append(f"{name}: Event date doesn't match expected day")
                
        except ValueError as e:
            errors.append(f"{name}: Invalid time format - {e}")
    
    return errors
