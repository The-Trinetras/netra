# P3 — media label review worksheet (M3-LBL-1)

**Status: not reviewed.** The 13 labels in
`api/tests/multimedia/fixtures/evaluation/m3_media_labels_v1.json` (version
v1) were written from the synthetic Ohm's-law fixture
(`api/tests/multimedia/fixtures/ohm_law.py`). No original PDF page or lecture
frame has been compared with them, so they are not gold. This worksheet is
generated from that file; the file stays the source of truth.

**How to review (Arun, with the permitted original PDF and lecture):** for
each case, find the figure, table, equation or lecture moment in the
original, compare it with the "Expected" and "Reference answer" columns, and
fill in the last three columns. Mark a case **matches**, **differs** (write the
correction exactly as the original shows it: signs, units, axis names, cell
values, timestamps) or **cannot check** (say why). Do not accept a label
because it looks plausible; a text judge score cannot establish what an image
shows.

When every case is filled in, the label file gets `label_reviewer` and
`original_media_reviewed: true`, corrections are applied, and the version is
bumped (v2) in one reviewed change; M4 then re-imports it.

| Case | Object | Variant | Expected | Failure class | Reference answer | Original located at (page/figure/time) | Result | Correction or reason |
|---|---|---|---|---|---|---|---|---|
| `m3-graph-axes-ok` | chart | {} | {"source_verified": true} | none | The graph plots Current in amperes on the x axis and Voltage in volts on the y axis. | | | |
| `m3-graph-axes-unreadable` | chart | {"unreadable_axes": true} | {"source_verified": false, "unreadable": true} | source_limitation | The axis labels cannot be read in the source, so the graph's quantities cannot be stated from it. | | | |
| `m3-graph-wrong-unit` | chart | {"y_unit": "mV"} | {"source_verified": false, "mismatch_fields": ["unit"]} | extraction_error | The y axis is Voltage in volts (V), not millivolts. | | | |
| `m3-graph-swapped-axes` | chart | {"swap_axes": true} | {"source_verified": false} | extraction_error | Current is on the x axis and Voltage on the y axis. | | | |
| `m3-table-cells-ok` | table | {} | {"source_verified": true} | none | The table lists (1 A, 2 V), (2 A, 4 V) and (3 A, 6 V). | | | |
| `m3-table-wrong-unit` | table | {"voltage_unit": "mV"} | {"source_verified": false, "mismatch_fields": ["unit"]} | extraction_error | The Voltage column is in volts (V). | | | |
| `m3-table-missing-row` | table | {"drop_row": 2} | {"source_verified": false, "mismatch_fields": ["presence"]} | extraction_error | The table has a row where current is 2 A and voltage is 4 V. | | | |
| `m3-equation-ok` | equation | {} | {"source_verified": true} | none | V equals I times R. | | | |
| `m3-equation-wrong-operator` | equation | {"operator": "÷", "spoken_operator": "divided by"} | {"source_verified": false, "mismatch_fields": ["canonical_form"]} | extraction_error | The equation multiplies I by R; it does not divide. | | | |
| `m3-lecture-visual-at-00-48` | lecture_moment | {"captured_player_time_ms": 48000, "items": ["transcript", "visual"], "window_before_ms": 5000, "window_after_ms": 5000} | {"sufficiency": "visual", "visual_claim_eligibility": "pending_decision"} | none | At 00:48 the lecturer points at the straight line on the voltage-current graph and says the resistance stays constant. | | | |
| `m3-lecture-transcript-only-at-00-48` | lecture_moment | {"captured_player_time_ms": 48000, "items": ["transcript"], "window_before_ms": 5000, "window_after_ms": 5000} | {"sufficiency": "transcript_only", "visual_claim_eligibility": "citation_only"} | evidence_gap | The transcript says the resistance stays constant, but it does not establish what the graph's axes show; the axes must come from the figure. | | | |
| `m3-lecture-wrong-timestamp` | lecture_moment | {"captured_player_time_ms": 10000, "items": ["transcript", "visual"], "window_before_ms": 5000, "window_after_ms": 5000} | {"sufficiency": "no_evidence_at_time"} | evidence_gap | No processed evidence covers 00:10; the explanation must not borrow the 00:48 moment. | | | |
| `m3-lecture-rejected-video` | video_processing | {"index_status": "failed"} | {"error_code": "provider_rejected_media", "retryable": false} | evidence_gap | The video could not be analysed, so no visual evidence is available for it; playback availability is a separate check. | | | |

Reviewer: ________  Date: ________  Original media (title, version,
permission): ________
