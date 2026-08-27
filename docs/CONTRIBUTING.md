# Contributing

The project is not accepting public contributions during the private alpha.
This file defines the policy intended for the first public release.

Contributions must include tests, preserve provenance and abstention semantics,
and distinguish measured results from hypotheses. Do not submit confidential
information, benchmark answer keys, personal data or code you cannot license.

## Propose before you build: RFC issues

Anything beyond a trivial fix (a typo, a broken link, a small docs
correction, a one-line bug fix with an obvious root cause) starts as an
**RFC issue**, not a pull request. Open one with the
[RFC issue template](../.github/ISSUE_TEMPLATE/rfc.md), describing the problem,
the proposed approach, and its scope/limits — the same discipline this
project already applies to its own research claims (see `RESEARCH.md`).
Discussion happens on the issue first; a pull request is opened only after
the approach is accepted there, and its description must link back to the
RFC issue it implements. A pull request that skips this for a non-trivial
change will be redirected back to an issue rather than reviewed as-is.

## Review and merge policy

Nothing merges into `main` without an explicit human approval — there is no
auto-merge on this repository, and branch protection enforces required
review even for the founding maintainer's own pull requests (see
`.github/CODEOWNERS`). A pull request must also pass CI
(`.github/workflows/ci.yml`) and have every review conversation resolved
before it can merge.

External contributors agree to [`CLA.md`](CLA.md) — a license grant covering
the AGPL core, the Apache-dual-licensed `adapters/` files, and any future
commercial license, plus a Developer Certificate of Origin-style certification
that they have the right to submit their contribution. It is checked
automatically on an external contributor's first pull request
(`.github/workflows/cla.yml`) and only needs signing once.

The CLA process does not apply to the project's founder or internal
maintainers acting in that capacity. They remain subject to the repository's
license, review, security and provenance rules. The workflow allowlist is
maintained only for internal maintainer GitHub accounts appointed by the
founder; it does not confer maintainer status or create a copyright assignment,
equity, compensation, revenue share or commercial-license arrangement. Any
such agreement must be separate, express and signed by the relevant parties.
Contributions to dual-licensed adapter files are submitted under that file's
existing license expression; other code is submitted under AGPL-3.0-or-later.

**Signing the CLA does not grant governance rights, maintainership, voting
power, equity or employment** — see [`GOVERNANCE.md`](GOVERNANCE.md) for how
technical decisions are actually made; contributing code never changes that
by itself.

Authorship remains visible in Git history and release notes. Contribution does
not authorize claims that a contributor or employer created the original
ProofRay architecture and its historical Horizon lineage.
