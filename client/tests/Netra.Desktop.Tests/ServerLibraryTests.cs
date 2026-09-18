using System.Net.Http;
using System.Threading;
using Netra.Desktop.Library;
using Netra.Desktop.Networking;
using Netra.Desktop.Protocol.Dto;
using Netra.Desktop.State;
using Netra.Desktop.ViewModels;
using Xunit;

namespace Netra.Desktop.Tests;

// Listing and explicitly opening the account's server sources: retry
// identity, version handling and accessible status text.
public sealed class ServerLibraryTests
{
    private static readonly CatalogSource Ready = new("s-1", "Ohm's law chapter", "v-1", 2);
    private static readonly CatalogSource Parsing = new("s-2", "Still parsing", null, null);

    [Fact]
    public async Task Open_RetriesATransportFailureOnceUnderTheSameRequestIdAndVersion()
    {
        var (api, state) = Build(sessionVersion: 4);
        api.SelectFailures.Enqueue(new HttpRequestException("connection reset"));
        var catalog = new ApiSourceCatalog(api, state);

        await catalog.OpenAsync(Ready, CancellationToken.None);

        Assert.Equal(2, api.Selects.Count);
        Assert.Equal(api.Selects[0], api.Selects[1]);
        Assert.Equal(4, api.Selects[0].ExpectedVersion);
        Assert.Equal("v-1", api.Selects[0].SourceVersionId);
    }

    [Fact]
    public async Task Open_DoesNotRetryATypedServerError()
    {
        var (api, state) = Build(sessionVersion: 4);
        api.SelectFailures.Enqueue(Conflict(5));
        var catalog = new ApiSourceCatalog(api, state);

        await Assert.ThrowsAsync<ApiErrorException>(() => catalog.OpenAsync(Ready, CancellationToken.None));

        Assert.Single(api.Selects);
    }

    [Fact]
    public async Task Open_RefusesASourceWithNoReadyVersionWithoutCallingTheServer()
    {
        var (api, state) = Build(sessionVersion: 0);
        var viewModel = Library(api, state);

        await viewModel.OpenSourceAsync(Parsing, CancellationToken.None);

        Assert.Empty(api.Selects);
        Assert.Equal("Still parsing is not ready to study yet.", viewModel.StatusMessage);
        Assert.Null(viewModel.OpenedSource);
    }

    [Fact]
    public async Task Refresh_ListsTheServersSourcesWithAccessibleLabels()
    {
        var (api, state) = Build(sessionVersion: 0);
        api.Sources.AddRange(new[] { new ApiSource("s-1", "Ohm's law chapter", "v-1", 2), new ApiSource("s-2", "Still parsing", null, null) });
        var viewModel = Library(api, state);

        await viewModel.RefreshSourcesAsync(CancellationToken.None);

        Assert.Equal(new[] { Ready, Parsing }, viewModel.AvailableSources);
        Assert.Equal("Ohm's law chapter, version 2, ready to study", viewModel.AvailableSources[0].AccessibleLabel);
        Assert.Equal("Still parsing, not ready to study", viewModel.AvailableSources[1].AccessibleLabel);
        Assert.Equal("2 source(s), 1 ready to study.", viewModel.StatusMessage);
    }

    [Fact]
    public async Task Open_AdoptsTheServersSnapshot()
    {
        var (api, state) = Build(sessionVersion: 0);
        api.NextSnapshot = Snapshot(version: 1, source: "v-1");
        var viewModel = Library(api, state);

        await viewModel.OpenSourceAsync(Ready, CancellationToken.None);

        Assert.Equal(1, state.SessionVersion);
        Assert.Equal("v-1", state.ActiveSourceVersionId);
        Assert.Equal(SessionInteractionMode.Reading, state.InteractionMode);
        Assert.Same(Ready, viewModel.OpenedSource);
        Assert.Equal("Opened Ohm's law chapter, version 2, ready to study.", viewModel.StatusMessage);
    }

    [Fact]
    public async Task Open_IgnoresAnOlderReplayedSnapshotInsteadOfRollingStateBack()
    {
        var (api, state) = Build(sessionVersion: 7);
        state.ActiveSourceVersionId = "v-current";
        api.NextSnapshot = Snapshot(version: 3, source: "v-1");
        var viewModel = Library(api, state);

        await viewModel.OpenSourceAsync(Ready, CancellationToken.None);

        Assert.Equal(7, state.SessionVersion);
        Assert.Equal("v-current", state.ActiveSourceVersionId);
    }

