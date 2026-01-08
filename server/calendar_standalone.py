import os
import sys
import time
import logging
from datetime import datetime, timedelta
import pytz

# Add the server directory to sys.path to allow importing modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import os
import sys
import time
import json
import logging
from datetime import datetime, timedelta
import pytz
from dotenv import load_dotenv

# Add the server directory to sys.path to allow importing modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Load environment variables
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
LOGGER = logging.getLogger("calendar_standalone")

def setup_credentials():
    """
    Check if credentials.json exists. If not, try to create it from env vars.
    Returns the path to the credentials file to use.
    """
    # Check for standard file first (optional, but good to respect if it exists)
    # But user specifically wants to use env vars now.
    
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    project_id = os.environ.get("GOOGLE_PROJECT_ID")
    
    if client_id and client_secret:
        print("[setup] Found Google Auth env vars. Creating temp credentials file...")
        
        creds_data = {
            "installed": {
                "client_id": client_id,
                "project_id": project_id or "smartpager-480305",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "client_secret": client_secret,
                "redirect_uris": ["http://localhost"]
            }
        }
        
        temp_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp_credentials.json")
        with open(temp_path, "w") as f:
            json.dump(creds_data, f, indent=2)
            
        # Tell calendar_client where to look
        os.environ["CREDENTIALS_PATH"] = temp_path
        return temp_path
    
    return None

def main():
    print("=== SmartPager Standalone Calendar Script ===")
    
    # 0. Setup Credentials
    temp_creds_path = setup_credentials()
    
    # Import modules AFTER setting up env vars
    from modules import simple_calendar as calendar_utils
    
    # 1. Authentication (Implicitly handled by simple_calendar)
    print("\n[1] Testing Authentication & List Events...")
    try:
        # Fetch events for the next 7 days to verify auth works
        events = calendar_utils.fetch_events(lookahead_days=7)
        print(f"    Success! Found {len(events)} upcoming events.")
        for i, evt in enumerate(events[:3]): # Show first 3
            print(f"    - {evt['start']} : {evt['name']}")
    except Exception as e:
        print(f"    FAILED: {e}")
        if temp_creds_path and os.path.exists(temp_creds_path):
            os.remove(temp_creds_path)
        return

    # 2. Add Event
    print("\n[2] Testing Add Event...")
    
    # Calculate times
    tz = pytz.timezone(os.getenv("TIMEZONE", "America/New_York"))
    now = datetime.now(tz)
    start_time = now + timedelta(hours=1)
    end_time = start_time + timedelta(hours=1)
    
    event_title = "SmartPager Test Event"
    event_desc = "This is a test event created by the standalone script."
    
    try:
        result = calendar_utils.create_event(
            title=event_title,
            start=start_time.isoformat(),
            end=end_time.isoformat(),
            description=event_desc
        )
        
        if result.get("status") == "success":
            event_id = result["event"]["id"]
            print(f"    Success! Created event ID: {event_id}")
            print(f"    Link: {result['event'].get('htmlLink')}")
        else:
            print(f"    FAILED: {result.get('error')}")
            if temp_creds_path and os.path.exists(temp_creds_path):
                os.remove(temp_creds_path)
            return
    except Exception as e:
        print(f"    FAILED: {e}")
        if temp_creds_path and os.path.exists(temp_creds_path):
            os.remove(temp_creds_path)
        return

    # 3. Modify Event
    print("\n[3] Testing Modify Event...")
    input(">>> Check Google Calendar for the new event. Press Enter to MODIFY it...")
    
    new_title = "SmartPager Test Event (MODIFIED)"
    new_end_time = end_time + timedelta(minutes=30)
    
    try:
        result = calendar_utils.update_event(
            event_id=event_id,
            title=new_title,
            end=new_end_time.isoformat()
        )
        
        if result.get("status") == "success":
            print(f"    Success! Updated event title to: '{result['event']['name']}'")
            print(f"    New End Time: {result['event']['end']}")
        else:
            print(f"    FAILED: {result.get('error')}")
            # Don't return here, try to delete anyway
    except Exception as e:
        print(f"    FAILED: {e}")

    # 4. Delete Event
    print("\n[4] Testing Delete Event...")
    input(">>> Check Google Calendar for the MODIFIED event. Press Enter to DELETE it...")
    
    try:
        result = calendar_utils.delete_event(event_id=event_id)
        
        if result.get("status") == "success":
            print("    Success! Event deleted.")
        else:
            print(f"    FAILED: {result.get('error')}")
    except Exception as e:
        print(f"    FAILED: {e}")

    # Cleanup
    input(">>> Check Google Calendar to ensure event is DELETED. Press Enter to finish...")
    if temp_creds_path and os.path.exists(temp_creds_path):
        print(f"\n[Cleanup] Removing temp credentials file: {temp_creds_path}")
        os.remove(temp_creds_path)

    print("\n=== Test Complete ===")

if __name__ == "__main__":
    main()
