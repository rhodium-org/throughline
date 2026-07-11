# Contributing to throughline

Thanks for your interest in throughline. This guide gets you to a working, checked
development environment.

## Set up your environment

```bash
git clone https://github.com/rhodium-org/throughline.git
cd throughline
python -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
python -m pip install -e '.[dev]'
```

## Verify your environment with the doctor

Run the contributor doctor. It diagnoses your setup and, per check, reports
either a pass or a specific fix — exiting non-zero if anything is not ready:

```bash
python scripts/doctor.py
```

It checks:

- Python version (>= 3.11)
- an importable / installed `throughline` and its CLI on `PATH`
- the `pytest` test runner
- whether the local grounding gate (pre-commit hook) is wired
- that the grounding gate passes for the self-hosted requirements and the demo

To wire the local pre-commit grounding hook automatically:

```bash
python scripts/doctor.py --fix
```

When every check passes, your environment is ready to contribute.

> `scripts/doctor.py` and the pre-commit configuration are contributor tooling
> maintained in this repository. They are not part of the shipped `throughline`
> package and may be environment-specific.

## The grounding gate

throughline manages its own requirements with itself. The grounding gate rejects an
ungrounded or otherwise invalid requirements graph:

```bash
tl -C requirements check --strict
```

The pre-commit hook runs this automatically on commit for both the self-hosted
requirements and the demo graph, so a bad graph cannot be committed. Install it
once with `python scripts/doctor.py --fix` (or `pre-commit install`).

## Run the tests

```bash
python -m pytest
```

## Making changes

- Follow Intent-Driven Development: write or update the grounded requirement
  (a `draft`) before implementing, then move it to `approved` once built.
- Keep the requirements graph grounded — the gate must stay green.
- Ensure `python scripts/doctor.py` and `python -m pytest` both pass before you
  open a pull request.

## Where to start

throughline is early software with plenty of well-scoped work available:

- **[`ROADMAP.md`](ROADMAP.md)** lists what's built, what's not, and a *Known rough
  edges* section of small, real, self-contained fixes — a good first PR.
- Issues labelled `good first issue` or `help wanted`.
- Using throughline on a real requirements set and reporting friction. A clear bug
  report or "the model fought me here" write-up is a genuine contribution.

New to the workflow? [`HOW_TO_USE.md`](HOW_TO_USE.md) is a fifteen-minute hands-on
start, and [`HOW_IDD_DIFFERS_FROM_BDD.md`](HOW_IDD_DIFFERS_FROM_BDD.md) explains the
discipline the project is built on.

## Licensing of contributions

throughline is released under the **Apache License 2.0** ([`LICENSE`](LICENSE)). By
submitting a contribution you agree it is licensed under those same terms, per
section 5 of the licence, unless you arrange otherwise with the maintainers. Please
keep the SPDX header (`# SPDX-License-Identifier: Apache-2.0`) on new source files.

Everyone participating is expected to follow the
[`Code of Conduct`](CODE_OF_CONDUCT.md).
