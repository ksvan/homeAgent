# Personalization Questionnaire Design

Status: draft for user completion
Last code check: 2026-06-29
Runtime targets: profiles, household world model, episodic memory, prompt harness

## Purpose

This questionnaire is designed to tune HomeAgent toward Kristian and the
household without changing the underlying LLM. The expected workflow is:

1. Kristian answers the questions in this file.
2. The answers are reviewed and converted into:
   - structured user and household profile entries
   - household world-model entities, aliases, routines, goals, activities, and facts
   - episodic memories for soft preferences and recurring patterns
   - compact prompt-harness guidance for behavior that should always apply

The questions below were selected for high runtime value. Each question should
produce information that changes at least one of these agent behaviors:

- avoids repeat clarification
- improves tool choice or timing
- improves household coordination
- improves tone and response shape
- makes proactive behavior safer or more useful
- creates better memory extraction examples for future conversations

## Answering Guidance

Answer with concrete examples where possible. The most valuable answers name
real people, rooms, routines, devices, calendars, constraints, and failure modes.
Short answers are fine when the preference is simple.

Use "do not store" beside anything that should inform this review but should not
be persisted into memory or the world model.

## Extraction Legend

- `Profile`: small fact worth injecting every run.
- `World model`: canonical household entity, alias, routine, activity, goal, or fact.
- `Episodic memory`: soft preference, pattern, or situational nuance retrieved by relevance.
- `Prompt harness`: compact behavioral rule that should apply globally.

## Questions

### Communication Style

1. What character or relationship model fits how you want the agent to feel day-to-day?
   - Why this matters: The fundamental persona the agent projects shapes every interaction — it is not the same as answer length or tone.
   - Extract to: `Profile`, `Prompt harness`.
   - Good answer includes: a character model such as professional assistant, knowledgeable peer, or warm familiar helper; when to be formal versus casual; and whether personality, dry humor, or opinions are welcome.
   - Answer:

2. When you ask the agent for help, what is the ideal default answer length and shape?
   - Why this matters: Calibrates everyday verbosity and avoids irritating over-explanation.
   - Extract to: `Profile`, `Prompt harness`.
   - Good answer includes: examples of a good one-line answer, a good structured answer, and a too-long answer.
   - Answer:

3. What kinds of replies make you trust the agent more, and what kinds make you trust it less?
   - Why this matters: Tunes confidence, uncertainty handling, and how the agent reports tool results.
   - Extract to: `Episodic memory`, `Prompt harness`.
   - Good answer includes: phrases or behaviors to prefer and avoid.
   - Answer:

4. When the agent is uncertain, should it ask a clarifying question, make a conservative assumption, or present options?
   - Why this matters: Sets the house style for ambiguity resolution.
   - Extract to: `Profile`, `Prompt harness`.
   - Good answer includes: separate rules for low-risk tasks, scheduling/logistics, and side effects.
   - Answer:

5. What tone should the agent use with you when you are stressed, busy, or coordinating family logistics?
   - Why this matters: These are high-value moments where tone and brevity matter most.
   - Extract to: `Episodic memory`, `Prompt harness`.
   - Good answer includes: examples of helpful and unhelpful wording.
   - Answer:

6. Should the agent adapt its style when you seem stressed, tired, or short, or should it stay consistent regardless of your apparent state?
   - Why this matters: Knowing whether to read emotional context prevents both tone mismatches and unwanted emotional commentary.
   - Extract to: `Episodic memory`, `Prompt harness`.
   - Good answer includes: whether adaptation is welcome, how far it should go, whether the agent may ever ask how you are, and whether it should mention what it noticed or just quietly adjust.
   - Answer:

7. Which language or language mix should the agent use with each household member?
   - Why this matters: Language choice belongs in always-available context and member grounding.
   - Extract to: `Profile`, `World model`.
   - Good answer includes: member names, preferred language, and when to switch.
   - Answer:

8. Are there topics where you want the agent to be extra direct, extra gentle, or avoid commentary?
   - Why this matters: Prevents tone mismatches around sensitive or repeated household topics.
   - Extract to: `Episodic memory`, `Prompt harness`.
   - Good answer includes: topic, preferred stance, and example phrasing.
   - Answer:

### Decision Style And Autonomy

9. What should the agent usually do when a request is underspecified but low risk?
   - Why this matters: Reduces needless back-and-forth for common tasks.
   - Extract to: `Prompt harness`, `Episodic memory`.
   - Good answer includes: examples such as reminders, simple searches, lights, or summaries.
   - Answer:

10. What decisions may the agent make autonomously for you or the household?
    - Why this matters: Defines the useful autonomy envelope beyond the default policy gate.
    - Extract to: `Profile`, `World model`, `Prompt harness`.
    - Good answer includes: allowed domains, limits, and examples.
    - Answer:

