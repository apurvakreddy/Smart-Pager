
import sys
import os
import time
import requests
import json
from datetime import datetime

# Add server directory to path for imports
sys.path.append(os.path.abspath("."))

# ANSI Colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

SERVER_URL = "http://localhost:8000"

def print_step(msg):
    print(f"\n{YELLOW}=== {msg} ==={RESET}")

def print_success(msg):
    print(f"{GREEN}✅ {msg}{RESET}")

def print_fail(msg):
    print(f"{RED}❌ {msg}{RESET}")

def check_auth():
    print_step("Step 1: Checking Google Calendar Authentication")
    
    cred_path = "credentials.json"
    token_path = "token.json"
    
    if os.path.exists(cred_path):
        print_success(f"Found credentials.json at {cred_path}")
    else:
        print_fail(f"Missing credentials.json at {cred_path}")
        return False

    if os.path.exists(token_path):
        print_success(f"Found token.json at {token_path}")
    else:
        print(f"{YELLOW}⚠️ token.json not found. Authentication flow might trigger on first run.{RESET}")
    
    return True

# Set environment variables for calendar_client BEFORE importing it
# This ensures it looks for credentials in the server root, not modules/
SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
os.environ["CREDENTIALS_PATH"] = os.path.join(SERVER_DIR, "credentials.json")
os.environ["TOKEN_DIR"] = os.path.join(SERVER_DIR, "token_store")

def test_direct_gcal():
    print_step("Step 2: Testing Direct Google Calendar Access")
    
    try:
        from modules.calendar_client import get_default_service
        
        print("Attempting to build GCal service...")
        service = get_default_service()
        
        print("Listing next 3 events...")
        now = datetime.utcnow().isoformat() + 'Z'
        events_result = service.events().list(
            calendarId='primary', timeMin=now,
            maxResults=3, singleEvents=True,
            orderBy='startTime').execute()
        events = events_result.get('items', [])
        
        print_success(f"Successfully connected! Found {len(events)} upcoming events.")
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            print(f"  - {event['summary']} ({start})")
            
        return True
    except Exception as e:
        print_fail(f"Direct GCal access failed: {e}")
        return False

def send_pipeline_command(transcript, description):
    print(f"\n👉 Sending command: '{transcript}'")
    payload = {
        "transcript": transcript,
        "client_datetime": datetime.now().isoformat()
    }
    
    try:
        response = requests.post(f"{SERVER_URL}/api/process_transcript", json=payload)
        if response.status_code == 200:
            data = response.json()
            print_success(f"{description} successful")
            print(f"   Response: {data.get('response_text')}")
            return data
        else:
            print_fail(f"{description} failed (Status {response.status_code})")
            print(response.text)
            return None
    except requests.exceptions.ConnectionError:
        print_fail("Could not connect to server. Is it running on port 8000?")
        return None

def get_monday_schedule():
    """Read the actual schedule file for Monday to verify state"""
    try:
        with open("schedule/monday/schedule.json", "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"events": []}

def test_pipeline_integration():
    print_step("Step 3: Testing Full Pipeline Integration")
    
    # 0. Clear Monday first to avoid conflicts
    clear_transcript = "Clear Monday"
    res = send_pipeline_command(clear_transcript, "Clear Schedule")
    if not res: return False
    
    # Verify empty
    sched = get_monday_schedule()
    if sched.get("events"):
        print_fail(f"Schedule not empty after clear: {len(sched['events'])} events found")
        return False
    print_success("Verified: Monday schedule is empty")
    
    # 1. Add Event
    add_transcript = "Add a system test event on Monday at 10am"
    res = send_pipeline_command(add_transcript, "Add Event")
    if not res: return False
    
    # Verify it's in the schedule
    sched = get_monday_schedule()
    events = sched.get("events", [])
    found = False
    for evt in events:
        if "system test event" in evt["name"].lower() and "10:00" in evt["start"]:
            found = True
            break
    
    if not found:
        print_fail("Event not found in schedule.json after Add")
        print(json.dumps(events, indent=2))
        return False
    print_success("Verified: Event found in schedule.json at 10:00")
    
    # 2. Modify Event
    # Wait a bit to ensure file writes complete (though server should be synchronous)
    time.sleep(1)
    mod_transcript = "Move the system test event on Monday to 11am"
    res = send_pipeline_command(mod_transcript, "Modify Event")
    if not res: return False
    
    # Verify it moved
    sched = get_monday_schedule()
    events = sched.get("events", [])
    found_new = False
    found_old = False
    for evt in events:
        name = evt["name"].lower()
        if "system test" in name:
            if "11:00" in evt["start"]:
                found_new = True
            elif "10:00" in evt["start"]:
                found_old = True
    
    if found_old:
        print_fail("Old event still exists at 10:00 after Modify")
        return False
    if not found_new:
        print_fail("New event not found at 11:00 after Modify")
        return False
    print_success("Verified: Event moved to 11:00 in schedule.json")
    
    # 3. Delete Event
    time.sleep(1)
    del_transcript = "Delete the system test event on Monday"
    res = send_pipeline_command(del_transcript, "Delete Event")
    if not res: return False
    
    # Verify it's gone
    sched = get_monday_schedule()
    events = sched.get("events", [])
    found = False
    for evt in events:
        if "system test event" in evt["name"].lower():
            found = True
            break
            
    if found:
        print_fail("Event still exists in schedule.json after Delete")
        return False
        
    print_success("Verified: Event removed from schedule.json")
    print_success("Full pipeline cycle (Add -> Modify -> Delete) passed!")
    return True

if __name__ == "__main__":
    print(f"{YELLOW}🚀 Starting System Test Suite{RESET}")
    
    if not check_auth():
        sys.exit(1)
        
    if not test_direct_gcal():
        print_fail("Skipping pipeline test due to GCal failure")
        sys.exit(1)
        
    if not test_pipeline_integration():
        sys.exit(1)
        
    print_step("🎉 All Tests Completed Successfully")
