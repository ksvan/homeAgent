"""Tests for app/email/extractor.py — extract_signals() and formatting helpers.

Pure functions — no DB, no mocking needed.
"""

from __future__ import annotations

import json

from app.email.extractor import (
    EmailSignals,
    extract_signals,
    format_signals_for_summary,
    signals_to_json,
)


# ---------------------------------------------------------------------------
# Flight extraction
# ---------------------------------------------------------------------------


def test_extracts_known_airline_flight_number() -> None:
    signals = extract_signals("Your booking for SK1472 is confirmed.")
    assert len(signals.flights) == 1
    assert signals.flights[0].flight_number == "SK1472"
    assert signals.flights[0].carrier_code == "SK"
    assert signals.flights[0].number == "1472"


def test_extracts_multiple_flights() -> None:
    signals = extract_signals("Outbound SK802, return SK803.")
    flight_numbers = {f.flight_number for f in signals.flights}
    assert "SK802" in flight_numbers
    assert "SK803" in flight_numbers


def test_deduplicates_flights() -> None:
    signals = extract_signals("Flight SK1472 (SK1472) departs at 10:00.")
    assert len(signals.flights) == 1


def test_extracts_alphanumeric_carrier_code() -> None:
    # U2 is easyJet — alphanumeric carrier code
    signals = extract_signals("Book U2456 from London.")
    assert any(f.carrier_code == "U2" for f in signals.flights)


def test_no_flights_in_plain_text() -> None:
    signals = extract_signals("Hello, please find your receipt attached.")
    assert signals.flights == []


# ---------------------------------------------------------------------------
# Route association
# ---------------------------------------------------------------------------


def test_associates_route_with_flight() -> None:
    signals = extract_signals("SK1472 OSL-CPH departs 14:00.")
    assert len(signals.flights) == 1
    assert signals.flights[0].route_from == "OSL"
    assert signals.flights[0].route_to == "CPH"


def test_route_arrow_syntax() -> None:
    signals = extract_signals("DY625 OSL → BGO confirmed.")
    assert signals.flights[0].route_from == "OSL"
    assert signals.flights[0].route_to == "BGO"


def test_route_ignored_when_neither_airport_is_known() -> None:
    # XYZ and ZZZ are not in _KNOWN_AIRPORTS
    signals = extract_signals("SK100 XYZ-ZZZ departs soon.")
    assert signals.flights[0].route_from is None
    assert signals.flights[0].route_to is None


# ---------------------------------------------------------------------------
# Date extraction
# ---------------------------------------------------------------------------


def test_extracts_iso_date() -> None:
    signals = extract_signals("Departure: 2026-07-14")
    assert any("2026-07-14" in d for d in signals.dates)


def test_extracts_dotted_date() -> None:
    signals = extract_signals("Date: 14.07.2026")
    assert any("14.07.2026" in d for d in signals.dates)


def test_extracts_natural_language_date() -> None:
    signals = extract_signals("Departing 14 July 2026.")
    assert len(signals.dates) >= 1
    assert any("14" in d for d in signals.dates)


def test_date_cap_at_six() -> None:
    text = (
        "Dates: 2026-01-01, 2026-01-02, 2026-01-03, "
        "2026-01-04, 2026-01-05, 2026-01-06, 2026-01-07"
    )
    signals = extract_signals(text)
    assert len(signals.dates) <= 6


def test_associates_first_date_with_first_flight() -> None:
    signals = extract_signals("SK1472 departs 2026-07-14.")
    assert len(signals.flights) >= 1
    assert signals.flights[0].date == "2026-07-14"


# ---------------------------------------------------------------------------
# Booking reference extraction
# ---------------------------------------------------------------------------


def test_extracts_booking_ref() -> None:
    signals = extract_signals("Booking reference: ABC123")
    assert "ABC123" in signals.booking_refs


def test_excludes_common_non_ref_words() -> None:
    signals = extract_signals("ECONOMY CLASS TICKET PLEASE CHECK TOTAL PRICE")
    # All uppercase words here are in the exclude list
    assert signals.booking_refs == []


def test_booking_ref_length_bounds() -> None:
    # 4-char: too short. 9-char: too long.
    signals = extract_signals("ABCD and ABCDEFGHI1 and ABCDE")
    assert "ABCD" not in signals.booking_refs
    assert "ABCDEFGHI1" not in signals.booking_refs
    assert "ABCDE" in signals.booking_refs


def test_deduplicates_booking_refs() -> None:
    signals = extract_signals("Ref ABC123 (ABC123)")
    assert signals.booking_refs.count("ABC123") == 1


def test_booking_refs_capped_at_five() -> None:
    text = "Refs: AAAAAA BBBBBB CCCCCC DDDDDD EEEEEE FFFFFF"
    signals = extract_signals(text)
    assert len(signals.booking_refs) <= 5


# ---------------------------------------------------------------------------
# Classification and confidence
# ---------------------------------------------------------------------------


def test_kind_is_travel_booking_with_flights() -> None:
    signals = extract_signals("SK1472 OSL-CPH on 2026-07-14. Ref: XY7Z99")
    assert signals.kind == "travel_booking"


def test_confidence_high_with_two_flights() -> None:
    signals = extract_signals("SK802 then SK803 return.")
    assert signals.confidence == "high"


def test_confidence_medium_with_one_flight() -> None:
    signals = extract_signals("Your flight SK1472 is booked.")
    assert signals.confidence == "medium"


def test_kind_travel_booking_from_ref_and_date_only() -> None:
    signals = extract_signals("Booking XYZABC on 2026-07-14.")
    assert signals.kind == "travel_booking"
    assert signals.confidence == "low"


def test_kind_general_for_plain_email() -> None:
    signals = extract_signals("Hi, please find the attached invoice.")
    assert signals.kind == "general"
    assert signals.confidence == "low"


# ---------------------------------------------------------------------------
# signals_to_json
# ---------------------------------------------------------------------------


def test_signals_to_json_is_valid() -> None:
    signals = extract_signals("SK1472 OSL-CPH 2026-07-14 Ref: XYZABC")
    raw = signals_to_json(signals)
    parsed = json.loads(raw)
    assert parsed["kind"] == "travel_booking"
    assert len(parsed["flights"]) >= 1


def test_signals_to_json_empty() -> None:
    signals = EmailSignals()
    raw = signals_to_json(signals)
    parsed = json.loads(raw)
    assert parsed["flights"] == []
    assert parsed["booking_refs"] == []


# ---------------------------------------------------------------------------
# format_signals_for_summary
# ---------------------------------------------------------------------------


def test_format_returns_empty_for_no_signals() -> None:
    signals = EmailSignals()
    assert format_signals_for_summary(signals) == ""


def test_format_includes_flight_number() -> None:
    signals = extract_signals("SK1472 OSL-CPH on 2026-07-14.")
    summary = format_signals_for_summary(signals)
    assert "SK1472" in summary
    assert "OSL" in summary
    assert "CPH" in summary


def test_format_includes_booking_ref() -> None:
    signals = extract_signals("Booking reference: XYZABC on 2026-07-14.")
    summary = format_signals_for_summary(signals)
    assert "XYZABC" in summary


def test_format_header_present_when_signals_found() -> None:
    signals = extract_signals("SK1472 confirmed.")
    summary = format_signals_for_summary(signals)
    assert summary.startswith("## Extracted Signals")
