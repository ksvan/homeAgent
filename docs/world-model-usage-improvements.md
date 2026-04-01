# World Model Usage Improvements

## Purpose

This note proposes a small but important improvement to how HomeAgent uses the
existing household world model at runtime.

The current system already has several relevant memory layers:

- recent conversation turns
- compact world-model prompt injection
- episodic memory, including `normal` and `ephemeral` retention tiers
- profile context

The issue shown in the cabin weather scenario is not missing data. The issue is
that the agent does not always treat those layers in the right order when
resolving vague but common household references.

The goal is to make the agent:

- faster and less clarification-heavy
- more relevant in everyday household conversation
- more grounded in canonical household knowledge
- more natural and "family-aware" without becoming overconfident

---

## Example Failure

Observed scenario:

1. User says: "We are at the cabin, what is the weather forecast here?"
2. The world model already contains a cabin location.
3. The agent still asks for the location instead of resolving it.
4. After being reminded about the world model, the agent succeeds.

This shows a gap between:

- world model being present in context
- and world model being used as an active grounding step

---

## Core Problem

Today the world model behaves mostly like:

- compact structured prompt context
- a fallback data source the model may or may not use
- a tool-accessible knowledge layer

For questions like weather, travel, charging, routines, room/device references,
or family coordination, that is too passive.

The world model should instead be used as:

- the default resolver for household entities and place references
- a source of candidate assumptions before the agent asks for clarification
- a grounding layer that helps tools run with canonical entities instead of
  free-text guesses

In short: world model usage should move from "available context" to "active
resolution layer".

At the same time, this should not ignore the memory features already in place.
The better model is:

- recent conversation for immediate situational context
- world model for canonical household grounding
- episodic memory for softer and shorter-lived supporting context
- clarification only after those layers fail to resolve the request well enough

---

## Desired Behavior

For household-relative phrases such as:

- `here`
- `home`
- `at the cabin`
- `upstairs`
- `in the office`
- `our car`
- `the kids' calendar`

the runtime should try to resolve meaning from existing context before the agent
responds defensively.

The preferred resolution order should be:

1. explicit reference in the current message
2. explicit reference in very recent conversation context
3. canonical world-model aliases and household defaults
4. relevant episodic or profile context when it materially helps
5. ask the user only if ambiguity is still material

For low-risk requests, the agent should prefer:

- best-effort resolution
- a brief stated assumption when useful
- immediate action or answer

instead of early clarification.

---

## Suggested Design

## 1. Add A Deterministic Resolution Pass Before The Main Agent Run

Before `run_conversation()`, add a lightweight resolution step during context
assembly.

Its job is not full NLP. Its job is to detect common household references and
produce structured candidate bindings such as:

- `resolved_place = cabin`
- `resolved_place_label = Høgevarde`
- `resolved_place_source = world_model.alias:cabin_location`
- `confidence = high`

This should be especially focused on:

- places
- people
- devices
- routines
- household aliases

It should read from the existing context layers in this order:

1. current user message
2. recent conversation turns
3. world-model aliases and canonical entities
4. episodic memory or profiles only when they improve resolution

This would let the runtime inject not only `## Household Model`, but also a
small `## Resolved Context` block for the current message.

Example:

```text
## Resolved Context
- Current likely place reference: cabin -> Høgevarde
- Resolution basis: user said "at the cabin"; matched world-model cabin alias
- Confidence: high
```

This is simpler and more reliable than expecting the model to always infer the
right fact from the whole world-model dump.

---

## 2. Treat Household Alias Resolution As A First-Class Capability

The runtime should explicitly support a "known place / known thing" resolver.

Examples:

- `home` -> primary residence
- `the cabin` -> `cabin_location` or canonical cabin place entity
- `the office` -> known office place alias
- `here` -> the most recently established current place, if confidence is high

This logic should be household-specific and alias-driven, not generic prompt
advice only.

The important design point is:

- household-relative words should not be left entirely to free-form model
  reasoning

They should resolve through canonical data first, then be handed to the model.

---

## 3. Separate "Current Situational Context" From The Long-Lived World Model

The scenario also shows a second issue: "We are at the cabin" is partly a
temporary situational fact, not just a stable world-model fact.

That suggests two layers should work together:

- long-lived canonical household model
- short-lived situational context inferred from the active conversation

The world model should answer: what places exist and what do they mean?

Situational context should answer: which one is likely active right now?

The agent will work better if context assembly can carry forward a few temporary
bindings such as:

- current likely place
- current likely family activity
- current likely target person or device

These should be lightweight and expiring, not permanent model writes.

Given the current system, this does not need a new durable store first.
The immediate source of truth for these temporary bindings should usually be:

- recent turns
- the current message
- optionally relevant `ephemeral` or `normal` episodic memories if they are
  strong enough to help

This is important because the current memory extractor intentionally avoids
persisting many temporary situations automatically. That is a good default and
should remain so.

---

## 4. Add A "Resolve Before Clarify" Agent Rule

Prompt policy should state a simple behavioral rule:

- for household-relative, low-risk questions, first attempt resolution from
  structured context before asking the user to restate known facts

Examples that should usually resolve first:

- weather at home or the cabin
- whether something is cold/warm there
- charging or heating questions tied to a known place
- routine questions tied to known people, calendars, or places

Clarification should still happen when:

- multiple matches are plausible
- the action is high-impact
- the chosen place/entity would materially change the result

This keeps the agent proactive without making it reckless.

---

## 5. Prefer Grounded Tool Inputs Over Free-Text Tool Planning

When the agent decides to use a tool, it should do so from resolved entities when
available.

For example, a weather lookup path should ideally operate on:

- canonical place name
- coordinates or address if available
- known household label

not only on free-text from the user message.

This improves:

- speed
- fewer failed tool attempts
- more stable behavior across phrasing

---

## Recommended V1

The simplest useful improvement is:

1. keep the compact world-model prompt injection
2. add a small deterministic resolver in `assemble_context()`
3. make that resolver read recent turns first, then world-model aliases, then
   softer memory layers
4. inject a compact `## Resolved Context` block per message
5. add a prompt rule to resolve from structured context before clarifying
6. prefer resolved place/device/person inputs when tool calls are prepared

This avoids a large redesign and fits the current architecture well.

---

## Expected Outcomes

If this is done well, HomeAgent should become:

- less defensive in everyday household questions
- better at understanding words like `here`, `home`, and `the cabin`
- more efficient because it uses known household facts first
- more natural because it behaves like it remembers how the household works
- more consistent because canonical structured data wins over ad hoc guessing

In practice, the desired user experience is:

- the agent should feel like it already knows the family's places, routines, and
  references
- and only ask follow-up questions when ambiguity is real, not when knowledge is
  already available

---

## Role Of Episodic Memory

Episodic memory should be part of the solution, but not the primary fix for
this class of issue.

Use it for:

- soft recent household context
- situational but somewhat durable facts
- personal habits or household patterns that help interpretation

Do not rely on it as the first resolver for:

- canonical place meaning
- room/device alias grounding
- household defaults like `home` or `the cabin`

Those should come from the world model.

For a phrase like `here`, the best interpretation should usually come from:

1. recent turn context
2. the current message
3. a world-model-backed place match
4. only then softer memory support

## Non-Goal

This does not require turning the world model into a full reasoning engine.

The point is narrower:

- use existing structured knowledge more deliberately
- resolve common household references before the model stalls
- combine stable world knowledge with short-lived situational context
- make better use of the existing memory layers without overloading episodic
  memory with canonical household meaning

That should be enough to materially improve the behavior seen in the cabin
weather example and similar household conversations.
