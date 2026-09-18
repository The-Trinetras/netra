namespace Netra.Desktop.Library;

// One numbered discovery result. client.md: "present numbered results and
// retain the exact selected result." Ordinal is 1-based to match how a
// screen-reader user would say "open the third one."
public sealed record VideoDiscoveryResult
{
    public required int Ordinal { get; init; }
    public required string Title { get; init; }
    public required string Lecturer { get; init; }
    public required string VideoId { get; init; }
}

// No discovery wire payload exists in shared/contracts/ (see
// docs/team/handoffs/M5.md Gap 2) — Tavily sits behind a server-side
// adapter per runtime-baseline.md, and this pass must not call it directly
// or invent its response shape as a contract. This interface is this pass's
// PROPOSED client-facing shape, not an approval.
public interface IVideoDiscoveryService
{
    Task<IReadOnlyList<VideoDiscoveryResult>> SearchAsync(string query, CancellationToken cancellationToken);
}

// Fixture double returning a fixed, clearly-labelled result set so the
// numbered-selection UI/state can be built and tested now. Never calls
// Tavily or any network endpoint.
public sealed class FixtureVideoDiscoveryService : IVideoDiscoveryService
{
    public Task<IReadOnlyList<VideoDiscoveryResult>> SearchAsync(string query, CancellationToken cancellationToken)
    {
        IReadOnlyList<VideoDiscoveryResult> results = new[]
        {
            new VideoDiscoveryResult { Ordinal = 1, Title = "Ohm's Law — lecture-v1 (fixture)", Lecturer = "Fixture Lecturer", VideoId = "fixture-lecture-v1" },
            new VideoDiscoveryResult { Ordinal = 2, Title = "Series and Parallel Resistance (fixture)", Lecturer = "Fixture Lecturer", VideoId = "fixture-lecture-v2" },
        };
        return Task.FromResult(results);
    }
}
