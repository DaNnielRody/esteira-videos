# ADR 0001 — Manim Community runtime

- **Status:** accepted
- **Date:** 2026-08-28
- **Affected contexts:** Project foundation

## Context

The first tracer bullet must execute generated Python through Manim Community,
and the host provides Python 3.13.9. The upstream ecosystem contains two
incompatible packages: Manim Community (`manim`) and 3Blue1Brown's ManimGL
(`manimgl`). Generated imports, CLI flags, and output layout depend on this
choice, so source alone would not explain a future switch.

Manim Community 0.21.0 declares Python 3.11+ and classifiers through Python
3.14. A repository-local spike observed successful Cairo rendering on Python
3.13.9 and produced a valid H.264 MP4. This repository's milestone explicitly
requires Manim Community.

## Alternatives and trade-offs

- **Manim Community 0.21.0** — matches the requested engine, has current
  documentation and Python 3.13 support, but generated code must target the
  Community API and its CLI/output conventions.
- **3Blue1Brown ManimGL** — remains the creator's actively maintained engine
  and a useful design reference, but uses a distinct `manimgl` package and
  runtime contract that would violate the requested Community renderer.
- **Manim Community 0.19.0** — matches the 2026 RITL paper's experimental
  engine, but needlessly pins an older API when 0.21.0 is locally proven and
  supports the same core scene primitives.

## Decision

Pin Manim Community `0.21.0` for the MVP and generate `from manim import ...`
scenes for the Cairo renderer. Keep Manim behind a subprocess so a future
runtime change does not leak into the Scene Spec or provider interface.

## Consequences

- Python 3.13.9 is supported and has an observed real-render path.
- Prompts and tests must name Manim Community 0.21.0 explicitly.
- The package carries Manim's native dependency footprint in the project-local
  environment.
- Reopen this decision if a security/support issue requires another release or
  a required animation cannot be expressed against the pinned Community API.
