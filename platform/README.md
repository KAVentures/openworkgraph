# OpenWorkGraph platform

This directory contains deployment and fleet-operational assets rather than application runtime packages.

- [deploy/](deploy/) — self-hosted Gateway/PostgreSQL Docker deployment.
- [enterprise/](enterprise/) — managed endpoint rollout, browser policy templates, fleet health, signing and acceptance material.
- [distribution/](distribution/) — source-side release/helper launchers; release artifacts keep their established user-facing filenames.

Keeping these under one platform boundary prevents deployment mechanics from appearing as peer product subsystems at repository root. Source-tree organization does not change the generated release layouts or customer-facing install contracts.
