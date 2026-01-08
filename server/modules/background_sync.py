# smartPager/server/modules/background_sync.py
# Run this in a separate process

import os
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
import schedule

from .schedule_manager import get_schedule_manager, DaySchedule, get_day_from_datetime
from .calendar_utils import fetch_events
from .summary_generator import generate_agenda_for_esp32


def merge_external_events(events):
    """
    Merge external calendar events into SmartPager's schedule.
    Supports all-day and recurring events.
    
    events: list of dicts with keys ['name', 'start', 'end', 'all_day', 'recurrence', '_calendar_id']
    """
    manager = get_schedule_manager()
    today_name = datetime.now().strftime("%A").lower()

    # Filter events for today (or recurring events)
    day_events = []
    for event in events:
        # Determine event date(s)
        try:
            event_start_dt = datetime.fromisoformat(event["start"])
        except Exception:
            continue

        # Recurring events (basic daily/weekly)
        recurrences = event.get("recurrence", [])
        if recurrences:
            # For now, handle simple daily or weekly recurrence
            # TODO: extend with full RRULE parsing
            day_events.append(event)
        elif event_start_dt.strftime("%A").lower() == today_name:
            day_events.append(event)

    # Load current schedule for today
    existing_schedule = manager.get_day_schedule(today_name)

    # Merge: add if name/start not already present
    merged_events = existing_schedule.events.copy()
    for evt in day_events:
        if not any(
            e["name"] == evt["name"] and e["start"] == evt["start"]
            for e in merged_events
        ):
            merged_events.append(evt)

    # Save updated schedule
    manager.save_day_schedule(DaySchedule(day=today_name, events=merged_events))
    return merged_events


def update_from_calendar():
    """
    Fetch events from external calendar and merge into SmartPager schedule.
    """
    now = datetime.now()
    print(f"[background_sync] Starting calendar sync at {now.isoformat()}")
    try:
        events = fetch_events()  # returns list of dicts with start/end/name/all_day/recurrence
        merged_events = merge_external_events(events)
        print(f"[background_sync] Merged {len(merged_events)} events into today's schedule")

        # Generate agenda for ESP32
        agenda = generate_agenda_for_esp32({"events": merged_events})
        output_dir = Path("output")
        output_dir.mkdir(parents=True, exist_ok=True)
        with open(output_dir / "agenda.json", "w") as f:
            json.dump(agenda, f, indent=2)

    except Exception as e:
        print(f"[background_sync] Error during calendar sync: {str(e)}")


def run_hourly_sync():
    """
    Schedule the background sync to run every hour.
    """
    print("[background_sync] Scheduling hourly calendar sync...")
    schedule.every(1).hours.do(update_from_calendar)

    # Run initial sync immediately
    update_from_calendar()

    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    run_hourly_sync()
