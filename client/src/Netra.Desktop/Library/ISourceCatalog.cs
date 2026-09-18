using System.Net.Http;
using System.Threading;
using Netra.Desktop.Networking;
using Netra.Desktop.Protocol.Dto;
using Netra.Desktop.State;

namespace Netra.Desktop.Library;

// The student's sources as the server reports them, and explicit opening
// (pinning) of one ready version for study. Opening never uploads anything;
// a newly activated version never moves a session implicitly.
public sealed record CatalogSource(string SourceId, string Title, string? ActiveSourceVersionId, int? ActiveVersionNumber)
{
    public bool CanOpen => ActiveSourceVersionId is not null;

    // What a screen reader announces for the list item.
    public string AccessibleLabel => ActiveVersionNumber is { } version
        ? $"{Title}, version {version}, ready to study"
        : $"{Title}, not ready to study";

    public override string ToString() => AccessibleLabel;
}

public interface ISourceCatalog
{
    Task<IReadOnlyList<CatalogSource>> ListAsync(CancellationToken cancellationToken);

    // Returns the server's snapshot after the pin (or the recorded one on a replay).
    Task<SessionSnapshotPayload> OpenAsync(CatalogSource source, CancellationToken cancellationToken);
}

// Everything the library needs to use real server sources. Resynchronize
// asks the server for a fresh session.snapshot (session.resume over the open
// WebSocket) after a version conflict; it may be null when no socket exists.
public sealed record LibraryServerAccess(
    ISourceCatalog Catalog,
    ClientSessionState SessionState,
    Func<CancellationToken, Task>? Resynchronize = null);

public sealed class ApiSourceCatalog : ISourceCatalog
{
    private readonly INetraApi _api;
    private readonly ClientSessionState _sessionState;

    public ApiSourceCatalog(INetraApi api, ClientSessionState sessionState)
    {
        _api = api;
        _sessionState = sessionState;
    }

    public async Task<IReadOnlyList<CatalogSource>> ListAsync(CancellationToken cancellationToken)
    {
        var sources = await _api.ListSourcesAsync(_sessionState.SessionId, cancellationToken).ConfigureAwait(false);
        return sources
            .Select(s => new CatalogSource(s.SourceId, s.Title, s.ActiveSourceVersionId, s.ActiveVersionNumber))
            .ToList();
    }

    // One logical action = one request id. A transport failure before any
    // response is retried once under the SAME request id and expected
    // version, so the server replays the recorded pin instead of applying a
    // second one. Typed server errors are never retried here.
    public async Task<SessionSnapshotPayload> OpenAsync(CatalogSource source, CancellationToken cancellationToken)
    {
        if (source.ActiveSourceVersionId is not { } version)
        {
            throw new InvalidOperationException("This source has no ready version to open.");
        }

        var requestId = Guid.NewGuid();
        var expected = _sessionState.SessionVersion;
        ApiSourceSelected selected;
        try
        {
            selected = await _api.SelectSourceAsync(_sessionState.SessionId, requestId, version, expected, cancellationToken)
                .ConfigureAwait(false);
        }
        catch (HttpRequestException)
        {
            selected = await _api.SelectSourceAsync(_sessionState.SessionId, requestId, version, expected, cancellationToken)
                .ConfigureAwait(false);
        }

        return selected.Snapshot;
    }
}
