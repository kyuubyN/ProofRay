# Responsible use

ProofRay is a persistent, provenance-tracking memory substrate: it is
designed to keep facts, and their sources, available indefinitely, and to
retrieve them with precision. That shape carries a specific misuse risk that
a generic software security policy does not cover — covert surveillance,
non-consensual profiling and long-term tracking of individuals — and it is
treated here as its own concern, not folded into `SECURITY.md`.

## What this document is, and is not

This is a serious statement of what the project intends ProofRay to be
used for, and what it does not want it used for — in the same spirit as
`AI_TRAINING_POLICY.md`. **It does not add a field-of-use restriction to the
AGPL software license.** The AGPL remains the complete license for the
engine; its permissions cannot honestly be narrowed by a document like this
one. The project considered a RAIL-style license with binding use
restrictions and decided against it: ProofRay is meant to work the way
Linux does — a free core that anyone builds on top of, the way Ubuntu,
Fedora and Red Hat build on the Linux kernel — and a restricted license adds
exactly the adoption friction and legal complexity that model depends on
not having. This document exists so the project's intent is stated plainly
instead, without pretending it has legal teeth it does not have.

## What it is for

- **Personal and educational use** — learning, teaching, coursework,
  personal projects, research and academic study.
- **Community and civil-society projects** — nonprofits, cooperatives,
  civic-tech and public-interest initiatives, including by volunteers and
  small organizations without dedicated legal review.
- **Cultural and heritage preservation** — archives, libraries, museums and
  similar institutions organizing, retrieving or preserving cultural or
  historical material with provenance tracking.
- **Accessibility** — adapting or integrating it to make information or
  services usable by people with disabilities.
- **Commercial use and redistribution** — building products, services or
  independent distributions on top of the core. This is intentional, not a
  grudging exception: the Linux-distribution model is the actual goal.

## What it is not for

- **Covert or mass surveillance** of individuals or groups without a lawful
  basis, independent oversight and, where required by law, informed consent.
- **Non-consensual profiling or tracking** of an identifiable person,
  including inferring protected characteristics (health, biometric data,
  political or religious belief, sexual orientation, criminal history)
  without a lawful basis.
- **Autonomous weapons or military targeting** — any use in the design,
  targeting logic or operation of a system meant to select or engage targets
  for physical harm without meaningful human control at the point of
  decision.
- **Unreviewed high-stakes automated decisions** about a specific person —
  law enforcement, immigration, credit, employment, benefits eligibility, or
  medical diagnosis or treatment — without meaningful human review of that
  decision.
- **Circumventing a data subject's rights** (access, correction, erasure —
  e.g. under LGPD or GDPR) by design, including building the system
  specifically to make a lawful request impossible or impractical to honor.
- **Large-scale disinformation** — generating or distributing content
  designed to deceive the public at scale about a material fact.
- **Unlawful discrimination** — denying a person a legal right, benefit,
  employment or economic opportunity because of a protected characteristic.
- Any other use that is **unlawful** under the law applicable to the
  deployer.

Labeling a use "educational" or "commercial" does not move it out of the
list above — a surveillance deployment is not exempted by what it's called.

## Why this is a statement of intent, not an enforcement mechanism

Provenance and verifiability are the engine's actual, technical guarantees —
that a retrieved fact is exactly what was stored and where it came from.
Whether the underlying data collection was lawful, consented to or
proportionate is a deployment-time decision this project cannot see or
control, the same position `DISCLAIMER.md` already takes for outputs and
downstream decisions generally. Naming the intended and unwanted uses here,
plainly and specifically, rather than leaving them implicit, is the honest
middle ground available without adding license-level restrictions: real
enough to say clearly, not strong enough to claim as a technical or legal
safeguard. Deployers remain responsible for their own compliance; see
`DISCLAIMER.md` for the liability position this document does not replace.
