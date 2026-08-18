# Responsible use

Horizon Memory is a persistent, provenance-tracking memory substrate: it is
designed to keep facts, and their sources, available indefinitely. That shape
carries a specific misuse risk that a generic software security policy does
not cover — covert surveillance, non-consensual profiling and long-term
tracking of individuals — and it is treated here as its own concern, not
folded into `SECURITY.md`.

## What this document is, and is not

This is a statement of the project's values and asks, in the same spirit as
`AI_TRAINING_POLICY.md`. **It does not add a field-of-use restriction to the
AGPL software license.** The AGPL remains the complete license for the
engine; its permissions cannot honestly be narrowed by a document like this
one. A legally enforceable use restriction would require a non-open-source
license and specialist legal drafting — for example a RAIL-style license with
attached use restrictions, which is a real, different licensing family from
the AGPL and is not OSI-approved open source. The project has not adopted
one, and will not describe this policy as if it had. If that trade-off is
ever made deliberately, it will be a separate, explicit licensing decision,
not a quiet addition to this file.

## Asks

Operators and integrators of Horizon Memory are asked to:

- Not deploy it for covert or non-consensual surveillance of individuals, or
  to build profiles of people without a lawful basis and, where required,
  their informed consent.
- Honor data-subject rights that apply to the underlying data under
  applicable law (for example LGPD in Brazil, GDPR in the EU) — access,
  correction and erasure — even though the storage engine itself has no
  built-in concept of a data-subject request and will not silently comply
  with one on its own.
- Not use it as the sole, unreviewed basis for a high-stakes automated
  decision about a person (law enforcement, immigration, credit, employment,
  benefits eligibility, medical judgment) without meaningful human review.
- Avoid aggregating sensitive categories of data (health, biometric,
  criminal history, political or religious belief, sexual orientation) beyond
  the specific, disclosed purpose of the deployment collecting it.
- Not use it to enable unlawful discrimination.
- Publish their own responsible-use or privacy policy when Horizon Memory is
  part of a product that stores information about identifiable people.

## Why this is an ask and not an enforcement mechanism

Provenance and verifiability are the engine's actual guarantees — that a
retrieved fact is exactly what was stored and where it came from. Whether the
underlying data collection was lawful, consented to, or proportionate is a
deployment-time decision this project cannot see or control, the same
position `DISCLAIMER.md` already takes for outputs and downstream decisions
generally. Naming the specific risk here, rather than leaving it implicit,
is the honest middle ground: real enough to say plainly, not strong enough
to claim as a technical or legal safeguard.
