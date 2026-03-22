# Security Policy

## Reporting A Vulnerability

Please do not open public GitHub issues for security vulnerabilities.

Until a dedicated security contact is configured, report vulnerabilities
privately to `security@invalid.local` and include:

- affected component
- reproduction steps
- impact assessment
- suggested mitigation if known

## Response Expectations

- We will acknowledge receipt as soon as practical.
- We will assess severity and reproduction.
- We will coordinate a fix and disclosure path before public discussion.

## Scope Notes

The most sensitive surfaces in this repository are:

- provider key handling
- run orchestration and container launch behavior
- environment backend authentication flows
- trace and telemetry exports

If you suspect credential exposure, container breakout risk, or remote code
execution behavior, include that explicitly in the report.
