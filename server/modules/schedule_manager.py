# smartPager/server/modules/schedule_manager.py
"""
Persistent weekly schedule storage manager.

Handles:
- Reading/writing day schedules (Monday-Sunday)
- Week metadata (start date, last reset)
- Automatic week reset detection
- CRUD operations for events
"""

import os
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict

# Days of the week in order (Monday = 0)
DAYS_OF_WEEK = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

# Default schedule directory (relative to server/)
DEFAULT_SCHEDULE_DIR = "schedule"


@dataclass
class DaySchedule:
    """Represents a single day's schedule"""
    day: str  # e.g., "monday"
    events: List[Dict[str, Any]]  # List of scheduled events
    last_updated: Optional[str] = None  # ISO timestamp
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DaySchedule":
        return cls(
            day=data.get("day", ""),
            events=data.get("events", []),
            last_updated=data.get("last_updated")
        )
    
    @classmethod
    def empty(cls, day: str) -> "DaySchedule":
        return cls(day=day, events=[], last_updated=None)


@dataclass
class WeekMetadata:
    """Metadata about the current week"""
    week_start_date: str  # ISO date of Monday of current week
    last_reset: Optional[str] = None  # ISO timestamp of last reset
    last_modified: Optional[str] = None  # ISO timestamp of last modification
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WeekMetadata":
        return cls(
            week_start_date=data.get("week_start_date", ""),
            last_reset=data.get("last_reset"),
            last_modified=data.get("last_modified")
        )