    [Fact]
    public async Task AVersionConflict_ResynchronizesButNeverRepeatsThePinForTheStudent()
    {
        var (api, state) = Build(sessionVersion: 1);
        api.SelectFailures.Enqueue(Conflict(2));
        var resyncs = 0;
        var viewModel = new LibraryViewModel(
            new FixtureSourcePreparationService(), new FixtureVideoDiscoveryService(),
            new LibraryServerAccess(new ApiSourceCatalog(api, state), state, _ => { resyncs++; return Task.CompletedTask; }));

        await viewModel.OpenSourceAsync(Ready, CancellationToken.None);

        Assert.Single(api.Selects);
        Assert.Equal(1, resyncs);
        Assert.Equal(1, state.SessionVersion); // only a real snapshot may move it
        Assert.Null(viewModel.OpenedSource);
        Assert.Equal("Your session changed on the server and has been refreshed. Choose Open again.", viewModel.StatusMessage);
    }

    [Fact]
    public async Task Failures_UseClientWordingAndNeverTheExceptionText()
    {
        var (api, state) = Build(sessionVersion: 0);
        api.ListFailure = new HttpRequestException("secret-host.internal:5432 refused");
        var viewModel = Library(api, state);

        await viewModel.RefreshSourcesAsync(CancellationToken.None);

        Assert.Equal("Could not reach the Netra server to load your sources.", viewModel.StatusMessage);

        api.ListFailure = new CredentialUnavailableException();
        await viewModel.RefreshSourcesAsync(CancellationToken.None);
        Assert.Equal("This computer is not signed in to Netra.", viewModel.StatusMessage);
    }

    [Fact]
    public async Task WithoutAServer_TheLibrarySaysSoAndHidesNothingBehindFixtures()
    {
        var viewModel = new LibraryViewModel(new FixtureSourcePreparationService(), new FixtureVideoDiscoveryService());

        await viewModel.RefreshSourcesAsync(CancellationToken.None);

        Assert.False(viewModel.IsServerCatalogAvailable);
        Assert.Empty(viewModel.AvailableSources);
        Assert.Equal("Not connected to a Netra server. Server sources are not available.", viewModel.StatusMessage);
    }

    [Fact]
    public void ASnapshotPushedOnResumeIsAppliedEvenWhenItIsNotNewer()
    {
        var state = new ClientSessionState();
        var sessionId = Guid.NewGuid();
        state.Initialize(sessionId, 5);

        SnapshotReconciler.Apply(state, Snapshot(version: 5, source: "v-1"));

        Assert.Equal(sessionId, state.SessionId);
        Assert.Equal(5, state.SessionVersion);
        Assert.Equal("v-1", state.ActiveSourceVersionId);
        Assert.False(SnapshotReconciler.ApplyUnlessOlder(state, Snapshot(version: 4, source: "v-0")));
        Assert.Equal("v-1", state.ActiveSourceVersionId);
    }

    private static LibraryViewModel Library(FakeApi api, ClientSessionState state) =>
        new(new FixtureSourcePreparationService(), new FixtureVideoDiscoveryService(),
            new LibraryServerAccess(new ApiSourceCatalog(api, state), state));

    private static (FakeApi Api, ClientSessionState State) Build(long sessionVersion)
    {
        var state = new ClientSessionState();
        state.Initialize(Guid.NewGuid(), sessionVersion);
        return (new FakeApi(), state);
    }

    private static SessionSnapshotPayload Snapshot(long version, string source) => new()
    {
        SessionVersion = version,
        InteractionMode = SessionInteractionMode.Reading,
        ActiveSourceVersionId = source,
    };

    private static ApiErrorException Conflict(long current) => new(409, new ErrorPayload
    {
        Code = ErrorCode.SessionVersionConflict,
        Message = "The session changed.",
        Retryable = false,
        CurrentSessionVersion = current,
    });

    private sealed class FakeApi : INetraApi
    {
        public List<ApiSource> Sources { get; } = new();
        public Exception? ListFailure { get; set; }
        public Queue<Exception> SelectFailures { get; } = new();
        public List<(Guid RequestId, string SourceVersionId, long ExpectedVersion)> Selects { get; } = new();
        public SessionSnapshotPayload NextSnapshot { get; set; } = Snapshot(1, "v-1");

        public Task<ApiSessionCreated> CreateSessionAsync(CancellationToken cancellationToken) =>
            throw new NotSupportedException();

        public Task<IReadOnlyList<ApiSource>> ListSourcesAsync(Guid sessionId, CancellationToken cancellationToken) =>
            ListFailure is { } failure ? Task.FromException<IReadOnlyList<ApiSource>>(failure) : Task.FromResult<IReadOnlyList<ApiSource>>(Sources);

        public Task<ApiSourceSelected> SelectSourceAsync(
            Guid sessionId, Guid requestId, string sourceVersionId, long expectedSessionVersion, CancellationToken cancellationToken)
        {
            Selects.Add((requestId, sourceVersionId, expectedSessionVersion));
            return SelectFailures.TryDequeue(out var failure)
                ? Task.FromException<ApiSourceSelected>(failure)
                : Task.FromResult(new ApiSourceSelected(NextSnapshot, Replayed: false));
        }
    }
}
