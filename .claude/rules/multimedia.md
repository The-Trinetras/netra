---
paths:
  - "api/**/multimedia/**"
  - "worker/src/netra_worker/jobs/multimedia/**"
  - "**/adapters/twelvelabs/**"
---

# Multimedia rules

Owner: M3 Multimedia.
Apply CLAUDE.md and backend-data.md for shared worker/persistence mechanics.
Multimedia components are bounded tools, services, adapters and workflows.
Do not create a visual, video, math or extraction agent.

## Boundaries and contracts

Own observed figure structure, diagrams, equations and video evidence.
Twelve Labs integrations remain behind the assigned provider adapters.
Preserve Marengo/Pegasus responsibilities and approved configuration.
Do not replace providers, choose new models or introduce frameworks for convenience.
Use the runtime baseline and the selected SDK version's interfaces.

Consume authorized source references through content services.
Do not create another ingestion database, source authority or job queue.
Return Netra-owned contract types, not provider SDK objects.

For data crossing an established service/process/client boundary, use the
existing shared contract schemas and do not invent incompatible wire fields.

Internal multimedia domain models may use Netra-owned typed structures when
no wire contract is required.

If a required cross-boundary schema is missing, report it as a blocker rather
than inventing a protocol field to accommodate a provider response.

## Figures and diagrams

Separate directly observed structure from generated interpretation.
Preserve purpose, parts, relationships, labels, units and source references.
Use stable part IDs so navigation does not depend on regenerated prose.
Keep diagram connectivity distinct from visual proximity.
Preserve chart axes, scales, series and exact values where actually available.
Mark estimated values and unreadable labels explicitly.
Never invent a label, relationship, coordinate or measurement.

Support layered descriptions: overview, parts, relationships and detail.
Navigation through an established structure is deterministic application logic.
Return content that M5 can expose through keyboard and accessible controls.
Do not implement WPF navigation or Tutor teaching policy in this workstream.

## Equations and tables

Preserve the equation source crop and source version.
Retain extracted notation, structured math representation and validation status.
Successful syntax conversion does not prove the source was read correctly.
Check signs, exponents, fractions, grouping and units against the source.
Unverified extraction must not become a confident authoritative derivation.

Math-part navigation follows a deterministic structure.
Raw LaTeX is not automatically accessible mathematical braille.
Do not claim braille compatibility without the tested client/reader/device path.
Do not add a conversion framework or runtime absent from approved dependencies.

Represent table cells with their headers, spans and source mapping.
Preserve exact values and structure separately from a summary.
Do not flatten a table into prose when the task needs cell relationships.

## Video and provider work

Keep authorized video identity and timestamp ranges with every evidence item.
A discovered video URL does not prove permission or provider ingestibility.
Use Marengo for its planned retrieval/embedding role and Pegasus for description.
Keep provider asset/index IDs distinct from canonical Netra source IDs.
Track model/version and provenance for reproducible evidence interpretation.

Long processing runs as leased PostgreSQL jobs through the shared worker.
Persist remote operation identity and completed-stage artifacts.
Respect cancellation, remaining deadlines, quota and idempotency policy.
Do not perform external calls inside long database transactions.
Do not publish an incomplete or failed asset as validated evidence.
Return explicit unavailable/unsupported/uncertain outcomes through contracts.

## Verification focus

Use source-checked fixtures for labels, relationships, signs, units and timestamps.
Check that repeated processing preserves canonical references and navigation IDs.
Include malformed responses, unreadable assets, timeout and replay cases.
Coordinate accessible exploration review with M5 and factual teaching review with M4.
Do not substitute text-only evaluation for inspection of the original visual.
Scaffold tasks use fixtures/stubs; live media/provider calls require explicit request.
