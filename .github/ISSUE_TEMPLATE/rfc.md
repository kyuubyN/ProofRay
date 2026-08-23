---
name: RFC (propose a change before building it)
about: Required first step for anything beyond a trivial fix -- see CONTRIBUTING.md
title: "RFC: "
labels: rfc
assignees: ""
---

<!--
Per CONTRIBUTING.md's "Propose before you build" policy: a trivial fix (typo, broken link, small
docs correction, a one-line bug fix with an obvious root cause) can skip this and go straight to a
pull request. Anything else -- a new feature, a behavior change, a new dependency, a change to a
public API, a change to license/governance/CI policy -- starts here. Discussion happens on this
issue; a pull request is opened only once the approach below is accepted, and its description must
link back to this issue.
-->

## Problem

What is broken, missing, or worth doing, and for whom? Link to a concrete failure, gap or request
if one exists -- not a vague sense that something could be better.

## Proposed approach

What you'd actually build or change. Be concrete enough that someone could disagree with a
specific part of it, not just the general direction.

## Scope and limits

What this explicitly does **not** do, and what it might break, cost, or regress. Every claim of
"this works" needs an honest boundary next to it -- the same discipline `RESEARCH.md`'s own
benchmark reports already require.

## Alternatives considered

What else was considered and why it wasn't chosen, if anything.

## Open questions

Anything you're genuinely unsure about and want input on before building.
