import os
import sys
from datetime import datetime, timedelta

# Add server directory to path so we can import modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Set credentials path explicitly
os.environ["CREDENTIALS_PATH"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials.json")

from modules.calendar_utils import create_event

def test_create_event():
    print("Testing Google Calendar Integration...")
    
    # Create an event 1 hour from now
    start_time = datetime.now() + timedelta(hours=1)
    end_time = start_time + timedelta(hours=1)
    
    start_iso = start_time.isoformat()
    end_iso = end_time.isoformat()
    
    print(f"Attempting to create event: 'Test Event from SmartPager' at {start_iso}")
    
    try:
        result = create_event(
            title="Test Event from SmartPager",
            start=start_iso,
            end=end_iso,
            description="This is a test event created by the SmartPager verification script."
        )
        
        if result.get("status") == "success":
            print("\n✅ SUCCESS: Event created successfully!")
            print(f"Event ID: {result['event']['id']}")
            print(f"Link: {result['event'].get('htmlLink', 'N/A')}")
        else:
            print("\n❌ FAILURE: Failed to create event.")
            print(f"Error: {result.get('error')}")
            
    except Exception as e:
        print(f"\n❌ EXCEPTION: An error occurred: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_create_event()
