# License policy

## The model: a free kernel, adapters are your distribution

ProofRay is meant to work the way the Linux kernel does. The kernel
itself (`src/horizon_memory/`, excluding `adapters/`) is free software under
**AGPL-3.0-or-later** — always free, always source-available, including for a
modified version offered as a network service. Anyone can build a
distribution, product or business on top of it, the same way Ubuntu, Fedora
and Red Hat build on the Linux kernel rather than on a fork of it.

`src/horizon_memory/adapters/` is that build point. Every file there carries
the SPDX expression `Apache-2.0 OR AGPL-3.0-or-later`: pick whichever license
suits your integration. Apache-2.0 has no copyleft/network-source obligation,
so a closed-source product, a hosted SaaS, or a proprietary adapter connecting
ProofRay to your own systems can use and extend those specific files without
being required to publish its own source — exactly the freedom Ubuntu has to
ship proprietary tools alongside the GPL kernel it's built on. This dual
license applies **only** to files that carry that exact SPDX header; it is
never a way to relicense the engine itself, and a new adapter you write does
not automatically inherit it unless you choose to add the same header.

This repository uses a deliberate split:

1. Unless a file states otherwise, code is licensed under
   `AGPL-3.0-or-later`.
2. Files under `src/horizon_memory/adapters/` that carry the SPDX expression
   `Apache-2.0 OR AGPL-3.0-or-later` may be used under either license.
3. Project names, marks and logos are governed by `TRADEMARKS.md`, not by the
   software licenses.
4. The visual identity under `assets/` is not licensed under AGPL or Apache;
   see `assets/README.md`.
5. Third-party components, if added, retain their own notices and licenses.

The Apache option exists only at the integration boundary. It must not be read
as relicensing the engine or experimental retrieval implementation.

The AGPL is a free-software license and does not forbid commercial use or a
particular field of use. It protects source availability, including for
modified network services. No contributor or user should describe ProofRay as
“non-commercial software.”

Commercial use is permitted when its applicable license is followed. Separate
commercial licenses may be offered later, but they do not revoke rights already
received under an open-source release.

The project considered and decided against a RAIL-style license (a license
family that attaches binding behavioral use restrictions, common for ML
models, and not OSI-approved open source). The reasoning: a restricted
license adds exactly the kind of adoption friction and legal complexity the
Linux-kernel model above depends on not having. `RESPONSIBLE_USE.md` instead
states real misuse concerns plainly, as an ask
and not a license term, for the same reason `AI_TRAINING_POLICY.md` declines
to smuggle a training restriction into the AGPL: this project will not
mislabel a restricted license as free software, and won't quietly make the
license more restrictive to get the same effect either.
