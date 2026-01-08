import os
import json
import logging
import datetime
from typing import List, Optional, Dict, Any
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from dateutil import parser as date_parser
import pytz

# Configure logging
LOGGER = logging.getLogger("simple_calendar")
LOGGER.setLevel(logging.INFO)

# Constants
SCOPES = ['https://www.googleapis.com/auth/calendar']
TIMEZONE = os.getenv("TIMEZONE", "America/New_York")
MODULE_DIR = Path(__file__).parent
TOKEN_PATH = MODULE_DIR / "token.json"
TEMP_CREDS_PATH = MODULE_DIR / "temp_credentials.json"

def _get_credentials():
    """
    Obtains valid user credentials from storage.
    If no valid token exists, initiates the OAuth flow using env vars.
    """
    creds = None
    
    # 1. Load existing token
    if TOKEN_PATH.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
        except Exception as e:
            LOGGER.warning(f"Failed to load token: {e}")
            creds = None

    # 2. Refresh if expired
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            # Save refreshed token
            with open(TOKEN_PATH, 'w') as f:
                f.write(creds.to_json())
        except Exception as e:
            LOGGER.warning(f"Failed to refresh token: {e}")
            creds = None

    # 3. New Login if needed
    if not creds or not creds.valid:
        LOGGER.info("No valid token found. Initiating login flow...")
        
        # Check for Env Vars
        client_id = os.environ.get("GOOGLE_CLIENT_ID")
        client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
        project_id = os.environ.get("GOOGLE_PROJECT_ID")
        
        if not client_id or not client_secret:
            raise RuntimeError("Missing GOOGLE_CLIENT_ID or GOOGLE_CLIENT_SECRET env vars.")
            
        # Create temp credentials file for InstalledAppFlow
        creds_data = {
            "installed": {
                "client_id": client_id,
                "project_id": project_id or "smartpager",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "client_secret": client_secret,
                "redirect_uris": ["http://localhost"]
            }
        }
        
        try:
            with open(TEMP_CREDS_PATH, "w") as f:
                json.dump(creds_data, f)
                
            flow = InstalledAppFlow.from_client_secrets_file(str(TEMP_CREDS_PATH), SCOPES)
            creds = flow.run_local_server(port=0)
            
            # Save new token
            with open(TOKEN_PATH, 'w') as f:
                f.write(creds.to_json())
                
        finally:
            # Cleanup temp file
            if TEMP_CREDS_PATH.exists():
                os.remove(TEMP_CREDS_PATH)
                
    return creds

def get_service():
    """Returns an authenticated Google Calendar service."""
    creds = _get_credentials()
    return build('calendar', 'v3', credentials=creds, cache_discovery=False)

def _ensure_rfc3339(dt_str: str) -> str:
    """Ensures datetime string is RFC3339 formatted with timezone."""
    try:
        dt = date_parser.parse(dt_str)
        if dt.tzinfo is None:
            tz = pytz.timezone(TIMEZONE)
            dt = tz.localize(dt)
        return dt.isoformat()
    except Exception as e:
        LOGGER.error(f"Date parsing error for {dt_str}: {e}")
        return dt_str

