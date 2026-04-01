# Import all SQLModel table classes so Alembic can discover them via target_metadata.
from app.models.cache import AgentRunLog, DeviceSnapshot, EventLog, PendingAction
from app.models.memory import (
    ConversationTurn,
    ConversationMessage,
    ConversationSummary,
    EpisodicMemory,
    HouseholdProfile,
    UserProfile,
)
from app.models.scheduled_prompts import ScheduledPrompt, ScheduledPromptLink, ScheduledPromptRun
from app.models.tasks import Task, TaskLink, TaskStep
from app.models.users import ActionPolicy, ChannelMapping, Household, User
from app.models.world import (
    CalendarEntity,
    DeviceEntity,
    HouseholdMember,
    MemberActivity,
    MemberGoal,
    MemberInterest,
    Place,
    Relationship,
    RoutineEntity,
    WorldFact,
    WorldModelProposal,
)

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
    "ConversationTurn",
    "DeviceSnapshot",
    "EventLog",
    "AgentRunLog",
    "PendingAction",
    "ScheduledPrompt",
    "ScheduledPromptLink",
    "ScheduledPromptRun",
    "Task",
    "TaskLink",
    "TaskStep",
    "HouseholdMember",
    "MemberInterest",
    "MemberGoal",
    "MemberActivity",
    "Place",
    "DeviceEntity",
    "CalendarEntity",
    "RoutineEntity",
    "Relationship",
    "WorldFact",
    "WorldModelProposal",
]
