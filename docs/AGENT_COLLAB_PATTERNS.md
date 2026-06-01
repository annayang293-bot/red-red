# Agent collaboration patterns — proposal

> Author: @Richard (research lane). Pure proposal, no code.
> Trigger: Anna's observation that the team blocks on PM/secretary awake-state (the 13-min
> Cindy stall today, in #无情的码字机:73fad641). Plus the parallel lil-Dev / Richard
> near-duplicate-work episode on `SYSTEM2_DRAFTS_VIEW.md` (commit `871bbd6`) showed the same
> coordination cost at the agent-execution level.

## TL;DR

The root cause isn't runtime ("agent asleep / slow to respond") — it's a **decision-rights
coordination cost** ("which agent owns this call, and what happens if they're not here?"). The
team already has the Slock primitives needed (channels, threads, reactions, tasks, reminders);
what's missing is three small conventions: **(1) channel + per-task thread as default**, **(2)
👀 ack reaction within 60s of being addressed**, **(3) named default-rule with a 5-minute
escalation** when the named owner doesn't respond. **Today-MVP**: post a 5-bullet pinned
convention sheet in #无情的码字机 + adopt 👀/✅/⏰/❓ reactions + tell lil-GUA "if Cindy is
silent 5 min on routing, pick the obvious fast-path and announce; Cindy can revert."

## Diagnosis: what actually broke

Observable in this morning's session:

- **DM-hopping fanout**: Anna ☆ → lil-Anna DM → lil-GUA channel forward → @Cindy ping →
  team waits. Five hops of awake-state dependency, single-threaded.
- **PM gate-keeping deadlock**: lil-GUA's 11:56 forward asked **@Cindy specifically** for the
  workflow-routing decision (full pipeline vs fast path). Cindy hadn't been active in the
  channel; ~13 min stuck. The decision itself was simple — anyone watching could have called
  it — but the routing convention says "Cindy decides workflow," so no one moved.
- **Awake-state opacity**: there's no signal "Cindy is alive right now and will get to this
  in ≈ X." Other agents conservatively wait, which compounds the stall.
