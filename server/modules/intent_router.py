# smartPager/server/modules/intent_router.py
"""
Intent-based routing system for voice commands.

Classifies user intent from transcripts and routes to appropriate handlers.
Supports multi-tool architecture where the LLM decides which action to take.
"""

import os
import json
from datetime import datetime
from openai import OpenAI
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict
from enum import Enum


class Intent(Enum):
    """Supported user intents"""
    MODIFY_SCHEDULE = "modify_schedule"      # Add, edit, or delete events
    QUERY_DAY = "query_day"                  # "What's my schedule for Monday?"
    QUERY_WEEK = "query_week"                # "What does my week look like?"
    CLEAR_DAY = "clear_day"                  # "Clear Monday's schedule"
    CLEAR_WEEK = "clear_week"                # "Start fresh" / "Clear everything"
    HELP = "help"                            # "What can you do?"
    CLARIFICATION_NEEDED = "clarification_needed" # Ambiguous time/day
    UNKNOWN = "unknown"                      # Can't understand


@dataclass
class IntentResult:
    """Result of intent classification"""
    intent: Intent
    confidence: float  # 0.0 to 1.0
    parameters: Dict[str, Any]  # Intent-specific parameters
    raw_response: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent.value,
            "confidence": self.confidence,
            "parameters": self.parameters
        }


# System prompt for intent classification
INTENT_CLASSIFICATION_PROMPT = """
You are an intent classifier for a voice-controlled scheduling assistant.
Your job is to analyze the user's transcript and determine their intent.

## Available Intents

1. **modify_schedule**: User wants to ADD, EDIT, or DELETE events
   - Examples: "Add a meeting Monday at 2pm", "Cancel my dentist appointment", 
     "Move gym to 5pm", "Schedule lunch with Bob tomorrow"
   - Parameters needed: operations (list of add/edit/delete actions)

2. **query_day**: User wants to know their schedule for a SPECIFIC day
   - Examples: "What's on Monday?", "What do I have tomorrow?", "Tell me about Tuesday"
   - Parameters needed: day (which day they're asking about)

3. **query_week**: User wants an overview of their ENTIRE week
   - Examples: "What does my week look like?", "Give me a summary", "What's coming up?"
   - Parameters needed: none

4. **clear_day**: User wants to CLEAR a specific day's schedule
   - Examples: "Clear Monday", "Delete everything on Tuesday", "Remove all Wednesday events"
   - Parameters needed: day (which day to clear)

5. **clear_week**: User wants to CLEAR the ENTIRE week / start fresh
   - Examples: "Clear my week", "Start fresh", "Delete everything", "Reset my schedule"
   - Parameters needed: none

6. **help**: User is asking what the assistant can do
   - Examples: "What can you do?", "Help", "What are my options?"
   - Parameters needed: none

7. **clarification_needed**: User's request is ambiguous regarding TIME or DAY
   - Examples: "Add a meeting later", "Schedule lunch sometime", "Put gym in my calendar" (missing day/time)
   - Parameters needed: missing_info (string description of what's missing)

8. **unknown**: Cannot determine intent (gibberish, off-topic, etc.)
   - Parameters needed: none

## Output Format

Respond with ONLY valid JSON in this exact format:
{
  "intent": "modify_schedule|query_day|query_week|clear_day|clear_week|help|clarification_needed|unknown",
  "confidence": 0.0-1.0,
  "parameters": {
    // For modify_schedule: include "operations" array (see below)
    // For query_day/clear_day: include "day" string
    // For others: empty object {}
  }
}

## For modify_schedule, the parameters.operations array format:

{
  "intent": "modify_schedule",
  "confidence": 0.95,
  "parameters": {
    "operations": [
      {
        "action": "add|edit|delete",
        "day": "monday|tuesday|...|sunday|today|tomorrow",
        "event": {
          "name": "Event name",
          "type": "fixed|flexible",
          "start": "HH:MM (24-hour)",
          "end": "HH:MM (24-hour, optional for flexible)",
          "durationMinutes": number (optional, for flexible tasks)
        }
      }
    ]
  }
}

## Important Rules

1. Output ONLY valid JSON. No explanations.
2. If the user mentions multiple events or days, include ALL in the operations array.
3. For relative days like "today" or "tomorrow", use those exact words - they'll be resolved later.
4. If the user provides a DURATION but no explicit time (e.g., "homework for two hours on Monday"), treat it as modify_schedule with a FLEXIBLE task for that day (no clarification needed).
5. If no specific day is mentioned, assume "today" ONLY IF the time is specific. If both day and time are vague, use 'clarification_needed'.
6. If no end time is given, estimate based on context (meetings: 1hr, lunch: 1hr, etc.)
7. For delete operations, you only need action, day, and event.name.
8. Be generous with confidence - if the intent is clear, use 0.9+.
9. Only use 'clarification_needed' if BOTH time and duration are missing/ambiguous for an ADD operation.
"""


def get_openai_client() -> OpenAI:
    """Get OpenAI client with API key from environment"""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set in environment variables.")
    return OpenAI(api_key=api_key)


