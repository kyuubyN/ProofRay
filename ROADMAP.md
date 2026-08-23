# Roadmap

This page says where the project is headed, not exactly when it'll get there. Horizon only ever
claims a capability once it's been built and independently tested. See
[Benchmarks](docs/BENCHMARKS.md) for what's actually validated today, and treat everything below
as direction, not a promise or a release date.

## More languages

Today, the parts of Horizon that read natural-language questions (not just store and retrieve raw
facts) have been carefully tested in **English and Portuguese**, each cleared through its own real
test against text the corresponding mechanism had never seen before. **Chinese support is next**,
and we intend to bring it in as soon as it's ready to meet that same bar, not before. Every language
after that follows the same rule: no "supports language X" claim ships without its own independent
test, the same way English and Portuguese were each proven on their own, not assumed to transfer
from the other.

## Better answers, not just better search

Some of our own testing has already shown that simply finding more of the right information isn't
always what limits a correct final answer; sometimes the harder part is putting several found
facts together correctly. Expect continued work here: not just retrieving evidence faster or more
precisely, but improving what happens after the right evidence has already been found.

## Easier to run

Today, using Horizon means running Python. A packaged, no-install version (a plain binary you can
just double-click, with an installer for your operating system) is a direction we want to go,
along with a small local interface for people who'd rather not write code at all. Feasibility notes
for this specific effort already exist in
[HorizonAI Engine's own roadmap](HorizonAI%20Engine/ROADMAP.md) for anyone curious about the
technical shape of that work.

## Connecting directly to your data

Right now, connecting Horizon to a database means writing a small amount of your own code to pull
rows out and hand them over (see the tutorial's own database examples). A more direct path,
pointing Horizon at a database connection and letting it take it from there, is a real, larger
piece of work we'd like to take on, not a small checkbox.

## Beyond one operator, one machine

Horizon's current security model is deliberately scoped to a single operator running it on their
own machine. Supporting a real multi-user or team deployment, with its own separate access model,
is a direction being considered for later, once the simpler case is solid.

## A note on scope

This list is intentionally general. We're an early-stage project and don't want to lock ourselves
into specifics that might change as we learn more, or take away the surprise of what's actually
coming.
