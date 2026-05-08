"""
Structured signal extraction from email body text.

Extracts flight candidates, booking references, and travel dates from
preprocessed email text. Results are stored as `proposed_action_json`
on the EmailMessage and included in the intake summary passed to the agent.

All extracted values are candidates — the agent validates them via tools.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

# IATA airline code (2-char) + flight number (1–4 digits) + optional suffix
_FLIGHT_NUMBER = re.compile(
    r"\b([A-Z]{2}|[A-Z][0-9])[0-9]{1,4}[A-Z]?\b"
)

# IATA airport codes: 3 uppercase letters standing alone or in route context
_AIRPORT_CODE = re.compile(r"\b([A-Z]{3})\b")

# Route patterns: OSL-CPH, OSL → CPH, OSL -> CPH, OSL/CPH
_ROUTE = re.compile(
    r"\b([A-Z]{3})\s*(?:[-–→/]|->)\s*([A-Z]{3})\b"
)

# Date patterns in multiple formats
_DATE_PATTERNS = [
    # 2026-05-12, 12.05.2026, 12/05/2026
    re.compile(r"\b(\d{4}[-./]\d{2}[-./]\d{2}|\d{2}[-./]\d{2}[-./]\d{4})\b"),
    # 12 May 2026, May 12 2026, 12 May, May 12
    re.compile(
        r"\b(\d{1,2})\s+(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May"
        r"|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        r"(?:\s+(\d{4}))?\b",
        re.IGNORECASE,
    ),
]

# Booking references: 5–8 uppercase alphanumeric characters alone on context
_BOOKING_REF = re.compile(r"\b([A-Z0-9]{5,8})\b")

# Common non-reference uppercase strings to exclude
_BOOKING_REF_EXCLUDE = {
    "FLIGHT", "TICKET", "CLASS", "CABIN", "ECONOMY", "BUSINESS", "FIRST",
    "PLEASE", "EMAIL", "PHONE", "CHECK", "SEATS", "TERMS", "MILES",
    "BONUS", "POINT", "DEBIT", "CREDIT", "TOTAL", "PRICE", "ORDER",
}

_KNOWN_AIRLINES = {
    "SK", "DY", "LH", "BA", "AF", "KL", "AA", "UA", "DL", "EK", "QR",
    "TK", "FR", "U2", "W6", "LS", "BT", "AY", "SN", "LX", "OS", "IB",
    "VY", "TO", "PC", "WF", "D8", "DX",
}

_KNOWN_AIRPORTS = {
    "OSL", "BGO", "SVG", "TRD", "TOS", "BOO", "LYR",  # Norway
    "CPH", "ARN", "HEL", "GOT",                          # Nordics
    "LHR", "LGW", "MAN", "EDI", "AMS", "CDG", "FRA",   # Western Europe
    "JFK", "LGA", "EWR", "LAX", "SFO", "ORD", "MIA",   # North America
    "DXB", "DOH", "SIN", "HKG", "NRT", "BKK",           # Long-haul
}


@dataclass
class FlightCandidate:
    flight_number: str
    carrier_code: str
    number: str
    route_from: str | None = None
    route_to: str | None = None
    date: str | None = None


@dataclass
class EmailSignals:
    flights: list[FlightCandidate] = field(default_factory=list)
    booking_refs: list[str] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)
    kind: str = "general"           # "travel_booking" | "general"
    confidence: str = "low"         # "high" | "medium" | "low"


def extract_signals(text: str) -> EmailSignals:
    """Extract structured travel signals from email body text."""
    signals = EmailSignals()
    upper = text.upper()

    # --- Flights ---
    seen_flights: set[str] = set()
    for m in _FLIGHT_NUMBER.finditer(upper):
        raw = m.group(0)
        carrier = raw[:2]
        number = raw[2:]
        if carrier in _KNOWN_AIRLINES or len(number) >= 2:
            if raw not in seen_flights:
                seen_flights.add(raw)
                signals.flights.append(FlightCandidate(
                    flight_number=raw,
                    carrier_code=carrier,
                    number=number,
                ))

    # --- Routes — try to associate with a nearby flight ---
    routes = _ROUTE.findall(upper)
    for i, (orig, dest) in enumerate(routes):
        if orig in _KNOWN_AIRPORTS or dest in _KNOWN_AIRPORTS:
            if i < len(signals.flights):
                signals.flights[i].route_from = orig
                signals.flights[i].route_to = dest

    # --- Dates ---
    seen_dates: set[str] = set()
    for pat in _DATE_PATTERNS:
        for m in pat.finditer(text, re.IGNORECASE if hasattr(pat, "flags") else 0):
            raw = m.group(0).strip()
            if raw not in seen_dates:
                seen_dates.add(raw)
                signals.dates.append(raw)
    signals.dates = signals.dates[:6]

    # --- Try to associate first date with first flight ---
    if signals.flights and signals.dates:
        signals.flights[0].date = signals.dates[0]

    # --- Booking references ---
    for m in _BOOKING_REF.finditer(upper):
        ref = m.group(0)
        if ref not in _BOOKING_REF_EXCLUDE and len(ref) in range(5, 9):
            signals.booking_refs.append(ref)
    # Deduplicate and cap
    seen: set[str] = set()
    deduped = []
    for r in signals.booking_refs:
        if r not in seen:
            seen.add(r)
            deduped.append(r)
    signals.booking_refs = deduped[:5]

    # --- Classification ---
    if signals.flights:
        signals.kind = "travel_booking"
        signals.confidence = "high" if len(signals.flights) >= 2 else "medium"
    elif signals.booking_refs and signals.dates:
        signals.kind = "travel_booking"
        signals.confidence = "low"

    return signals


def signals_to_json(signals: EmailSignals) -> str:
    d = asdict(signals)
    return json.dumps(d)


def format_signals_for_summary(signals: EmailSignals) -> str:
    """Return a compact signals block for the intake summary."""
    if not signals.flights and not signals.booking_refs:
        return ""

    lines = ["## Extracted Signals"]
    for f in signals.flights:
        route = f" {f.route_from} → {f.route_to}" if f.route_from and f.route_to else ""
        date_str = f", {f.date}" if f.date else ""
        lines.append(f"  - flight: {f.flight_number}{route}{date_str}")
    for ref in signals.booking_refs[:2]:
        lines.append(f"  - booking ref: {ref}")
    for d in signals.dates[:2]:
        lines.append(f"  - date: {d}")
    return "\n".join(lines)