- **Top-level vs thread asymmetry**: top-level posts pollute the channel; thread replies are
  organized but easy to miss for agents not subscribed to the thread (Slock daemon ≤ 0.49.x
  had broken thread sends; ≥ 0.52.0 restored, but the muscle memory of "use top-level to be
  safe" lingers). Result: small coordination items get top-level, channel noise grows, signal
  drops.
- **Parallel-execution invisibility (cross-session)**: lil-Dev and I (Richard) both nearly
  produced `SYSTEM2_DRAFTS_VIEW.md` within ~1 minute of each other this morning. No process
  knew the other was running. Caught only because I checked git before overwriting.
  This is the same coordination cost at a different layer (cross-CLI rather than cross-DM).

The pattern across all five: **decisions wait on a specific named owner, with no default
behavior if that owner is absent**. Adding more agents or more pinging doesn't fix it; the fix
is to make "no owner response = a sensible default fires, owner can revert later."

## Pattern menu (the five lil-Anna listed + one I'd add)

Each gets one line: what it solves, what it doesn't.

- **A. Channel-only + per-task thread.** Solves DM-fanout (one place to look) and channel noise
  (threads contain each task). Doesn't solve owner-absent waiting on its own.
- **B. Status reactions** (👀 ack, ✅ done, ⏰ queued, ❓ need clarification). Solves
  awake-state opacity (cheap "I see you" signal), no new messages. Requires convention adoption.
- **C. Task-claim / async queue.** Slock's native `slock task claim` is first-come-first-served
  with one owner; ideal when *any qualified agent* can execute. Doesn't help when a decision
  genuinely requires a specific person (e.g., Anna's IP-direction calls).
- **D. Heartbeat pings ("I'm alive").** High noise, low signal — agents sleep between
  messages by design; an "alive 30s ago" message tells you nothing about now. Don't adopt.
- **E. Cron-triggered wakes / reminders.** Slock has native reminders; good for *recurring*
  work (daily pipeline, weekly review) but doesn't help urgent ad-hoc coordination — reminders
  are author-owned (wake yourself, not someone else).
- **F. Named default + time-boxed escalation** ⭐ (the one I'd add). For any coordination
  decision, codify **(a) who's the default decider, (b) the fallback rule if they're silent
  for N minutes**. The fallback fires automatically; the named decider can revert later. This
  is the single most impactful pattern for the Cindy-deadlock failure mode, because it
  converts "waiting" into "moving with reversible default."

## Recommended bundle (3 conventions, MVP today)

### 1. Channel + per-task thread is the default; DM is the exception
- Public coordination → channel top-level (low volume) OR thread (when a discrete topic
  emerges, open one thread and keep it there).
- DM only for: genuinely 1:1 work assignment ("here's your task, see channel for context"),
  or sensitive info that doesn't belong in channel.
- Rationale: kills the lil-Anna-hub single-point. Anyone in the channel can pick up from any
  thread state by reading once; no fanout needed.

### 2. 👀 reaction within 60s of being @-addressed = "I'm on it"
Convention:
- **👀** = "I see this, I'm acting on it" (ack within 60s of being addressed)
- **✅** = "done" (on the request message, after completion)
- **⏰** = "queued, will get to it within X" (when you can't act immediately)
- **❓** = "I need clarification before I can act"

Absence of 👀 after **2 minutes** from a specific addressee = next person executes default
(per convention 3). The 2-minute window assumes the addressee's daemon is roughly responsive;
if they're genuinely deep in another long task, the default-rule covers it.

### 3. Named default + 5-minute escalation per decision class
Maintain a tiny decision-rights cheat-sheet (lil-Anna owns it, ≤ 10 entries). Each entry has:
**decision class → primary owner → 5-minute fallback rule**.

Starter sheet (Anna/lil-Anna refine):

| Decision class                           | Primary owner | 5-min fallback                                |
|------------------------------------------|---------------|-----------------------------------------------|
| Workflow routing (full vs fast path)     | Cindy         | lil-GUA picks the simpler path; announce it   |
| Topic prioritization / IP direction      | Anna          | (no fallback — Anna only)                     |
| Brief fact-conformance check (novel src) | Richard       | Fu marks "tentative, awaiting Richard" + ship |
| Per-topic step-5 二审                    | Fu            | Alice marks "WIP-no-fact-check"; revisit      |
| Schema / migration design                | lil-Dev       | flag in channel, defer build                  |
| Research lane (tech / competitive)       | Richard       | lil-Anna marks "awaiting Richard" + defer     |
| Carrie-lens / human-taste review         | lil-Carrie    | proceed without; Anna may revisit             |

The pattern: **silence ≤ 5 min on a named call → execute the obvious-default, announce in
thread, named owner can revert when they arrive**. Forward progress beats waiting.

## What NOT to do

- **Heartbeats** (D): noise without signal. Don't.
- **Hub-and-spoke through any single secretary** (today's lil-Anna pattern). lil-Anna is
  excellent as Anna's interface, but coordination across the whole team shouldn't bottleneck
  on a single hub. Channel-first (convention 1) routes around this naturally.
- **Defaulting to DM "to be polite"**: if it's team-relevant, it belongs in channel/thread.
  DMs hide context from anyone who could've helped.
- **Adding more @-mentions when an agent is silent**: doesn't wake them faster, raises noise.
  Wait the 2 min for 👀, then execute the default.

## Trade-offs (one line each)

- **Channel + thread default** — minor channel-volume rise; mitigated by threads containing
  each task. Net less noise than DM-hopping because no per-hop fanout.
- **Reaction convention** — risk of lapsing without enforcement; mitigation = lil-Anna
  reminds gently for first week, then it's muscle memory.
- **Default-rule with 5-min escalation** — risk of executing a wrong default; mitigation =
  conservative defaults (pick the simpler / more-reversible action) + explicit
  "executing default, X can revert" announcement.
- **Decision-rights sheet** — risk of staleness as roles shift; mitigation = lil-Anna owns
  one doc, reviews monthly, updates inline.

## Today-MVP — the 5-bullet pinned convention

What lil-Anna can post in #无情的码字机 right now to start this:

```
📌 Team coord conventions (v1, 2026-06-01)

1. Default to channel + per-task thread. DM only for genuinely 1:1 tasking or sensitive.
2. React 👀 within 60s of being @-addressed = "on it". Use ✅ on done, ⏰ for queued,
   ❓ for needs-clarification.
3. If you @ someone for a decision and they're silent 5 min: execute the obvious-default,
   announce in thread ("executing X in absence of <owner>; <owner> can revert"). The named
   owner can override later.
4. Decision-rights cheat-sheet (who's default-decider per class) lives in
   docs/AGENT_COLLAB_PATTERNS.md §"Named default + 5-min escalation". Add/edit a row when
   a new decision class emerges.
5. Cross-CLI parallel work: before producing any committed artifact, `git fetch && git log
   <path>` to confirm no one beat you to it. (Anti-duplicate-work hygiene.)
```

That's it. Five bullets, no architecture change, today.

## Risks if we don't adopt

- More mornings like today: 13-min PM stalls become predictable; team throughput limited by
  the slowest-to-respond agent on any decision.
- Parallel-execution duplicates: as more agents come online (lil-Dev, Richard, future
  hires), git history fills with "same file written twice" episodes; each one costs ~15-30 min
  of investigation + reconciliation.
- Anna increasingly becomes the unblocker (because she's responsive), defeating the whole
  multi-agent point.

## What this deliberately doesn't decide

- Whether to invest in any *technical* coordination layer (e.g., a shared "current intent"
  log every agent reads on wake). This proposal is convention-only — the lowest-cost
  intervention. Revisit if conventions don't stick after ~2 weeks.
- Whether the decision-rights sheet should live in code (a yaml file the agents auto-read on
  startup) vs in a doc. Start in a doc; promote to code if conventions become load-bearing.
- Cross-runtime coordination at the Slock daemon level (e.g., a "who has my CWD open" lock).
  That's a Slock-platform question, not a team-convention question.
