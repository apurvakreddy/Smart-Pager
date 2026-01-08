import os
import sys
from datetime import datetime
from dotenv import load_dotenv

# Load env vars
load_dotenv()

# Add server directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Set credentials path explicitly
os.environ["CREDENTIALS_PATH"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials.json")

from modules.audio_pipeline import process_transcript_only

def test_pipeline():
    print("Testing Audio Pipeline Integration...")
    
    transcript = "Add a lunch meeting tomorrow at 12pm"
    print(f"Input Transcript: '{transcript}'")
    
    try:
        # Process the transcript
        result = process_transcript_only(transcript)
        
        if result.success:
            print("\n✅ SUCCESS: Pipeline processed the request.")
            print(f"Intent: {result.intent}")
            print(f"Response: {result.response_text}")
            
            # Check if changes were made
            if result.changes_made:
                print("\nChanges Made:")
                print(result.changes_made)
                
                # Verify _calendar_id in the saved file
                import json
                # Assuming tomorrow is Monday based on previous runs
                schedule_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schedule", "monday", "schedule.json")
                if os.path.exists(schedule_path):
                    with open(schedule_path, 'r') as f:
                        data = json.load(f)
                        events = data.get("events", [])
                        found = False
                        for evt in events:
                            if evt.get("name") == "lunch meeting":
                                found = True
                                print(f"\n🔎 Verifying storage for 'lunch meeting':")
                                if "_calendar_id" in evt:
                                    print(f"   ✅ Found _calendar_id: {evt['_calendar_id']}")
                                    print(f"   ✅ Found _calendar_htmlLink: {evt.get('_calendar_htmlLink')}")
                                else:
                                    print("   ❌ _calendar_id NOT found in saved file!")
                                break
                        if not found:
                             print("   ❌ Event 'lunch meeting' not found in schedule file.")
                else:
                     print(f"   ❌ Schedule file not found at {schedule_path}")

                # Check for added events
                added = result.changes_made.get("added", [])
                if added:
                    print(f"✅ Added {len(added)} event(s).")
                    print("Check your Google Calendar for 'strategy meeting' tomorrow at 10am.")
                else:
                    print("⚠️ No events were added.")
            else:
                print("⚠️ No changes reported.")
                
        else:
            print("\n❌ FAILURE: Pipeline failed.")
            print(f"Error: {result.error}")
            
    except Exception as e:
        print(f"\n❌ EXCEPTION: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_pipeline()