def _normalize_event(evt: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize Google Calendar event for internal use."""
    start = evt["start"].get("dateTime") or evt["start"].get("date")
    end = evt["end"].get("dateTime") or evt["end"].get("date")
    
    # Handle all-day events (date only) -> convert to ISO for consistency if needed
    if "date" in evt["start"]:
        start_dt = date_parser.parse(start)
        end_dt = date_parser.parse(end) - datetime.timedelta(seconds=1)
        start = start_dt.isoformat()
        end = end_dt.isoformat()

    return {
        "name": evt.get("summary", "event"),
        "start": start,
        "end": end,
        "id": evt.get("id"),
        "htmlLink": evt.get("htmlLink"),
        "description": evt.get("description"),
        "location": evt.get("location")
    }

# ================= PUBLIC API =================

def create_event(
    title: str,
    start: str,
    end: str,
    description: Optional[str] = None,
    location: Optional[str] = None,
    all_day: bool = False,
    recurrence: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Create a new event in the primary calendar.
    Returns: {"status": "success", "event": {...}} or {"status": "error", "error": ...}
    """
    try:
        service = get_service()
        
        event_body = {
            "summary": title,
            "description": description,
            "location": location
        }
        
        if all_day:
            event_body["start"] = {"date": date_parser.parse(start).date().isoformat()}
            event_body["end"] = {"date": date_parser.parse(end).date().isoformat()}
        else:
            event_body["start"] = {"dateTime": _ensure_rfc3339(start)}
            event_body["end"] = {"dateTime": _ensure_rfc3339(end)}
            
        if recurrence:
            event_body["recurrence"] = recurrence
            
        created_event = service.events().insert(calendarId='primary', body=event_body).execute()
        LOGGER.info(f"Created event: {created_event.get('id')}")
        
        return {
            "status": "success", 
            "event": _normalize_event(created_event),
            "debug_message": f"Created Google Calendar event '{title}' (ID: {created_event.get('id')})"
        }
        
    except Exception as e:
        LOGGER.error(f"Error creating event: {e}")
        return {"status": "error", "error": str(e), "debug_message": f"Failed to create event: {str(e)}"}

def update_event(
    event_id: str,
    title: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    description: Optional[str] = None,
    all_day: bool = False,
    recurrence: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Update an existing event by ID.
    """
    try:
        service = get_service()
        
        patch_body = {}
        if title: patch_body["summary"] = title
        if description: patch_body["description"] = description
        if recurrence: patch_body["recurrence"] = recurrence
        
        if start or end:
            patch_body["start"] = {}
            patch_body["end"] = {}
            
            if all_day:
                if start: patch_body["start"]["date"] = date_parser.parse(start).date().isoformat()
                if end: patch_body["end"]["date"] = date_parser.parse(end).date().isoformat()
            else:
                if start: patch_body["start"]["dateTime"] = _ensure_rfc3339(start)
                if end: patch_body["end"]["dateTime"] = _ensure_rfc3339(end)
                
        updated_event = service.events().patch(calendarId='primary', eventId=event_id, body=patch_body).execute()
        LOGGER.info(f"Updated event: {event_id}")
        
        return {
            "status": "success", 
            "event": _normalize_event(updated_event),
            "debug_message": f"Updated Google Calendar event (ID: {event_id})"
        }
        
    except Exception as e:
        LOGGER.error(f"Error updating event {event_id}: {e}")
        return {"status": "error", "error": str(e), "debug_message": f"Failed to update event {event_id}: {str(e)}"}

def delete_event(event_id: str) -> Dict[str, Any]:
    """
    Delete an event by ID.
    """
    try:
        service = get_service()
        service.events().delete(calendarId='primary', eventId=event_id).execute()
        LOGGER.info(f"Deleted event: {event_id}")
        return {
            "status": "success",
            "debug_message": f"Deleted Google Calendar event (ID: {event_id})"
        }
    except Exception as e:
        LOGGER.error(f"Error deleting event {event_id}: {e}")
        return {"status": "error", "error": str(e), "debug_message": f"Failed to delete event {event_id}: {str(e)}"}

def fetch_events(lookahead_days: int = 7) -> List[Dict[str, Any]]:
    """
    Fetch upcoming events for the next N days.
    """
    try:
        service = get_service()
        
        tz = pytz.timezone(TIMEZONE)
        now = datetime.datetime.now(tz).isoformat()
        end_time = (datetime.datetime.now(tz) + datetime.timedelta(days=lookahead_days)).isoformat()
        
        events_result = service.events().list(
            calendarId='primary',
            timeMin=now,
            timeMax=end_time,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        items = events_result.get('items', [])
        return [_normalize_event(evt) for evt in items]
        
    except Exception as e:
        LOGGER.error(f"Error fetching events: {e}")
        return []

def find_event_by_details(title: str, start_dt: str) -> Optional[Dict[str, Any]]:
    """
    Try to find an event by title and approximate start time.
    Useful for recovering lost IDs.
    """
    try:
        service = get_service()
        
        # Search window: +/- 2 hours around start time
        start_obj = date_parser.parse(start_dt)
        if start_obj.tzinfo is None:
            tz = pytz.timezone(TIMEZONE)
            start_obj = tz.localize(start_obj)
            
        time_min = (start_obj - datetime.timedelta(hours=2)).isoformat()
        time_max = (start_obj + datetime.timedelta(hours=2)).isoformat()
        
        events_result = service.events().list(
            calendarId='primary',
            timeMin=time_min,
            timeMax=time_max,
            q=title, # Full text search
            singleEvents=True
        ).execute()
        
        items = events_result.get('items', [])
        
        # Filter by exact title match (case insensitive)
        for item in items:
            if item.get('summary', '').lower() == title.lower():
                return _normalize_event(item)
                
        return None
        
    except Exception as e:
        LOGGER.error(f"Error finding event: {e}")
        return None
