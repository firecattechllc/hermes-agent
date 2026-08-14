"""Governed, default-off browser capability boundary."""

from .adapter import BrowserOSMCPAdapter
from .config import BrowserConfig
from .models import (
    BrowserAvailability,
    BrowserCapabilityError,
    BrowserErrorCategory,
    BrowserHealth,
    BrowserState,
    BrowserTool,
    BrowserToolResult,
)

__all__ = [
    "BrowserAvailability",
    "BrowserCapabilityError",
    "BrowserConfig",
    "BrowserErrorCategory",
    "BrowserHealth",
    "BrowserOSMCPAdapter",
    "BrowserState",
    "BrowserTool",
    "BrowserToolResult",
]
