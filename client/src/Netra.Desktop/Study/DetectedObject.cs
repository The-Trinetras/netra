namespace Netra.Desktop.Study;

public enum DetectedObjectKind
{
    Heading,
    Figure,
    Table,
    Equation,
}

// One accessible-exploration target within the current reading position.
// AccessibleSummary is what gets announced/read; it must describe the
// object's actual structure (axes, headers, grouping) rather than a
// generated re-explanation — client.md: "Do not regenerate authoritative
// figure labels or equation structure in the client."
public sealed record DetectedObject
{
    public required string ObjectId { get; init; }
    public required DetectedObjectKind Kind { get; init; }
    public required string Label { get; init; }
    public required string AccessibleSummary { get; init; }
}

// No client-visible evidence/multimedia wire contract exists yet (see
// docs/team/handoffs/M5.md Gap 3). This fixture mirrors the AgentSpec §4
// Ohm's Law acceptance fixture (fig02, tbl01, eq01) so the exploration
// INTERACTION — list, select, announce, return — can be built and tested
// now. Every summary here is hand-authored from the spec text, not
// generated, and is explicitly a fixture: real content requires M2/M3 to
// define and expose reading-block/evidence structures to the client.
public static class OhmsLawFixture
{
    public static IReadOnlyList<DetectedObject> DetectedObjects { get; } = new[]
    {
        new DetectedObject
        {
            ObjectId = "fig02",
            Kind = DetectedObjectKind.Figure,
            Label = "Figure 2",
            AccessibleSummary =
                "Graph with current in amperes on the x axis and voltage in volts on the y axis; " +
                "a straight line through the origin shows voltage proportional to current.",
        },
        new DetectedObject
        {
            ObjectId = "tbl01",
            Kind = DetectedObjectKind.Table,
            Label = "Table 1",
            AccessibleSummary =
                "Table with columns Current (A) and Voltage (V), rows: 1 ampere and 2 volts; " +
                "2 amperes and 4 volts; 3 amperes and 6 volts.",
        },
        new DetectedObject
        {
            ObjectId = "eq01",
            Kind = DetectedObjectKind.Equation,
            Label = "Equation 1",
            AccessibleSummary = "V equals I times R.",
        },
    };
}
