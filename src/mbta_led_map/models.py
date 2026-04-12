"""Pydantic models for MBTA vehicles, LED state, and WebSocket messages."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class Vehicle(BaseModel):
    """A single MBTA vehicle parsed from the API response."""

    id: str
    """Unique vehicle ID (e.g. 'G-10040')."""

    route_id: str
    """MBTA route identifier (e.g. 'Red', 'Green-B')."""

    direction_id: int
    """0 = outbound / away from terminal; 1 = inbound / toward terminal."""

    stop_id: Optional[str] = None
    """The stop the vehicle is at or heading toward (may be a platform ID)."""

    current_status: str
    """One of STOPPED_AT, INCOMING_AT, IN_TRANSIT_TO."""

    latitude: Optional[float] = None
    """GPS latitude reported by the vehicle (may be null)."""

    longitude: Optional[float] = None
    """GPS longitude reported by the vehicle (may be null)."""

    label: Optional[str] = None
    """Human-readable vehicle label (e.g. train number)."""


class LedState(BaseModel):
    """Brightness state for a single LED, 0–255."""

    index: int = Field(..., ge=0, description="LED position in the daisy chain.")
    brightness: int = Field(..., ge=0, le=255, description="0 = off, 255 = train at station.")


class LineState(BaseModel):
    """All LED brightness values for one subway line."""

    leds: list[int] = Field(..., description="Brightness array, one entry per LED in index order.")
    count: int = Field(..., description="Number of LEDs on this line (len(leds)).")


class MapState(BaseModel):
    """Full LED map state broadcast over WebSocket on each poll cycle."""

    timestamp: str = Field(..., description="ISO-8601 UTC timestamp of the poll.")
    vehicle_count: int = Field(0, description="Number of vehicles returned by the MBTA API.")
    lines: dict[str, LineState] = Field(
        default_factory=dict,
        description="Per-line LED states keyed by line name (red, orange, blue, green, mattapan).",
    )

    @classmethod
    def empty(cls, led_counts: dict[str, int]) -> "MapState":
        """Create an all-zero MapState given a dict of {line: led_count}."""
        return cls(
            timestamp=datetime.utcnow().isoformat() + "Z",
            vehicle_count=0,
            lines={
                line: LineState(leds=[0] * count, count=count)
                for line, count in led_counts.items()
            },
        )
