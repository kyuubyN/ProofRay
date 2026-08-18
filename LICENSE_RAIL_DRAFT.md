# DRAFT — Horizon OpenRAIL-R (not yet adopted)

Modeled specifically on the BigScience/Hugging Face **OpenRAIL** family
(OpenRAIL-M for models, OpenRAIL-D for datasets) — this is the **retrieval**
variant of that same template, "R" for retrieval, adapted for a search/
memory engine rather than a generative model. It reuses OpenRAIL's actual
mechanic (a copyleft-style source grant plus a binding attachment of named
prohibited uses, breach of which terminates the grant) rather than inventing
a new structure from scratch.

**Status: draft for review. Not a legal document yet. Not signed off by a
lawyer.** The earlier version of this note warned that a license cannot be
revoked retroactively for recipients who already received the code under
`AGPL-3.0-or-later` — that caveat assumed public distribution that hasn't
actually happened. **The repository is private, and the sole author is its
sole copyright holder; nobody outside that has received the code yet, so
there is no existing third party with a vested AGPL grant to protect.**
Nothing here is staged for "future releases only" — it can be adopted
directly, whenever the wording is ready, with no legacy grant to work
around. The moment that stops being true — a collaborator gets access, the
repo goes public, anyone outside the author receives a copy — is the moment
this reasoning needs to be rechecked, not before.

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

## Explicitly permitted uses (draft)

The Attachment below names what is prohibited; it should not be read as
leaving everything else in doubt. The following are explicitly permitted,
stated affirmatively so a good-faith user never has to guess:

1. **Personal and educational use** — learning, teaching, coursework,
   personal projects, research and academic study.
2. **Community and civil-society projects** — nonprofits, cooperatives,
   civic-tech and public-interest initiatives, including by volunteers and
   small organizations without dedicated legal review.
3. **Cultural and heritage preservation** — archives, libraries, museums and
   similar institutions using it to organize, retrieve or preserve
   provenance-tracked cultural or historical material.
4. **Accessibility** — adapting or integrating it to make information or
   services usable by people with disabilities.

These categories do not override Attachment restriction 1–8 below — a
surveillance use does not become permitted by labeling it "educational."
They exist so the restrictions are read as narrow and named, not as a
general license to interrogate every use case.

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

- It is not a substitute for `DISCLAIMER.md`'s liability position — a
  restriction on use is a different legal instrument from a warranty
  disclaimer, and both are still needed.
- It is not final on wording. Several categories in the Attachment use terms
  a lawyer should tighten before they carry real weight in a dispute —
  "meaningful human review," "lawful basis" — this is about precision, not
  about waiting for a release milestone.
- Adopting it means updating every file's SPDX header, `LICENSE`,
  `LICENSE_POLICY.md` and `README.md` together in one change, not piecemeal —
  a mechanical step, not a legal one, and doable as soon as the wording above
  is settled.

## Open decisions, for you

- Replace AGPL outright, or keep AGPL available as a second option for users
  who don't need the restriction (dual-licensing)?
- Is the wording above ready to adopt as written, or does it need a lawyer's
  pass first? Nothing forces a choice here except wanting it right.