11. What decisions should the agent never make without asking first, even if technically low risk?
    - Why this matters: Captures household-specific consent boundaries.
    - Extract to: `Profile`, `Prompt harness`.
    - Good answer includes: examples involving family coordination, purchases, communications, or device behavior.
    - Answer:

12. When the agent has several plausible options, how should it rank them?
    - Why this matters: Tunes planning toward your real priorities.
    - Extract to: `Profile`, `Episodic memory`.
    - Good answer includes: tradeoffs such as time, family calm, cost, energy, reliability, comfort, privacy, and effort.
    - Answer:

13. How should the agent handle mistakes it made or actions that failed?
    - Why this matters: Improves recovery behavior and reduces frustrating apology loops.
    - Extract to: `Prompt harness`, `Episodic memory`.
    - Good answer includes: desired apology level, diagnostics, retry policy, and when to escalate to you.
    - Answer:

14. What is your tolerance for proactive suggestions?
    - Why this matters: Helps distinguish useful proactivity from engagement-seeking noise.
    - Extract to: `Profile`, `Prompt harness`.
    - Good answer includes: examples of suggestions you want, suggestions you dislike, and quiet periods.
    - Answer:

15. Are there domains where you want the agent to challenge your thinking or flag concerns even when you have not asked, and domains where you want it to just execute?
    - Why this matters: Determines whether the agent acts as a pure executor or a peer who volunteers scrutiny — and keeps that calibrated per domain.
    - Extract to: `Profile`, `Prompt harness`.
    - Good answer includes: domains where pushback is welcome, domains where you are the expert and want no commentary, and how hard the agent should press before dropping it.
    - Answer:

### Household Structure And People

16. Who are the household members the agent should know about, and what names or aliases do they use?
    - Why this matters: Grounds memory scoping, calendars, routines, and coordination.
    - Extract to: `World model`, `Profile`.
    - Good answer includes: canonical names, nicknames, roles, and active/inactive status.
    - Answer:

17. What relationships or responsibilities matter for household coordination?
    - Why this matters: Helps the agent infer who to ask, notify, or consider in plans.
    - Extract to: `World model`.
    - Good answer includes: caregiver roles, transport responsibility, school/activity responsibility, pet or home responsibilities.
    - Answer:

18. What should stay private between household members, and should the agent treat conversations with each member as confidential by default?
    - Why this matters: The agent speaks to multiple household members; without a clear privacy model it can inadvertently share information that was meant to be individual.
    - Extract to: `World model`, `Prompt harness`.
    - Good answer includes: whether member conversations are private by default or shared freely, any explicit exceptions, whether children read messages and what content rules follow, and whether the agent may ever proactively message one member about something another said.
    - Answer:

19. What are each household member's recurring interests, activities, and goals?
    - Why this matters: Gives the world model useful hooks for calendars, planning, and proactive reminders.
    - Extract to: `World model`.
    - Good answer includes: member name, interest/activity/goal, schedule hints, seasonality, and importance.
    - Answer:

20. Are there important non-household people the agent should recognize?
    - Why this matters: Prevents confusion when messages mention grandparents, friends, neighbors, teachers, coaches, or helpers.
    - Extract to: `World model`, `Episodic memory`.
    - Good answer includes: name, relationship, aliases, and relevance.
    - Answer:

21. What household facts are so stable and important that the agent should almost always know them?
    - Why this matters: Identifies always-injected profile facts rather than relevance-based memories.
    - Extract to: `Profile`, `World model`.
    - Good answer includes: location, timezone, household defaults, recurring constraints, and durable family context.
    - Answer:

22. What health, dietary, or physical considerations should the agent factor into recommendations for any household member?
    - Why this matters: Allergies, dietary restrictions, and physical constraints directly affect recommendations for food, travel, activities, and purchases — and getting these wrong can matter.
    - Extract to: `World model`, `Profile`.
    - Good answer includes: per-member dietary restrictions or allergies, physical considerations relevant to recommendations, and whether this information may be shared across members when relevant.
    - Answer:

23. What does a genuinely good week look like for your household, and what is the most common thing that derails it?
    - Why this matters: Understanding what a good week means for this household lets the agent infer priorities across planning, proactive suggestions, and how to frame options — rather than using a generic utility function.
    - Extract to: `Profile`, `Episodic memory`, `World model`.
    - Good answer includes: what makes a week feel successful, the most common disruptors, and which household members' wellbeing sets the overall household tone.
    - Answer:

### Home, Devices, And Routines

