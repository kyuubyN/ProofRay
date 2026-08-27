# Contributor License Agreement

This is plain-language project policy, not a substitute for legal advice —
before relying on it for a real dispute, have it reviewed by counsel in your
own jurisdiction, the same caveat this repository already gives for
`LICENSE_POLICY.md` and `RESPONSIBLE_USE.md`.

## Why this exists

`CONTRIBUTING.md` previously asked only for a Developer Certificate of Origin
(DCO) `Signed-off-by` line. A DCO certifies you have the right to contribute
your code under the project's stated license — it does **not** grant the
maintainer any license broader than that. `LICENSE_POLICY.md` already commits
this project to a specific model: the core stays AGPL-3.0-or-later forever,
`adapters/` stays Apache-2.0-or-AGPL dual-licensed, and a **separate
commercial license may be offered later** without revoking rights already
granted under the open-source release. That last option cannot work safely
with DCO alone: if ten different people each hold copyright over their own
patch with no broader grant, the maintainer cannot unilaterally offer a
commercial license containing their combined work without asking every one of
them for consent, every time. This document closes that gap, once, up front,
instead of leaving it to be untangled retroactively.

## What signing this does not do

**Signing this agreement grants a license. It does not grant governance,
voting power, maintainership, equity or employment.** Those are covered
entirely by `GOVERNANCE.md`, are not created by contributing code under any
circumstance, and are not for sale or negotiation via this document. The
founding role and technical-decision process described there apply
regardless of who has signed this CLA or how much they have contributed.

## Who must sign

This CLA applies to **external contributors**: people or organizations
submitting a contribution without acting as a project maintainer. It does not
apply to the project's founder or its co-founders when they contribute in their
maintainer capacity. Their work is governed by their founding/maintainer role
and the repository's applicable license policy, rather than by the
external-contributor CLA process.

This is an operational and governance exemption only. It does **not** create
or presume a copyright assignment, equity, revenue share, compensation,
commercial-license grant or other financial arrangement between maintainers.
Any such term requires a separate, express agreement signed by the relevant
parties; it must not be inferred from this repository document or the CLA
allowlist.

That exemption does not reduce the standard for an internal change. Founders,
co-founders and other maintainers must still have the right to submit their
work, preserve the applicable license expression and follow the same review,
security and provenance rules. Conversely, co-authorship, a collaborator title
or access to a discussion does not by itself make someone a maintainer or
exempt an external contribution from this CLA.

## What you keep

You keep full copyright ownership and authorship of your own contribution.
This is a license grant, not a copyright assignment — nothing here transfers
ownership of your work to the project or its maintainers. Your authorship
remains visible in Git history, `AUTHORS.md`/`CONTRIBUTORS.md` and release
notes, exactly as `CONTRIBUTING.md` already promises.

## What you grant

For any contribution you submit to this project, you grant Kaue Oliveira
Costa (kyuubyN), as founding maintainer, and the project:

1. A perpetual, worldwide, non-exclusive, royalty-free, irrevocable copyright
   license to use, reproduce, prepare derivative works of, publicly display,
   publicly perform, sublicense and distribute your contribution and such
   derivative works, **under `AGPL-3.0-or-later`, under `Apache-2.0` for
   files that carry that dual-license header, and under any future
   commercial license the project offers** — consistent with, and never
   exceeding, the scope `LICENSE_POLICY.md` already describes.
2. A perpetual, worldwide, non-exclusive, royalty-free, irrevocable
   (except as stated below) patent license to make, have made, use, offer to
   sell, sell, import and otherwise transfer your contribution, for patent
   claims you own or control that are necessarily infringed by your
   contribution alone or in combination with the project it was submitted
   to. If anyone institutes patent litigation alleging that the project or a
   contribution infringes a patent, any patent licenses granted to that
   party under this agreement terminate as of the date the litigation is
   filed — the same defensive-termination shape Apache-2.0 itself uses.

## Certification (replaces the standalone DCO requirement)

By submitting a contribution, you certify, for that contribution:

1. It was created in whole or in part by you, and you have the right to
   submit it under the license(s) named above; or
2. It is based on prior work that, to your knowledge, is covered under an
   appropriate open-source license, and you have the right under that
   license to submit it with modifications; or
3. It was provided to you by someone who certified (1) or (2) and you have
   not modified it; and
4. You understand this contribution is public and that a record of it
   (including your name and the license/certification above) is maintained
   indefinitely.

This certification text is adapted from the Developer Certificate of Origin
1.1 — contributors who prefer a plain `Signed-off-by` line already understand
its substance; this document simply pairs that certification with the
license grant `LICENSE_POLICY.md`'s own commercial-licensing option requires.

## How this is recorded

A pull request from a first-time external contributor is checked automatically
(see `.github/workflows/cla.yml`); signing once covers every future external
contribution from that same account unless this document changes materially, in
which case a re-signature is requested. The workflow's maintainer allowlist is
an operational mirror of official GitHub maintainer accounts and must be kept
in sync when a co-founder is granted or loses maintainer status; it is not a
way to grant that status. Signatures are recorded in
`.github/cla-signatures.json` in this repository — a public, append-only
record, not a private database.

## Scope

This agreement applies to external contributions submitted once the project
accepts public contributions. `CONTRIBUTING.md` states the current status and
the maintainer/external boundary.
