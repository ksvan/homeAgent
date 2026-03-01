# Home Context

<!--
  Optional template for describing your home layout, rooms, and device naming
  conventions. This is injected into the system prompt when a query appears
  home-related, supplementing live device state from Homey.

  Use this to give the agent stable background knowledge about your home
  that does not change often — room names, which devices are in which rooms,
  household routines, and any naming quirks.

  Template variables filled in at runtime:
    {timestamp}   — time of the last Homey device state snapshot
    {device_states} — current device states from the Homey state cache

  Edit the static sections below to match your home.
  Leave {device_states} in place — it is filled in dynamically.
-->

## Home layout

<!--
  Describe your home's rooms and floors so the agent can interpret
  requests like "turn off the lights downstairs" correctly.

  Example (edit to match your home):
-->

The home has two floors:

- **Ground floor:** kitchen, living room, hallway, guest toilet
- **First floor:** master bedroom, children's room (Sofie), children's room (Emma),
  bathroom, home office

The garage is connected to the house and has its own lighting zone.

## Device naming conventions

<!--
  Homey device names can sometimes be unclear. Use this section to clarify
  any naming that might confuse the agent, e.g. shorthand names, old names,
  or room associations.

  Example:
-->

- Devices named "stue" refer to the living room (Norwegian name).
- The "kontor" light is in the home office, first floor.
- The thermostat labelled "hall-nede" is the ground floor hallway.

## Routines and context

<!--
  Optional: describe common household routines, schedules, or patterns
  that help the agent give better responses without asking for context.

  Example:
-->

- The household is typically awake 06:30–23:00 on weekdays,
  slightly later on weekends.
- School pick-up is around 14:30 on weekdays.
- "Night mode" means all lights off, heating set to 18°C.

---

## Current device states

Current home state (from Homey, as of {timestamp}):

{device_states}
