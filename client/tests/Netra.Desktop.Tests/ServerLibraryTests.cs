using System.Net.Http;
using System.Threading;
using System.IO;
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
        CatalogSource? opened = null;
        viewModel.SourceOpened += (_, source) => opened = source;

        await viewModel.OpenSourceAsync(Ready, CancellationToken.None);

        Assert.Equal(1, state.SessionVersion);
        Assert.Equal("v-1", state.ActiveSourceVersionId);
        Assert.Equal(SessionInteractionMode.Reading, state.InteractionMode);
        Assert.Same(Ready, viewModel.OpenedSource);
        Assert.Same(Ready, opened);
        Assert.Equal("Opened Ohm's law chapter, version 2, ready to study.", viewModel.StatusMessage);
    }

    [Fact]
    public async Task Open_IgnoresAnOlderReplayedSnapshotInsteadOfRollingStateBack()
    {
        var (api, state) = Build(sessionVersion: 7);
        state.ActiveSourceVersionId = "v-current";
        api.NextSnapshot = Snapshot(version: 3, source: "v-1");
        var viewModel = Library(api, state);
        viewModel.SourceOpened += (_, _) => Assert.Fail("A stale pin must not open the Study view.");

        await viewModel.OpenSourceAsync(Ready, CancellationToken.None);

        Assert.Equal(7, state.SessionVersion);
        Assert.Equal("v-current", state.ActiveSourceVersionId);
        Assert.Null(viewModel.OpenedSource);
    }

    [Fact]
    public async Task AVersionConflict_ResynchronizesButNeverRepeatsThePinForTheStudent()
    {
        var (api, state) = Build(sessionVersion: 1);
        api.SelectFailures.Enqueue(Conflict(2));
        var session = new FakeLiveSession();
        var viewModel = Library(api, state, session);

        await viewModel.OpenSourceAsync(Ready, CancellationToken.None);

        Assert.Single(api.Selects);
        Assert.Equal(1, session.Resyncs);
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
        Assert.Equal("This computer is not signed in to Netra. Choose Sign in under Preferences and status.", viewModel.StatusMessage);

        api.ListFailure = new ApiErrorException(401, new ErrorPayload { Code = ErrorCode.AuthRequired, Message = "x", Retryable = false });
        await viewModel.RefreshSourcesAsync(CancellationToken.None);
        Assert.Equal("Netra did not accept this computer's sign-in. It may have expired. Choose Sign in under Preferences and status to enter a new access code.", viewModel.StatusMessage);
    }

    [Fact]
    public async Task AVersionConflictWhoseRefreshFails_SaysSoInsteadOfClaimingItWasRefreshed()
    {
        var (api, state) = Build(sessionVersion: 1);
        api.SelectFailures.Enqueue(Conflict(2));
        var session = new FakeLiveSession { ResyncFailure = new TimeoutException() };
        var viewModel = Library(api, state, session);

        await viewModel.OpenSourceAsync(Ready, CancellationToken.None);

        Assert.Equal(
            "Your session changed on the server and could not be refreshed. Choose Refresh, then Open again.",
            viewModel.StatusMessage);
    }

    // Before: an exception outside the recognised set was swallowed by the
    // command wrapper and the status stayed at "Opening ..." indefinitely.
    [Fact]
    public async Task AnUnexpectedFailure_StillEndsWithAFinalStatus()
    {
        var (api, state) = Build(sessionVersion: 0);
        api.SelectFailures.Enqueue(new Netra.Desktop.Protocol.ProtocolException("The server returned an empty response."));
        var viewModel = Library(api, state);

        await viewModel.OpenSourceAsync(Ready, CancellationToken.None);

        Assert.Equal("Netra could not open Ohm's law chapter.", viewModel.StatusMessage);
    }

    [Fact]
    public async Task Open_RetriesAClientTimeoutOnceUnderTheSameRequestId()
    {
        var (api, state) = Build(sessionVersion: 2);
        // What HttpClient throws when ITS timeout elapses (the caller did not cancel).
        api.SelectFailures.Enqueue(new TaskCanceledException("timeout", new TimeoutException()));
        var catalog = new ApiSourceCatalog(api, state);

        await catalog.OpenAsync(Ready, CancellationToken.None);

        Assert.Equal(2, api.Selects.Count);
        Assert.Equal(api.Selects[0], api.Selects[1]);
    }

    [Fact]
    public async Task Open_DoesNotRetryWhenTheCallerCancelled()
    {
        var (api, state) = Build(sessionVersion: 2);
        using var cancelled = new CancellationTokenSource();
        cancelled.Cancel();
        api.SelectFailures.Enqueue(new TaskCanceledException());
        var catalog = new ApiSourceCatalog(api, state);

        await Assert.ThrowsAsync<TaskCanceledException>(() => catalog.OpenAsync(Ready, cancelled.Token));

        Assert.Single(api.Selects);
    }

    [Fact]
    public async Task ASecondOpenWhileTheFirstIsInFlight_IsNotSentAsAnotherPin()
    {
        var (api, state) = Build(sessionVersion: 0);
        var release = new TaskCompletionSource();
        api.SelectGate = release.Task;
        var viewModel = Library(api, state);

        var first = viewModel.OpenSourceAsync(Ready, CancellationToken.None);
        var second = viewModel.OpenSourceAsync(Ready, CancellationToken.None);

        // Checked before releasing the first pin, so a missing guard fails
        // here instead of hanging on the gate.
        var secondReturnedAtOnce = second.IsCompleted;
        var selectsWhileFirstInFlight = api.Selects.Count;
        var statusWhileFirstInFlight = viewModel.StatusMessage;
        release.SetResult();
        await Task.WhenAll(first, second);

        Assert.True(secondReturnedAtOnce, "a second Open must return at once, not send another pin");
        Assert.Equal(1, selectsWhileFirstInFlight);
        Assert.Equal("Still opening your previous choice. Please wait.", statusWhileFirstInFlight);

        Assert.Single(api.Selects);
        Assert.Same(Ready, viewModel.OpenedSource);
    }

    [Fact]
    public async Task Refresh_KeepsTheStudentsSelectedSource()
    {
        var (api, state) = Build(sessionVersion: 0);
        api.Sources.AddRange(new[] { new ApiSource("s-1", "Ohm's law chapter", "v-1", 2), new ApiSource("s-2", "Still parsing", null, null) });
        var viewModel = Library(api, state);
        await viewModel.RefreshSourcesAsync(CancellationToken.None);
        viewModel.SelectedAvailableSource = viewModel.AvailableSources[1];

        api.Sources[1] = new ApiSource("s-2", "Still parsing", "v-9", 1); // became ready meanwhile
        await viewModel.RefreshSourcesAsync(CancellationToken.None);

        Assert.Equal("s-2", viewModel.SelectedAvailableSource?.SourceId);
        Assert.True(viewModel.SelectedAvailableSource!.CanOpen);
    }

    // Before: a start that failed at launch was never retried; the app stayed
    // unconnected until restart, and Refresh listed with an empty session id.
    [Fact]
    public async Task Refresh_StartsTheLiveSessionFirstAndRetriesAFailedStart()
    {
        var (api, state) = Build(sessionVersion: 0);
        api.Sources.Add(new ApiSource("s-1", "Ohm's law chapter", "v-1", 2));
        var session = new FakeLiveSession();
        session.StartFailures.Enqueue(new HttpRequestException("refused"));
        var viewModel = Library(api, state, session);

        await viewModel.RefreshSourcesAsync(CancellationToken.None);
        Assert.Equal("Could not reach the Netra server to load your sources.", viewModel.StatusMessage);
        Assert.Equal(0, api.ListCalls);

        await viewModel.RefreshSourcesAsync(CancellationToken.None);
        Assert.Equal(2, session.Starts);
        Assert.Equal(1, api.ListCalls);
        Assert.Single(viewModel.AvailableSources);
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

    private static LibraryViewModel Library(FakeApi api, ClientSessionState state, ILiveSession? session = null) =>
        new(new FixtureSourcePreparationService(), new FixtureVideoDiscoveryService(),
            new LibraryServerAccess(new ApiSourceCatalog(api, state), state, session));

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

    private sealed class FakeLiveSession : ILiveSession
    {
        public Queue<Exception> StartFailures { get; } = new();
        public Exception? ResyncFailure { get; set; }
        public int Starts { get; private set; }
        public int Resyncs { get; private set; }

        public Task EnsureStartedAsync(CancellationToken cancellationToken)
        {
            Starts++;
            return StartFailures.TryDequeue(out var failure) ? Task.FromException(failure) : Task.CompletedTask;
        }

        public Task ResynchronizeAsync(CancellationToken cancellationToken)
        {
            Resyncs++;
            return ResyncFailure is { } failure ? Task.FromException(failure) : Task.CompletedTask;
        }
    }

    private sealed class FakeApi : INetraApi
    {
        public List<ApiSource> Sources { get; } = new();
        public int ListCalls { get; private set; }
        public Task SelectGate { get; set; } = Task.CompletedTask;
        public Exception? ListFailure { get; set; }
        public Queue<Exception> SelectFailures { get; } = new();
        public List<(Guid RequestId, string SourceVersionId, long ExpectedVersion)> Selects { get; } = new();
        public SessionSnapshotPayload NextSnapshot { get; set; } = Snapshot(1, "v-1");


        public Task<ApiJob> UploadAsync(
            Guid sessionId, Guid requestId, string title, string fileName, Stream content, CancellationToken cancellationToken) =>
            throw new NotSupportedException();

        public Task<ApiJob> GetJobAsync(Guid sessionId, Guid jobId, CancellationToken cancellationToken) =>
            throw new NotSupportedException();
        public Task<ApiSessionCreated> CreateSessionAsync(CancellationToken cancellationToken) =>
            throw new NotSupportedException();

        public Task<IReadOnlyList<ApiSource>> ListSourcesAsync(Guid sessionId, CancellationToken cancellationToken)
        {
            ListCalls++;
            return ListFailure is { } failure
                ? Task.FromException<IReadOnlyList<ApiSource>>(failure)
                : Task.FromResult<IReadOnlyList<ApiSource>>(Sources.ToList());
        }

        public async Task<ApiSourceSelected> SelectSourceAsync(
            Guid sessionId, Guid requestId, string sourceVersionId, long expectedSessionVersion, CancellationToken cancellationToken)
        {
            Selects.Add((requestId, sourceVersionId, expectedSessionVersion));
            await SelectGate;
            if (SelectFailures.TryDequeue(out var failure))
            {
                throw failure;
            }

            return new ApiSourceSelected(NextSnapshot, Replayed: false);
        }
    }
}
