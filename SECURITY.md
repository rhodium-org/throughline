# Security Policy

throughline is a local command-line tool that reads and writes files in a project
directory and is intended to run in developer environments and CI. It has no
network service and no runtime authentication surface, so its attack surface is
small — but reports are still welcome.

## Supported versions

throughline is pre-1.0 (alpha). Only the latest `main` is supported; fixes land
there.

## Reporting a vulnerability

Please report suspected vulnerabilities **privately**, not via a public issue:

- Preferred: GitHub's [private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
  ("Report a vulnerability" under the repository's *Security* tab), or
- Email **henry.grechcini@gmail.com** with details and, if possible, a minimal
  reproduction.

Please give us a reasonable window to investigate and release a fix before any
public disclosure. We will acknowledge your report, keep you updated, and credit
you (if you wish) once a fix is available.

## Things worth reporting

- A crafted project file that causes throughline to write outside the project
  directory, execute code, or crash unsafely.
- Any way `tl check` can pass a graph that violates a documented invariant (a
  false "green" is a correctness *and* trust issue for a CI gate).
