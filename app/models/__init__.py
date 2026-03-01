# Import all SQLModel table classes so Alembic can discover them via target_metadata.
from app.models.cache import AgentRunLog, DeviceSnapshot, EventLog, PendingAction
from app.models.memory import (
    ConversationMessage,
    ConversationSummary,
    EpisodicMemory,
    HouseholdProfile,
    UserProfile,
)
from app.models.tasks import Task
from app.models.users import ActionPolicy, ChannelMapping, Household, User

__all__ = [
    "Household",
    "User",
    "ActionPolicy",
    "ChannelMapping",
    "UserProfile",
    "HouseholdProfile",
    "EpisodicMemory",
    "ConversationMessage",
    "ConversationSummary",
    "DeviceSnapshot",
    "EventLog",
    "AgentRunLog",
    "PendingAction",
    "Task",
]
