using System.Net.Http;
using System.Threading;
using System.IO;
using Netra.Desktop.Networking;
using Netra.Desktop.Protocol.Dto;
using Netra.Desktop.State;
using Xunit;

namespace Netra.Desktop.Tests;

// LiveSession: the session is created once, a failed start is retryable,
// concurrent callers share one attempt, and resynchronisation completes only
// when an authoritative snapshot has actually arrived.
public sealed class LiveSessionTests
{
    [Fact]
    public async Task CreatesTheSessionOnceAdoptsItsSnapshotAndConnects()
    {
        var rig = new Rig();

        await rig.Live.EnsureStartedAsync(CancellationToken.None);
        rig.State.ConnectionState = ConnectionState.Connected;
        await rig.Live.EnsureStartedAsync(CancellationToken.None);

        Assert.Equal(1, rig.Api.Creates);
        Assert.Equal(1, rig.Connects);
        Assert.Equal(rig.Api.SessionId, rig.State.SessionId);
        Assert.Equal(3, rig.State.SessionVersion);
    }

    [Fact]
    public async Task AFailedConnectIsRetriedWithoutCreatingASecondSession()
    {
        var rig = new Rig();
        rig.ConnectFailures.Enqueue(new System.Net.WebSockets.WebSocketException("refused"));

        await Assert.ThrowsAsync<System.Net.WebSockets.WebSocketException>(() => rig.Live.EnsureStartedAsync(CancellationToken.None));
        await rig.Live.EnsureStartedAsync(CancellationToken.None);

        Assert.Equal(1, rig.Api.Creates);
        Assert.Equal(2, rig.Connects);
    }

    [Fact]
    public async Task AFailedCreationIsRetriedByTheNextCall()
    {
        var rig = new Rig();
        rig.Api.CreateFailures.Enqueue(new HttpRequestException("server down at launch"));

        await Assert.ThrowsAsync<HttpRequestException>(() => rig.Live.EnsureStartedAsync(CancellationToken.None));
        Assert.Equal(0, rig.Connects);
        await rig.Live.EnsureStartedAsync(CancellationToken.None);

        Assert.Equal(2, rig.Api.Creates);
        Assert.Equal(1, rig.Connects);
    }

    [Fact]
    public async Task ConcurrentCallersShareOneAttempt()
    {
        var rig = new Rig();
        var release = new TaskCompletionSource();
        rig.Api.CreateGate = release.Task;

        var first = rig.Live.EnsureStartedAsync(CancellationToken.None);
        var second = rig.Live.EnsureStartedAsync(CancellationToken.None);
        release.SetResult();
        await Task.WhenAll(first, second);

        Assert.Equal(1, rig.Api.Creates);
        Assert.Equal(1, rig.Connects);
    }

    [Fact]
    public async Task WhileTheReconnectLoopOwnsTheSocket_ItIsNotConnectedTwice()
    {
        var rig = new Rig();
        await rig.Live.EnsureStartedAsync(CancellationToken.None);
        rig.State.ConnectionState = ConnectionState.Reconnecting;

        await rig.Live.EnsureStartedAsync(CancellationToken.None);

        Assert.Equal(1, rig.Connects);
    }

    [Fact]
    public async Task ResynchronizeSendsResumeAndCompletesOnlyWhenASnapshotArrives()
    {
        var rig = new Rig();
        rig.State.Initialize(Guid.NewGuid(), 4);
        rig.State.LastAcknowledgedSentenceId = "sentence-9";

        var resync = rig.Live.ResynchronizeAsync(CancellationToken.None);
        var resume = Assert.Single(rig.Socket.Sent);
        Assert.Contains("\"session.resume\"", resume);
        Assert.Contains("\"last_known_session_version\":4", resume);
        Assert.False(resync.IsCompleted);

        rig.Socket.ReceiveText(SnapshotEnvelope(rig.State.SessionId, version: 6));
        await resync.WaitAsync(TimeSpan.FromSeconds(5));
    }

    [Fact]
    public async Task ResynchronizeFailsWhenNoSnapshotArrives()
    {
        var rig = new Rig(resynchronizeTimeout: TimeSpan.FromMilliseconds(50));

        await Assert.ThrowsAsync<TimeoutException>(() => rig.Live.ResynchronizeAsync(CancellationToken.None));
    }

    private static string SnapshotEnvelope(Guid sessionId, long version) => $$$"""
        {"protocol_version":"1.0","message_id":"{{{Guid.NewGuid()}}}","session_id":"{{{sessionId}}}",
         "request_id":"{{{Guid.NewGuid()}}}","sequence":1,"type":"session.snapshot",
         "payload":{"session_version":{{{version}}},"interaction_mode":"reading","active_source_version_id":null,
         "current_block_id":null,"current_sentence_id":null,"last_acknowledged_sentence_id":null,
         "active_lesson":null,"pending_question":null,"last_result_set":null}}
        """;

    private sealed class Rig
    {
        public Rig(TimeSpan? resynchronizeTimeout = null)
        {
            Connection = new ConnectionManager(Socket, State);
            Live = new LiveSession(Api, State, Connection, _ =>
            {
                Connects++;
                return ConnectFailures.TryDequeue(out var failure) ? Task.FromException(failure) : Task.CompletedTask;
            }, resynchronizeTimeout);
        }

        public ClientSessionState State { get; } = new();
        public CapturingSocket Socket { get; } = new();
        public FakeApi Api { get; } = new();
        public ConnectionManager Connection { get; }
        public LiveSession Live { get; }
        public Queue<Exception> ConnectFailures { get; } = new();
        public int Connects { get; private set; }
    }

    private sealed class FakeApi : INetraApi
    {
        public Guid SessionId { get; } = Guid.NewGuid();

        public Task<ApiJob> UploadAsync(
            Guid sessionId, Guid requestId, string title, string fileName, Stream content, CancellationToken cancellationToken) =>
            throw new NotSupportedException();

        public Task<ApiJob> GetJobAsync(Guid sessionId, Guid jobId, CancellationToken cancellationToken) =>
            throw new NotSupportedException();

        public Queue<Exception> CreateFailures { get; } = new();
        public Task CreateGate { get; set; } = Task.CompletedTask;
        public int Creates { get; private set; }

        public async Task<ApiSessionCreated> CreateSessionAsync(CancellationToken cancellationToken)
        {
            Creates++;
            await CreateGate;
            if (CreateFailures.TryDequeue(out var failure))
            {
                throw failure;
            }

            return new ApiSessionCreated(SessionId, new SessionSnapshotPayload
            {
                SessionVersion = 3,
                InteractionMode = SessionInteractionMode.Idle,
            });
        }

        public Task<IReadOnlyList<ApiSource>> ListSourcesAsync(Guid sessionId, CancellationToken cancellationToken) =>
            throw new NotSupportedException();

        public Task<ApiSourceSelected> SelectSourceAsync(
            Guid sessionId, Guid requestId, string sourceVersionId, long expectedSessionVersion, CancellationToken cancellationToken) =>
            throw new NotSupportedException();
    }
}
