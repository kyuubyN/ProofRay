# License policy

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
modified network services. No contributor or user should describe Horizon
Memory as “non-commercial software.”

Commercial use is permitted when its applicable license is followed. Separate
commercial licenses may be offered later, but they do not revoke rights already
received under an open-source release.

The project has not adopted a RAIL-style license (a license family that
attaches behavioral use restrictions, common for ML models, and not
OSI-approved open source). `RESPONSIBLE_USE.md` states real misuse concerns
as an ask, not as a license term, for the same reason `AI_TRAINING_POLICY.md`
declines to smuggle a training restriction into the AGPL: this project will
not mislabel a restricted license as free software.
