<!--
Thanks for contributing to throughline!

By submitting this pull request you agree that your contribution is licensed under
the project's Apache License 2.0 (see LICENSE), per section 5 of that licence.
-->

## What & why

<!-- What does this change and, more importantly, why? Link the issue or roadmap item. -->

Closes #

## Intent-Driven Development checklist

- [ ] The grounded requirement was written/updated *before* the implementation
      (or this change needs none — e.g. docs/tooling).
- [ ] `tl -C requirements check --strict` is green (the self-host grounding gate).
- [ ] `tl -C examples/grounding-demo check --strict` is green.
- [ ] `python -m pytest` passes.
- [ ] `python scripts/doctor.py` passes (contributor environment is healthy).

## Notes for reviewers

<!-- Anything worth calling out: trade-offs, follow-ups, things you're unsure about. -->