24. What are the real household place names, aliases, and ambiguous room names?
    - Why this matters: Improves device, calendar, and routine disambiguation.
    - Extract to: `World model`.
    - Good answer includes: canonical room/floor names, Norwegian/English names, nicknames, and common ambiguous references.
    - Answer:

25. Which smart-home devices have names that are misleading, overloaded, or need special interpretation?
    - Why this matters: Prevents unsafe or wrong tool calls.
    - Extract to: `World model`.
    - Good answer includes: device name, actual purpose, place, aliases, and cautions.
    - Answer:

26. What device actions should be considered routine and low-risk in your home?
    - Why this matters: Tunes the action/confirmation boundary inside your real household norms.
    - Extract to: `World model`, `Prompt harness`.
    - Good answer includes: action, scope, allowed times, and exceptions.
    - Answer:

27. What device actions are high-impact or annoying enough that the agent should be conservative?
    - Why this matters: Household-specific safety and comfort rules may be stricter than the generic policy.
    - Extract to: `World model`, `Prompt harness`.
    - Good answer includes: devices, zones, actions, confirmation requirement, and why.
    - Answer:

28. What named home routines or modes should the agent understand operationally?
    - Why this matters: Converts fuzzy requests like "goodnight" into grounded plans.
    - Extract to: `World model`.
    - Good answer includes: routine name, aliases, desired actions, exclusions, and confirmation level.
    - Answer:

29. What are the household's normal daily and weekly rhythms?
    - Why this matters: Helps with timing, proactive reminders, and interpreting "normal."
    - Extract to: `World model`, `Episodic memory`.
    - Good answer includes: wake/sleep windows, school/work patterns, meal patterns, activity days, and exceptions.
    - Answer:

30. What should the agent know about quiet hours, sleep, guests, illness, travel, or other context that changes how it should act?
    - Why this matters: These contexts should suppress or soften otherwise normal automation.
    - Extract to: `World model`, `Episodic memory`, `Prompt harness`.
    - Good answer includes: condition, detection clues, behavior changes, and whether to ask first.
    - Answer:

### Planning, Logistics, And Calendar Use

31. Which calendars exist, who owns them, and how should the agent interpret them?
    - Why this matters: Grounds calendar filtering, member linking, and schedule summaries.
    - Extract to: `World model`.
    - Good answer includes: calendar names, owners, categories, aliases, reliability, and events to ignore.
    - Answer:

32. What kinds of schedule conflicts or logistics risks should the agent proactively surface?
    - Why this matters: Defines high-signal proactive calendar behavior.
    - Extract to: `Profile`, `World model`, `Prompt harness`.
    - Good answer includes: conflict types, lead time, severity threshold, and who should be notified.
    - Answer:

33. How should the agent help with transport, pickups, drop-offs, and travel time?
    - Why this matters: Family logistics often need assumptions, buffers, and role awareness.
    - Extract to: `World model`, `Episodic memory`.
    - Good answer includes: default transport modes, buffer preferences, responsible people, and known locations.
    - Answer:

34. What recurring planning tasks should the agent watch for or help initiate?
    - Why this matters: Finds candidates for scheduled prompts, event rules, or task workflows.
    - Extract to: `World model`, `Episodic memory`, `Prompt harness`.
    - Good answer includes: task, frequency, trigger, desired output, and when silence is preferred.
    - Answer:

35. What does a good morning briefing, evening summary, or weekly family digest look like for you?
    - Why this matters: Converts proactive summaries into a useful format instead of a generic digest.
    - Extract to: `Profile`, `Prompt harness`, `World model`.
    - Good answer includes: sections, length, ordering, delivery time, suppression rules, and examples.
    - Answer:

36. How should the agent handle reminders and follow-ups so they are useful rather than noisy?
    - Why this matters: Tunes persistence, reminder timing, and escalation.
    - Extract to: `Profile`, `Episodic memory`, `Prompt harness`.
    - Good answer includes: first reminder timing, repeat policy, maximum nudges, and wording.
    - Answer:

### Memory And Personalization Boundaries

37. What should the agent always remember about your working style and priorities?
    - Why this matters: These are likely user-profile facts that should not depend on semantic retrieval.
    - Extract to: `Profile`.
    - Good answer includes: stable preferences for work, family, information density, and decision making.
    - Answer:

38. What kinds of things should the agent remember only softly, because they may change?
    - Why this matters: Helps classify memories as normal or ephemeral instead of permanent.
    - Extract to: `Episodic memory`.
    - Good answer includes: examples and approximate expiry expectations.
    - Answer:

39. What should the agent avoid remembering unless you explicitly say "remember this"?
    - Why this matters: Creates privacy and sensitivity boundaries for memory extraction.
    - Extract to: `Prompt harness`.
    - Good answer includes: topics, people, health/finance/privacy boundaries, and exceptions.
    - Answer:

