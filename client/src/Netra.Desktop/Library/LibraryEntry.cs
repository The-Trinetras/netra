namespace Netra.Desktop.Library;

// Status of a locally-selected source as it moves toward being study-ready.
// "Processing"/"Ready"/"Failed" must only ever be driven by an actual
// preparation result (fixture or real) — never asserted at selection time,
// per client.md: "announce meaningful processing changes" and current-scope.md:
// "announce readiness only for capabilities actually available."
public enum LibrarySourceStatus
{
    Selected,
    Processing,
    Ready,
    Failed,
}

public sealed class LibraryEntry
{
    public required Guid EntryId { get; init; }
    public required string FileName { get; init; }
    public required string FullPath { get; init; }
    public LibrarySourceStatus Status { get; set; } = LibrarySourceStatus.Selected;

    // True whenever Status was reached through a fixture/local double rather
    // than an authorized server response. Never silently promoted to false;
    // client.md: "fixture mode must be explicit and never pretend a server
    // mutation succeeded."
    public bool IsFixtureSourced { get; set; } = true;
}
