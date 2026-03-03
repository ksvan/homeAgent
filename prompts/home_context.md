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

## Set location
Home is in timezone CET

## Home layout

<!--
  Describe your home's rooms and floors so the agent can interpret
  requests like "turn off the lights downstairs" correctly.

  Example (edit to match your home):
-->

The home has three floors. Norwegian standard where ground floor equals first floor

- **Basement floor:** Gym, movie room, bathrom, storage, hallway/stair
- **First floor / ground floor:** kitchen, living room, hallway, dining room, outer hallway
- **Second floor:** master bedroom, children's room 1, children's room 2,
  bathroom, home office, hallway/stairs

  There are also some outdoor devices, in the garden zone.

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
- home from school around 14:00
- "Night mode" means all lights off, heating is kept the same

---

## Current device states

Current home state (from Homey, as of {timestamp}):

{device_states}
