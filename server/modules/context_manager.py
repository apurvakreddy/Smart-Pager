from enum import Enum
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

class ContextState(Enum):
    IDLE = "idle"
    AWAITING_CLARIFICATION = "awaiting_clarification"
    AWAITING_CONFLICT_RESOLUTION = "awaiting_conflict_resolution"

class ContextManager:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ContextManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.reset()
        
    def reset(self):
        """Reset context to idle state"""
        self.state = ContextState.IDLE
        self.last_transcript = None
        self.pending_event = None
        self.recommendation = None
        self.last_interaction_time = None
        
    def is_expired(self, timeout_seconds=300) -> bool:
        """Check if context has expired"""
        if not self.last_interaction_time:
            return True
        return (datetime.now() - self.last_interaction_time).total_seconds() > timeout_seconds
        
    def set_clarification_state(self, original_transcript: str):
        """Enter clarification state"""
        self.state = ContextState.AWAITING_CLARIFICATION
        self.last_transcript = original_transcript
        self.last_interaction_time = datetime.now()
        print(f"[ContextManager] State set to AWAITING_CLARIFICATION. Transcript: '{original_transcript}'")
        
    def set_conflict_state(self, pending_event: Dict[str, Any], recommendation: Optional[Dict[str, Any]] = None):
        """Enter conflict resolution state"""
        self.state = ContextState.AWAITING_CONFLICT_RESOLUTION
        self.pending_event = pending_event
        self.recommendation = recommendation
        self.last_interaction_time = datetime.now()
        print(f"[ContextManager] State set to AWAITING_CONFLICT_RESOLUTION. Event: {pending_event.get('name')}")

    def get_context(self) -> Dict[str, Any]:
        """Get current valid context"""
        if self.is_expired():
            if self.state != ContextState.IDLE:
                print("[ContextManager] Context expired, resetting.")
                self.reset()
            return {"state": ContextState.IDLE}
        print (f"[ContextManager] Returning current context state: {self.state}")
        print (f"[ContextManager] Last transcript: {self.last_transcript}")
        print (f"[ContextManager] Pending event: {self.pending_event}")
        print (f"[ContextManager] Recommendation: {self.recommendation}")
            
        return {
            "state": self.state,
            "last_transcript": self.last_transcript,
            "pending_event": self.pending_event,
            "recommendation": self.recommendation
        }

# Global accessor
def get_context_manager():
    return ContextManager()