class ScheduleManager:
    """
    Manages persistent weekly schedule storage.
    
    Directory structure:
        schedule/
        ├── monday/
        │   └── schedule.json
        ├── tuesday/
        │   └── schedule.json
        ├── ...
        ├── sunday/
        │   └── schedule.json
        └── week_meta.json
    """
    
    def __init__(self, base_dir: str = None):
        """
        Initialize the schedule manager.
        
        Args:
            base_dir: Base directory for schedule storage. 
                     Defaults to 'schedule/' relative to server directory.
        """
        if base_dir is None:
            # Get the directory where this module is located
            module_dir = Path(__file__).parent.parent
            base_dir = module_dir / DEFAULT_SCHEDULE_DIR
        
        self.base_dir = Path(base_dir)
        self._ensure_structure()
    
    def _ensure_structure(self):
        """Create directory structure if it doesn't exist"""
        self.base_dir.mkdir(parents=True, exist_ok=True)
        
        for day in DAYS_OF_WEEK:
            day_dir = self.base_dir / day
            day_dir.mkdir(exist_ok=True)
        
        # Create week_meta.json if it doesn't exist
        meta_path = self.base_dir / "week_meta.json"
        if not meta_path.exists():
            self._reset_week_metadata()
    
    def _get_current_week_start(self) -> str:
        """Get the Monday of the current week as ISO date string"""
        today = datetime.now().date()
        # Monday is weekday 0
        days_since_monday = today.weekday()
        monday = today - timedelta(days=days_since_monday)
        return monday.isoformat()
    
    def _reset_week_metadata(self):
        """Reset week metadata to current week"""
        now = datetime.now().isoformat()
        meta = WeekMetadata(
            week_start_date=self._get_current_week_start(),
            last_reset=now,
            last_modified=now
        )
        meta_path = self.base_dir / "week_meta.json"
        with open(meta_path, 'w') as f:
            json.dump(meta.to_dict(), f, indent=2)
    
    def get_week_metadata(self) -> WeekMetadata:
        """Load week metadata"""
        meta_path = self.base_dir / "week_meta.json"
        try:
            with open(meta_path, 'r') as f:
                return WeekMetadata.from_dict(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            self._reset_week_metadata()
            return self.get_week_metadata()
    
    def _update_week_metadata(self):
        """Update the last_modified timestamp"""
        meta = self.get_week_metadata()
        meta.last_modified = datetime.now().isoformat()
        meta_path = self.base_dir / "week_meta.json"
        with open(meta_path, 'w') as f:
            json.dump(meta.to_dict(), f, indent=2)
    
    def check_and_reset_if_new_week(self) -> bool:
        """
        Check if we're in a new week and reset if so.
        
        Returns:
            True if week was reset, False otherwise
        """
        meta = self.get_week_metadata()
        current_week_start = self._get_current_week_start()
        
        if meta.week_start_date != current_week_start:
            print(f"[schedule_manager] New week detected. Resetting schedule.")
            self.clear_week()
            return True
        return False
    
    # ==================== DAY OPERATIONS ====================
    
    def get_day_schedule(self, day: str) -> DaySchedule:
        """
        Load schedule for a specific day.
        
        Args:
            day: Day name (e.g., "monday", "tuesday")
            
        Returns:
            DaySchedule object
        """
        day = day.lower()
        if day not in DAYS_OF_WEEK:
            raise ValueError(f"Invalid day: {day}. Must be one of {DAYS_OF_WEEK}")
        
        schedule_path = self.base_dir / day / "schedule.json"
        
        try:
            with open(schedule_path, 'r') as f:
                data = json.load(f)
                return DaySchedule.from_dict(data)
        except (FileNotFoundError, json.JSONDecodeError):
            return DaySchedule.empty(day)
    
    def save_day_schedule(self, schedule: DaySchedule):
        """
        Save schedule for a specific day.
        
        Args:
            schedule: DaySchedule object to save
        """
        day = schedule.day.lower()
        if day not in DAYS_OF_WEEK:
            raise ValueError(f"Invalid day: {day}")
        
        schedule.last_updated = datetime.now().isoformat()
        schedule_path = self.base_dir / day / "schedule.json"
        
        with open(schedule_path, 'w') as f:
            json.dump(schedule.to_dict(), f, indent=2)
        
        self._update_week_metadata()
        print(f"[schedule_manager] Saved {day} schedule with {len(schedule.events)} events")
    
    def clear_day(self, day: str) -> bool:
        """
        Clear all events for a specific day.
        
        Args:
            day: Day name
            
        Returns:
            True if cleared, False if already empty
        """
        day = day.lower()
        existing = self.get_day_schedule(day)
        had_events = len(existing.events) > 0
        
        empty_schedule = DaySchedule.empty(day)
        self.save_day_schedule(empty_schedule)
        
        print(f"[schedule_manager] Cleared {day} schedule")
        return had_events
    
    # ==================== WEEK OPERATIONS ====================
    
    def get_week_schedule(self) -> Dict[str, DaySchedule]:
        """
        Load schedules for all days of the week.
        
        Returns:
            Dictionary mapping day names to DaySchedule objects
        """
        week = {}
        for day in DAYS_OF_WEEK:
            week[day] = self.get_day_schedule(day)
        return week
    
    def clear_week(self):
        """Clear all schedules for the entire week"""
        for day in DAYS_OF_WEEK:
            schedule_path = self.base_dir / day / "schedule.json"
            empty_schedule = DaySchedule.empty(day)
            with open(schedule_path, 'w') as f:
                json.dump(empty_schedule.to_dict(), f, indent=2)
        
        self._reset_week_metadata()
        print(f"[schedule_manager] Cleared entire week schedule")
    
    def get_week_summary_data(self) -> Dict[str, Any]:
        """
        Get a summary of the entire week for display/API.
        
        Returns:
            Dictionary with week overview
        """
        week = self.get_week_schedule()
        meta = self.get_week_metadata()
        
        summary = {
            "week_start": meta.week_start_date,
            "last_modified": meta.last_modified,
            "days": {}
        }
        
        total_events = 0
        for day, schedule in week.items():
            event_count = len(schedule.events)
            total_events += event_count
            summary["days"][day] = {
                "event_count": event_count,
                "events": schedule.events,
                "last_updated": schedule.last_updated
            }
        
        summary["total_events"] = total_events
        return summary
    
    # ==================== EVENT CRUD OPERATIONS ====================
    
    def add_event_to_day(self, day: str, event: Dict[str, Any]) -> DaySchedule:
        """
        Add an event to a specific day's schedule.
        
        Args:
            day: Day name
            event: Event dictionary with name, start, end, etc.
            
        Returns:
            Updated DaySchedule
        """
        schedule = self.get_day_schedule(day)
        schedule.events.append(event)
        self.save_day_schedule(schedule)
        return schedule
    
    def remove_event_from_day(self, day: str, event_name: str) -> Optional[Dict[str, Any]]:
        """
        Remove an event from a specific day by name.
        
        Args:
            day: Day name
            event_name: Name of event to remove (case-insensitive partial match)
            
        Returns:
            Removed event if found, None otherwise
        """
        schedule = self.get_day_schedule(day)
        event_name_lower = event_name.lower()
        
        removed_event = None
        new_events = []
        
        for event in schedule.events:
            if event_name_lower in event.get("name", "").lower():
                removed_event = event
            else:
                new_events.append(event)
        
        if removed_event:
            schedule.events = new_events
            self.save_day_schedule(schedule)
            print(f"[schedule_manager] Removed '{removed_event.get('name')}' from {day}")
        
        return removed_event
    
    def modify_event_in_day(self, day: str, event_name: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Modify an existing event in a day's schedule.
        
        Args:
            day: Day name
            event_name: Name of event to modify (case-insensitive partial match)
            updates: Dictionary of fields to update
            
        Returns:
            Updated event if found, None otherwise
        """
        schedule = self.get_day_schedule(day)
        event_name_lower = event_name.lower()
        
        modified_event = None
        for event in schedule.events:
            if event_name_lower in event.get("name", "").lower():
                event.update(updates)
                modified_event = event
                break
        
        if modified_event:
            self.save_day_schedule(schedule)
            print(f"[schedule_manager] Modified '{modified_event.get('name')}' in {day}")
        
        return modified_event
    
    def find_event_by_name(self, event_name: str) -> List[tuple]:
        """
        Search for an event by name across all days.
        
        Args:
            event_name: Name to search for (case-insensitive partial match)
            
        Returns:
            List of (day, event) tuples where event was found
        """
        results = []
        event_name_lower = event_name.lower()
        
        for day in DAYS_OF_WEEK:
            schedule = self.get_day_schedule(day)
            for event in schedule.events:
                if event_name_lower in event.get("name", "").lower():
                    results.append((day, event))
        
        return results


# ==================== UTILITY FUNCTIONS ====================

def get_day_from_datetime(dt: datetime) -> str:
    """
    Get the day name from a datetime object.
    
    Args:
        dt: datetime object
        
    Returns:
        Day name (e.g., "monday")
    """
    return DAYS_OF_WEEK[dt.weekday()]


def parse_relative_day(relative: str, reference_date: datetime) -> str:
    """
    Parse relative day references like "today", "tomorrow", "next monday".
    
    Args:
        relative: Relative day string
        reference_date: Reference datetime (usually current time from client)
        
    Returns:
        Day name (e.g., "monday")
    """
    relative = relative.lower().strip()
    
    if relative == "today":
        return get_day_from_datetime(reference_date)
    
    if relative == "tomorrow":
        tomorrow = reference_date + timedelta(days=1)
        return get_day_from_datetime(tomorrow)
    
    if relative == "yesterday":
        yesterday = reference_date - timedelta(days=1)
        return get_day_from_datetime(yesterday)
    
    # Check for day names
    for i, day in enumerate(DAYS_OF_WEEK):
        if day in relative:
            return day
    
    # Check for "in X days"
    if "in" in relative and "day" in relative:
        import re
        match = re.search(r'in\s+(\d+)\s+day', relative)
        if match:
            days_ahead = int(match.group(1))
            future = reference_date + timedelta(days=days_ahead)
            return get_day_from_datetime(future)
    
    # Default to today
    return get_day_from_datetime(reference_date)


def normalize_day_name(day_input: str, reference_date: datetime = None) -> str:
    """
    Normalize any day reference to a standard day name.
    
    Args:
        day_input: Day name or relative reference
        reference_date: Reference datetime for relative references
        
    Returns:
        Normalized day name (e.g., "monday")
    """
    if reference_date is None:
        reference_date = datetime.now()
    
    day_lower = day_input.lower().strip()
    
    # Check if it's already a valid day name
    if day_lower in DAYS_OF_WEEK:
        return day_lower
    
    # Try to parse as relative
    return parse_relative_day(day_lower, reference_date)


# Global instance for convenience
_manager_instance: Optional[ScheduleManager] = None

def get_schedule_manager() -> ScheduleManager:
    """Get or create the global ScheduleManager instance"""
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = ScheduleManager()
    return _manager_instance
