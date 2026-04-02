from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class SlashCommandContext:
    raw_text: str
    args: list[str]
    user_id: str
    user_name: str
    telegram_id: int
    is_admin: bool
    household_id: str


class SlashCommand(ABC):
    name: str
    help: str
    admin_only: bool = False

    @abstractmethod
    async def run(self, ctx: SlashCommandContext) -> str: ...


class SlashCommandRegistry:
    def __init__(self) -> None:
        self._commands: dict[str, SlashCommand] = {}

    def register(self, cmd: SlashCommand) -> None:
        self._commands[cmd.name] = cmd

    def get(self, name: str) -> SlashCommand | None:
        return self._commands.get(name)

    def list_visible(self, is_admin: bool) -> list[SlashCommand]:
        return [cmd for cmd in self._commands.values() if not cmd.admin_only or is_admin]