40. What facts about the household should be canonical structured facts rather than loose memories?
    - Why this matters: Prevents important structure from being lost in episodic retrieval.
    - Extract to: `World model`.
    - Good answer includes: entities, aliases, routines, device meanings, responsibilities, and calendars.
    - Answer:

41. How should the agent behave when a stored memory seems outdated or contradicts what you just said?
    - Why this matters: Tunes conflict handling and memory correction.
    - Extract to: `Prompt harness`, `Episodic memory`.
    - Good answer includes: whether to ask, update immediately, forget old memory, or mention the conflict.
    - Answer:

### Information, Research, And Recommendations

42. For factual questions, when should the agent answer from memory versus verify with tools or external sources?
    - Why this matters: Sets your trust bar for stale or high-impact information.
    - Extract to: `Profile`, `Prompt harness`.
    - Good answer includes: domains requiring verification, acceptable uncertainty, and citation expectations.
    - Answer:

43. How do you want recommendations ranked for purchases, travel, restaurants, activities, or household decisions?
    - Why this matters: Makes recommendations reflect your actual utility function.
    - Extract to: `Profile`, `Episodic memory`.
    - Good answer includes: budget sensitivity, quality bar, convenience, kid-friendliness, sustainability, aesthetics, and risk tolerance.
    - Answer:

44. What sources, brands, services, or local context does your household prefer or avoid?
    - Why this matters: Saves repeated filtering and improves local recommendations.
    - Extract to: `Profile`, `Episodic memory`, `World model`.
    - Good answer includes: preferred stores/services, avoided sources, local geography, memberships, and reasons.
    - Answer:

45. What is the spending threshold above which the agent should flag the cost, ask before proceeding, or decline to assist directly?
    - Why this matters: Sets a clear line between execute, flag, and escalate for any action or recommendation with financial side effects.
    - Extract to: `Profile`, `Prompt harness`.
    - Good answer includes: a flag-and-note threshold, an ask-first threshold, and whether different limits apply for household versus personal spending.
    - Answer:

### Failure Modes And Guardrails

46. Are there actions the agent should refuse even if you instruct it in the moment?
    - Why this matters: Some behaviors must be locked out permanently — these are not consent gates but hard limits that should hold regardless of context or phrasing.
    - Extract to: `Prompt harness`.
    - Good answer includes: specific actions, side effects, or topics that are always off-limits, and a short reason for each so the agent can recognize edge-case variants.
    - Answer:

47. What are the top ways the agent could be annoying, unsafe, or counterproductive in your home?
    - Why this matters: Directly identifies guardrails that matter more than generic assistant behavior.
    - Extract to: `Prompt harness`, `Episodic memory`.
    - Good answer includes: concrete failure modes, severity, and desired alternative behavior.
    - Answer:

48. If you could add five "house rules" to the agent's harness, what would they be?
    - Why this matters: Forces prioritization into compact prompt-quality rules.
    - Extract to: `Prompt harness`, plus `Profile` or `World model` when specific.
    - Good answer includes: short imperative rules, examples, and whether each rule applies to everyone or only you.
    - Answer:

## Post-Answer Analysis Plan

After the answers are filled in, analyze them in four passes:

1. Profile pass:
   - Extract small, stable, always-useful facts.
   - Prefer terse key/value facts such as `communication_style`, `preferred_language`,
     `autonomy_default`, `recommendation_priorities`, and `verification_threshold`.

2. World-model pass:
   - Extract members, aliases, places, device meanings, routines, activities,
     goals, relationships, calendars, and durable household facts.
   - Keep canonical names and aliases explicit.
   - Mark risky or inferred changes as proposals instead of auto-applying.

3. Episodic-memory pass:
   - Extract soft preferences and situational patterns as complete standalone
     sentences.
   - Classify importance deliberately:
     - `critical`: safety or privacy guardrails, permanent household config
     - `important`: strong recurring preferences and long-lived routines
     - `normal`: useful but ordinary preferences
     - `ephemeral`: preferences or situations likely to expire

4. Prompt-harness pass:
   - Extract only rules that should apply across many interactions.
   - Keep them compact enough for always-included prompt files.
   - Prefer decision rules over biographies.

## Quality Checks

Before storing or editing prompts, verify:

- The same fact is not stored in multiple layers unless each layer has a distinct purpose.
- Structured household facts go to the world model, not episodic memory.
- Always-needed facts stay compact enough for profiles.
- Prompt-harness additions are general rules, not one-off memories.
- Sensitive facts are not persisted unless the answer clearly permits it.
- Ambiguous answers become follow-up questions instead of overconfident memories.