def classify_intent(transcript: str, current_datetime: datetime = None) -> IntentResult:
    """
    Classify the user's intent from their transcript.
    
    Args:
        transcript: The transcribed voice command
        current_datetime: Current datetime from client (for context)
        
    Returns:
        IntentResult with classified intent and parameters
    """
    if not transcript or not transcript.strip():
        return IntentResult(
            intent=Intent.UNKNOWN,
            confidence=1.0,
            parameters={"reason": "Empty transcript"}
        )
    
    if current_datetime is None:
        current_datetime = datetime.now()
    
    # Add context about current date/time
    context = f"""
Current date/time: {current_datetime.strftime("%A, %B %d, %Y at %I:%M %p")}
Today is: {current_datetime.strftime("%A").lower()}

User transcript: "{transcript}"
"""
    
    try:
        client = get_openai_client()
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": INTENT_CLASSIFICATION_PROMPT},
                {"role": "user", "content": context}
            ],
            temperature=0.0,
        )
        
        raw_response = response.choices[0].message.content
        result = parse_intent_response(raw_response)
        result.raw_response = raw_response
        
        print(f"[intent_router] Classified intent: {result.intent.value} (confidence: {result.confidence})")
        return result
        
    except Exception as e:
        print(f"[intent_router] Error classifying intent: {e}")
        return IntentResult(
            intent=Intent.UNKNOWN,
            confidence=0.0,
            parameters={"error": str(e)}
        )


def parse_intent_response(raw_text: str) -> IntentResult:
    """
    Parse the LLM's JSON response into an IntentResult.
    
    Args:
        raw_text: Raw JSON string from LLM
        
    Returns:
        IntentResult object
    """
    # Clean up markdown code blocks if present
    clean_text = raw_text.strip()
    if clean_text.startswith("```json"):
        clean_text = clean_text[7:]
    if clean_text.startswith("```"):
        clean_text = clean_text[3:]
    if clean_text.endswith("```"):
        clean_text = clean_text[:-3]
    clean_text = clean_text.strip()
    
    try:
        data = json.loads(clean_text)
        
        # Map string intent to enum
        intent_str = data.get("intent", "unknown").lower()
        intent_map = {
            "modify_schedule": Intent.MODIFY_SCHEDULE,
            "query_day": Intent.QUERY_DAY,
            "query_week": Intent.QUERY_WEEK,
            "clear_day": Intent.CLEAR_DAY,
            "clear_week": Intent.CLEAR_WEEK,
            "help": Intent.HELP,
            "clarification_needed": Intent.CLARIFICATION_NEEDED,
            "unknown": Intent.UNKNOWN,
        }
        intent = intent_map.get(intent_str, Intent.UNKNOWN)
        
        return IntentResult(
            intent=intent,
            confidence=float(data.get("confidence", 0.5)),
            parameters=data.get("parameters", {})
        )
        
    except json.JSONDecodeError as e:
        print(f"[intent_router] Failed to parse JSON: {e}")
        print(f"[intent_router] Raw response: {raw_text[:500]}")
        return IntentResult(
            intent=Intent.UNKNOWN,
            confidence=0.0,
            parameters={"parse_error": str(e)}
        )


# ==================== INTENT HANDLERS ====================
# These will be called by the audio_pipeline based on classified intent

def get_help_response() -> str:
    """Generate help text for the HELP intent"""
    return """I can help you manage your weekly schedule. Here's what I can do:

To add events, say things like "Add a meeting on Monday at 2pm" or "Schedule lunch with Bob tomorrow at noon".

To check your schedule, ask "What's on Monday?" or "What does my week look like?"

To remove events, say "Cancel my dentist appointment" or "Delete the meeting on Tuesday".

To clear your schedule, say "Clear Monday" or "Start fresh" to clear the whole week.

What would you like to do?"""


def format_operations_summary(operations: List[Dict[str, Any]]) -> str:
    """
    Format a list of schedule operations into a human-readable summary.
    Used for TTS feedback after modifications.
    
    Args:
        operations: List of operation dictionaries from intent classification
        
    Returns:
        Human-readable summary string
    """
    if not operations:
        return "No changes were made to your schedule."
    
    summaries = []
    
    for op in operations:
        action = op.get("action", "").lower()
        day = op.get("day", "today")
        event = op.get("event", {})
        name = event.get("name", "event")
        start = event.get("start", "")
        
        if action == "add":
            if start:
                summaries.append(f"Added {name} on {day} at {start}")
            else:
                summaries.append(f"Added {name} on {day}")
        elif action == "edit":
            summaries.append(f"Updated {name} on {day}")
        elif action == "delete":
            summaries.append(f"Removed {name} from {day}")
    
    if len(summaries) == 1:
        return summaries[0] + "."
    elif len(summaries) == 2:
        return f"{summaries[0]} and {summaries[1].lower()}."
    else:
        return ", ".join(summaries[:-1]) + f", and {summaries[-1].lower()}."


def validate_operations(operations: List[Dict[str, Any]]) -> tuple:
    """
    Validate and clean up operations from intent classification.
    
    Args:
        operations: List of operation dictionaries
        
    Returns:
        Tuple of (valid_operations, errors)
    """
    valid = []
    errors = []
    
    for i, op in enumerate(operations):
        action = op.get("action", "").lower()
        
        if action not in ["add", "edit", "delete"]:
            errors.append(f"Operation {i+1}: Invalid action '{action}'")
            continue
        
        if not op.get("day"):
            op["day"] = "today"  # Default to today
        
        event = op.get("event", {})
        if action in ["add", "edit"] and not event.get("name"):
            errors.append(f"Operation {i+1}: Missing event name")
            continue
        
        if action == "delete" and not event.get("name"):
            errors.append(f"Operation {i+1}: Missing event name to delete")
            continue
        
        valid.append(op)
    
    return valid, errors
