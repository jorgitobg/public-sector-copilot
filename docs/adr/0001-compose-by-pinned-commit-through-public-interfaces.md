# ADR 0001: Compose components by pinned commit through their public interfaces

Status: accepted (2026-11-06)

The four components live in separate repositories and are installed from Git at exact commit SHAs, not from a branch. A new commit upstream cannot change this demo's behaviour, and the output in the README stays reproducible. The cost is that bumping a component is an explicit change here, which is what we want to review.

Each component is used through the boundary it was designed for: a library call where it is a library, the MCP protocol where it is a server, HTTP where it is a service. Nothing imports another component's internals beyond its documented entry points. If a seam is awkward, that is feedback for the component, not a reason to reach in.

Rejected: a monorepo (it would hide the seams this demo exists to show) and floating dependencies (non-reproducible output).
