# DRAFT — Horizon OpenRAIL-R (not yet adopted)

Modeled specifically on the BigScience/Hugging Face **OpenRAIL** family
(OpenRAIL-M for models, OpenRAIL-D for datasets) — this is the **retrieval**
variant of that same template, "R" for retrieval, adapted for a search/
memory engine rather than a generative model. It reuses OpenRAIL's actual
mechanic (a copyleft-style source grant plus a binding attachment of named
prohibited uses, breach of which terminates the grant) rather than inventing
a new structure from scratch.

**Status: draft for review. Not a legal document yet. Not signed off by a
lawyer. Does not apply to anything already released — code already published
under `AGPL-3.0-or-later` keeps that license for the copies already
distributed; a license cannot be revoked retroactively for recipients who
already received it under the current terms.** This document exists so the
actual decision — whether and how to adopt it for future releases — can be
made deliberately, with something concrete in front of it, not decided by a
markdown file appearing in a commit.

**The repository is currently private.** Nothing here is urgent in the sense
of a public release racing against someone else's — there is no public
audience yet to protect or to be exposed to. The point of drafting this now,
before the repo opens, is to have the license question already settled
*before* the first public release, rather than retrofitting restrictions
onto users who already received an unrestricted grant.

## Why this exists

Horizon Memory started with the intent of being open infrastructure in the
spirit of Linux: free, unrestricted, judged only by its usefulness. What
changed is the recognition that the underlying mechanism is not just another
retrieval tool — it is a different approach to a capability (precise,
provenance-verified search that outperforms an LLM reader at a small byte
budget) with real dual-use weight, most acutely as a tool for persistent,
provenance-tracked *memory* of people and events. The choice is not "open vs.
restricted" in the abstract; it is closer to the one BigScience/Hugging Face
faced with OpenRAIL: when the capability itself is powerful enough to matter,
release it with named, binding limits on its worst uses from the start,
rather than release it unrestricted first and add limits later, after an
unrestricted grant already went out. The repository being private today is
exactly what makes that ordering possible.

## What this license is meant to preserve

1. **Source availability stays real**, in the spirit of the AGPL choice
   already made: anyone who receives the software, including as a modified
   network service, receives its corresponding source. This draft is not
   about closing the source — it adds a behavioral condition on top of an
   otherwise still-copyleft grant.
2. **The restriction is a named list of uses, not a purpose whitelist.** A
   clause limiting the software to "the purpose it was created for" is
   unworkable and would forbid legitimate uses nobody anticipated at release
   time. What can be defined, and enforced, is a list of specific,
   recognizable *bad-faith or high-harm* uses — the same shape OpenRAIL uses.

## Attachment: Use Restrictions (draft)

You may not use Horizon Memory, or a modified version of it, for any of the
following:

1. **Mass or covert surveillance** of individuals or groups without a lawful
   basis, independent oversight and, where required by applicable law,
   informed consent.
2. **Non-consensual profiling or tracking** of an identifiable person,
   including inferring protected characteristics (health, biometric data,
   political or religious belief, sexual orientation, criminal history)
   without a lawful basis.
3. **Autonomous weapons or military targeting systems** — any use in the
   design, targeting logic, or operation of a system intended to select or
   engage targets for physical harm without meaningful human control at the
   point of decision.
4. **Unreviewed high-stakes automated decisions** about a specific person —
   law enforcement, immigration, credit, employment, benefits eligibility, or
   medical diagnosis or treatment — without meaningful, qualified human
   review of that specific decision.
5. **Circumventing a data subject's legal rights** (access, correction,
   erasure — e.g. under LGPD or GDPR) by design, including building the
   system specifically to make a lawful erasure or access request
   impossible or impractical to honor.
6. **Large-scale disinformation** — generating or distributing content
   designed to deceive the public at scale about a material fact.
7. **Unlawful discrimination** — using the system to deny a person a legal
   right, benefit, employment or economic opportunity because of a protected
   characteristic.
8. Any use that is **unlawful** under the law applicable to the deployer.

A violation of this Attachment terminates the license grant for the
violating party, for that use, the same enforcement mechanic OpenRAIL uses:
this is framed as a condition of the license, not a separate promise.

## What this draft does not do

- It does not touch code already distributed under `AGPL-3.0-or-later`.
- It is not a substitute for `DISCLAIMER.md`'s liability position — a
  restriction on use is a different legal instrument from a warranty
  disclaimer, and both are still needed.
- It is not final. Turning this into the actual governing license for future
  releases needs, at minimum: a decision on whether it *replaces* AGPL going
  forward or is offered *alongside* it (dual-licensing), a decision on the
  effective version/date, a real legal review of the exact restriction
  wording (several of the categories above use terms — "meaningful human
  review," "lawful basis" — that a lawyer should tighten before this has real
  teeth), and, if adopted, updating every file's SPDX header, the `LICENSE`
  file, `LICENSE_POLICY.md` and `README.md` together in one deliberate
  change, not piecemeal.

## Open decisions, for you

- Replace AGPL going forward, or dual-license (AGPL for those who don't need
  the restriction removed, RAIL-style for a broader default)?
- Since the repo is private, there is no forced deadline: adopt this before
  the repo ever goes public, or tie it to a specific milestone (e.g. v1.0,
  once the V1 accuracy gate is met)?
- Who reviews the restriction wording before it is treated as binding —
  arranged now, while there is no public release pressure, or closer to
  whenever going public is actually decided?
