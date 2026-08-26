# Benchmark status and claim boundaries

This is the public, current reading guide. It answers one question first:
**what can the current ProofRay release honestly claim?**

For the full chronology, frozen artifacts, superseded experiments and negative
results, see the [research archive](benchmarks/HISTORICAL_RESEARCH_ARCHIVE.md).
That archive is preserved for reproducibility; it is not a release scorecard.

## Public Alpha: current claims

- **Release surface:** Linux x86_64, packaged as an AppImage. The Linux native
  acceptance flow (first launch, encrypted local memory, recall marker and
  restart) and the common Python, Flutter and connector contracts run in CI.
- **Authority contract:** a direct answer requires reopenable authorized
  evidence. Otherwise ProofRay returns evidence, conflict or abstention. This
  is a design and test contract, not a promise that all natural-language
  questions can be compiled today.
- **Models are optional writers/readers, never proof authority.** A model can
  phrase an already-authorized result, but cannot turn unsupported memory into
  a proved answer.
- **Windows and Android:** experimental ports only. Their hosted feasibility
  builds are manual diagnostics, not release checks or support claims, until
  they have owner-controlled device validation and distributable artifacts.

## What the numbers mean

Do not compare values across rows without their protocol label. In particular:

| Surface | Current evidence | What it does *not* establish |
|---|---|---|
| Structured causal execution | Exact reopenable execution on frozen, typed inputs | Universal natural-language understanding |
| LoCoMo personal recall | 90.77% annotated-turn hit at the measured high-recall cut | Final-answer accuracy or an independent cohort result |
| MemGym EOP | 6/120 closed proof dossiers in a runtime-only consumed-development replay | MemGym answer accuracy |
| LongMemEval reader/composer | A consumed-development paired judge compared plain 0.7750 with proof-first 0.9375 | Current universal accuracy or an independent holdout result |

The LongMemEval **0.7750** is not a score for the current engine. It is the
plain-composer comparator in that one 120-output consumed-development paired
run. Its proof-first counterpart was 0.9375 under the same frozen judge
protocol. Subsequent independent slices rejected treating either development
number as an end-to-end generalization claim. Therefore neither number is a
public product headline.

## Protocol labels

Every result in this repository belongs to one of these classes:

- **Release validation:** current build, current configuration and an
  executable acceptance or contract check. This is the only class that can
  support a platform-release statement.
- **Independent evaluation:** a preregistered, previously unopened cohort.
  It may support a narrow claim only at its evaluated boundary.
- **Consumed development:** public or previously inspected data used to make
  design decisions. Useful engineering evidence, never independent proof.
- **Diagnostic/history:** reopened holdouts, pilots, ablations, rejected
  hypotheses and old snapshots. Kept to explain decisions, never promoted by
  proximity to newer code.

## Where to read next

- [Consumed-development memory benchmarks](benchmarks/CONSUMED_DEVELOPMENT.md)
  — LoCoMo, LongMemEval and MemGym, with the exact boundaries.
- [Historical research archive](benchmarks/HISTORICAL_RESEARCH_ARCHIVE.md)
  — all older protocols, raw tables, diagnostics and preserved negatives.
- [Release gates for the native app](../apps/proofray/docs/RELEASE_GATES.md)
  — platform-specific evidence and remaining requirements.

## Reporting rule

When discussing ProofRay publicly, name the **mechanism**, **dataset status**,
**metric** and **denominator** together. For example: “90.77% LoCoMo
annotated-turn hit on consumed development at the high-recall candidate cut,”
not “90.77% memory accuracy.” If the required proof cannot close, report the
abstention or evidence state rather than folding it into answer accuracy.
