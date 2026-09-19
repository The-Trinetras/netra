using System.Collections.Concurrent;
using System.IO;
using System.Text.Json;
using System.Threading;
using Netra.Desktop.Audio;
using Netra.Desktop.Diagnostics;
using Netra.Desktop.Library;
using Netra.Desktop.Networking;
using Netra.Desktop.Protocol;
using Netra.Desktop.Protocol.Dto;
using Netra.Desktop.Speech;
using Netra.Desktop.State;
using Netra.Desktop.Threading;
using Netra.Desktop.ViewModels;
using Xunit;
using Xunit.Abstractions;

namespace Netra.Desktop.Tests;

// The desktop client's real classes against the REAL Netra server
// (api/tests/server/serve_for_client.py: uvicorn + production composition +
// disposable PostgreSQL), over TCP. Skipped unless NETRA_LIVE_SERVER_INFO
// names the launcher's info file.
//
// Evidence class: real local. The server's synthesizer is a paced stand-in
// (opaque bytes labelled audio/mpeg) and the player is ScriptedPlayer, so
// this proves frames, fencing, acknowledgement and state over the real wire,
// not audibility and not NVDA behaviour.
public sealed class LiveServerTests
{
    private static readonly TimeSpan Wait = TimeSpan.FromSeconds(10);
    private readonly ITestOutputHelper _output;

    public LiveServerTests(ITestOutputHelper output) => _output = output;

    [LiveServerFact]
    public async Task HttpSessionCreationListingAndExplicitPin()
    {
        var info = LiveServerInfo.Load();
        var credentials = new NetraApiClientTests.FixedCredentials(info.Token);
        using var api = new NetraApiClient(NetraApiClient.HttpBaseFor(info.Endpoint), credentials);
        var state = new ClientSessionState();

        var created = await api.CreateSessionAsync(CancellationToken.None);
        SnapshotReconciler.Apply(state, created.Snapshot, created.SessionId);
        Assert.Equal(0, state.SessionVersion);
        Assert.Equal(SessionInteractionMode.Idle, state.InteractionMode);

        var library = new LibraryViewModel(
            new FixtureSourcePreparationService(), new FixtureVideoDiscoveryService(),
            new LibraryServerAccess(new ApiSourceCatalog(api, state), state));
        await library.RefreshSourcesAsync(CancellationToken.None);

        // Exactly this account's three sources; another account's is invisible.
        Assert.Equal(
            new[] { info.SourceId, info.SecondSourceId, info.NotReadySourceId }.OrderBy(id => id),
            library.AvailableSources.Select(s => s.SourceId).OrderBy(id => id));
        Assert.DoesNotContain(library.AvailableSources, s => s.SourceId == info.OtherSourceId);
        Assert.Equal("3 source(s), 2 ready to study.", library.StatusMessage);
        var notReady = library.AvailableSources.Single(s => s.SourceId == info.NotReadySourceId);
        Assert.False(notReady.CanOpen);
        Assert.Equal("Still processing notes, not ready to study", notReady.AccessibleLabel);
        await library.OpenSourceAsync(notReady, CancellationToken.None);
        Assert.Equal(0, state.SessionVersion);
        Assert.Null(state.ActiveSourceVersionId);

        var mine = library.AvailableSources.Single(s => s.SourceId == info.SourceId);
        await library.OpenSourceAsync(mine, CancellationToken.None);
        Assert.Same(mine, library.OpenedSource);
        Assert.Equal(info.SourceVersionId, state.ActiveSourceVersionId);
        Assert.Equal(info.FirstSentenceIds[0], state.CurrentSentenceId);
        Assert.Equal(1, state.SessionVersion);

        // Stale expected version: typed conflict carrying the server's version.
        var stale = await Assert.ThrowsAsync<ApiErrorException>(() => api.SelectSourceAsync(
            state.SessionId, Guid.NewGuid(), info.SecondSourceVersionId, 0, CancellationToken.None));
        Assert.Equal(409, stale.StatusCode);
        Assert.Equal(ErrorCode.SessionVersionConflict, stale.Error?.Code);
        Assert.Equal(1, stale.Error?.CurrentSessionVersion);

        // Same request id twice = one pin, replayed with the recorded result.
        var requestId = Guid.NewGuid();
        var first = await api.SelectSourceAsync(state.SessionId, requestId, info.SecondSourceVersionId, 1, CancellationToken.None);
        var again = await api.SelectSourceAsync(state.SessionId, requestId, info.SecondSourceVersionId, 1, CancellationToken.None);
        Assert.False(first.Replayed);
        Assert.True(again.Replayed);
        Assert.Equal(first.Snapshot, again.Snapshot);
        Assert.Equal(info.SecondSourceVersionId, again.Snapshot.ActiveSourceVersionId);

        // Credentials: absent -> nothing sent; wrong or expired -> AUTH_REQUIRED.
        using var anonymous = new NetraApiClient(NetraApiClient.HttpBaseFor(info.Endpoint), new NetraApiClientTests.FixedCredentials(null));
        await Assert.ThrowsAsync<CredentialUnavailableException>(() => anonymous.CreateSessionAsync(CancellationToken.None));
        foreach (var token in new[] { "not-a-real-token", info.ExpiredToken })
        {
            using var refusedClient = new NetraApiClient(NetraApiClient.HttpBaseFor(info.Endpoint), new NetraApiClientTests.FixedCredentials(token));
            var refused = await Assert.ThrowsAsync<ApiErrorException>(() => refusedClient.CreateSessionAsync(CancellationToken.None));
            Assert.Equal(401, refused.StatusCode);
            Assert.Equal(ErrorCode.AuthRequired, refused.Error?.Code);
        }
    }

    // The app's real order (App.xaml.cs): LiveSession creates the session and
    // connects the socket, the library lists and opens over HTTP WHILE the
    // socket is connected, and reading continues over the socket.
    [LiveServerFact]
    public async Task AppOrderStartOpenReadAcknowledgeStopSwitchConflictAndReconnect()
    {
        var info = LiveServerInfo.Load();
        var credentials = new NetraApiClientTests.FixedCredentials(info.Token);
        using var api = new NetraApiClient(NetraApiClient.HttpBaseFor(info.Endpoint), credentials);
        var client = new ClientRig(credentials);
        await using var reconnect = new ReconnectCoordinator(
            client.Socket, client.Connection, client.State, info.Endpoint,
            backoff: new[] { TimeSpan.FromMilliseconds(50), TimeSpan.FromMilliseconds(200) });
        var live = new LiveSession(api, client.State, client.Connection, reconnect.ConnectAsync);
        var library = new LibraryViewModel(
            new FixtureSourcePreparationService(), new FixtureVideoDiscoveryService(),
            new LibraryServerAccess(new ApiSourceCatalog(api, client.State), client.State, live));
        var state = client.State;

        // 1. Start: session created over HTTP, socket connected, sources listed.
        await library.RefreshSourcesAsync(CancellationToken.None);
        Assert.Equal(ConnectionState.Connected, state.ConnectionState);
        Assert.NotEqual(Guid.Empty, state.SessionId);
        Assert.Equal(3, library.AvailableSources.Count);
        await Until(() => client.Count(ServerMessageType.SessionSnapshot) >= 1, "resume snapshot");

        // 2. Open source A over HTTP while the socket is connected.
        var sourceA = library.AvailableSources.Single(s => s.SourceId == info.SourceId);
        await library.OpenSourceAsync(sourceA, CancellationToken.None);
        Assert.Equal(info.SourceVersionId, state.ActiveSourceVersionId);
        Assert.Equal(1, state.SessionVersion);

        // 3. Read the next sentence aloud over the socket: text, then real
        //    binary frames admitted, assembled and queued under that sentence.
        var spoken = await client.NavigateAsync(NavigationCommandType.Next);
        Assert.Equal(info.FirstSentenceIds[1], spoken.SentenceId);
        Assert.Equal(2, state.SessionVersion);
        await Until(() => client.Completed.Any(s => s.GenerationId == spoken.GenerationId), "segment audio");
        var audio = client.Completed.Single(s => s.GenerationId == spoken.GenerationId);
        Assert.Equal("audio/mpeg", audio.MediaType);
        var headers = client.Admitted.Where(h => h.GenerationId == spoken.GenerationId).ToList();
        Assert.True(headers.Count > 1, $"expected paced multi-frame delivery, got {headers.Count} frame(s)");
        Assert.Equal(Enumerable.Range(0, headers.Count).Select(i => (long)i), headers.Select(h => h.Sequence));
        Assert.True(headers[^1].EndOfSegment && headers[^1].EndOfGeneration);
        await Until(() => client.Player.Played.Count == 1, "queued playback");
        Assert.Equal(spoken.SentenceId, client.Player.Played[0].SentenceId);

        // 4. Playback acknowledgement (started, completed) is recorded by the server.
        client.Player.Open();
        client.Player.End();
        await Until(() => state.LastAcknowledgedSentenceId == spoken.SentenceId, "acknowledged sentence");

        // 5. STOP mid-generation: fenced locally first, then response.cancel;
        //    nothing more of it is admitted or assembled, and the server stops.
        var stopped = await client.InterruptNextGenerationAsync(
            () => client.Interruption.StopAsync(CancelReason.UserStop, CancellationToken.None));
        Assert.False(stopped.CompleteBeforeInterrupt, "the scenario must interrupt a generation still streaming");
        Assert.True(client.Interruption.IsCancelled(stopped.GenerationId));
        Assert.Equal(stopped.AdmittedAtStop, client.Admitted.Count(h => h.GenerationId == stopped.GenerationId));
        Assert.DoesNotContain(client.Completed, s => s.GenerationId == stopped.GenerationId);
        Assert.Equal(stopped.RawAfterGrace, stopped.RawAtEnd);
        Assert.Equal(0, client.Count(ServerMessageType.Error));
        Assert.Single(client.Player.Played);
        _output.WriteLine($"STOP: {stopped.RawAfterGrace - stopped.RawAtStop} frame(s) arrived in flight after STOP, none later; "
            + $"local stop (ScriptedPlayer, not audio) {string.Join(",", client.Timeline.StopToLocalStopMilliseconds().Select(ms => ms.ToString("F3")))} ms");

        // 6. Switch to source B: the new version starts at its first sentence.
        var sourceB = library.AvailableSources.Single(s => s.SourceId == info.SecondSourceId);
        await library.OpenSourceAsync(sourceB, CancellationToken.None);
        Assert.Same(sourceB, library.OpenedSource);
        Assert.Equal(info.SecondSourceVersionId, state.ActiveSourceVersionId);
        Assert.Equal(info.SecondFirstSentenceIds[0], state.CurrentSentenceId);
        var inB = await client.NavigateAsync(NavigationCommandType.Next);
        Assert.Equal(info.SecondFirstSentenceIds[1], inB.SentenceId);
        await Until(() => client.Player.Played.Count == 2, "source B segment playback");
        Assert.Equal(inB.SentenceId, client.Player.Played[1].SentenceId);
        client.Player.Open();
        client.Player.End();
        await Until(() => state.LastAcknowledgedSentenceId == inB.SentenceId, "source B acknowledgement");

        // 7. Drop the connection MID-generation, then reconnect: the generation
        //    in flight at the drop is fenced and never resumes (no later frame
        //    admitted, nothing assembled or played); session.resume restores
        //    the version and pin. The next sentence of B has never been
        //    synthesized, so its audio is paced (a cached segment would arrive
        //    whole in one frame and leave nothing in flight to fence).
        var snapshotsBefore = client.Count(ServerMessageType.SessionSnapshot);
        var dropped = await client.InterruptNextGenerationAsync(() => client.Socket.CloseAsync(CancellationToken.None));
        Assert.False(dropped.CompleteBeforeInterrupt, "the scenario must interrupt a generation still streaming");
        await Until(() => client.Count(ServerMessageType.SessionSnapshot) > snapshotsBefore + 1, "reconnect snapshot");
        Assert.Equal(ConnectionState.Connected, state.ConnectionState);
        Assert.True(client.Interruption.IsCancelled(dropped.GenerationId));
        Assert.Equal(dropped.AdmittedAtStop, client.Admitted.Count(h => h.GenerationId == dropped.GenerationId));
        Assert.DoesNotContain(client.Completed, s => s.GenerationId == dropped.GenerationId);
        Assert.Equal(dropped.VersionAtInterrupt, state.SessionVersion);
        Assert.Equal(info.SecondSourceVersionId, state.ActiveSourceVersionId);
        Assert.Equal(2, client.Player.Played.Count);
        _output.WriteLine($"drop: {dropped.RawAfterGrace - dropped.RawAtStop} frame(s) in flight after the drop, none after reconnect");

        // 8. Version conflict over the reconnected socket: simulate a client
        //    that missed one update by lowering ITS local counter (the server
        //    is untouched). The pin is refused, the client resynchronises, and
        //    the next explicit Open succeeds against the server's real version.
        var serverVersion = state.SessionVersion;
        state.Initialize(state.SessionId, serverVersion - 1);
        await library.OpenSourceAsync(sourceA, CancellationToken.None);
        Assert.Equal("Your session changed on the server and has been refreshed. Choose Open again.", library.StatusMessage);
        Assert.Equal(serverVersion, state.SessionVersion);
        Assert.Equal(info.SecondSourceVersionId, state.ActiveSourceVersionId);
        await library.OpenSourceAsync(sourceA, CancellationToken.None);
        Assert.Same(sourceA, library.OpenedSource);
        Assert.Equal(info.SourceVersionId, state.ActiveSourceVersionId);
        Assert.Equal(serverVersion + 1, state.SessionVersion);
        Assert.Equal(0, client.Count(ServerMessageType.Error));

        _output.WriteLine($"frames received: {client.RawFrames}");
        await client.Connection.DisposeAsync();
    }

    // An expired token is refused at the upgrade (M1 closes before accept ->
    // HTTP 403). The client names the cause and does not retry a credential
    // that cannot succeed, both at first connect and during reconnect.
    [LiveServerFact]
    public async Task AnExpiredCredentialIsRejectedAtTheUpgradeAndNotRetried()
    {
        var info = LiveServerInfo.Load();

        var expired = new NetraWebSocketClient(new NetraApiClientTests.FixedCredentials(info.ExpiredToken));
        await Assert.ThrowsAsync<CredentialRejectedException>(() => expired.ConnectAsync(info.Endpoint, CancellationToken.None));
        await expired.DisposeAsync();

        // The token expires during a session: connected with a valid one,
        // then every later read of the store yields the expired one.
        var credentials = new SwitchableCredentials(info.Token);
        using var api = new NetraApiClient(NetraApiClient.HttpBaseFor(info.Endpoint), credentials);
        var state = new ClientSessionState();
        var socket = new NetraWebSocketClient(credentials);
        var connection = new ConnectionManager(socket, state);
        var attempts = 0;
        var statuses = new ConcurrentQueue<string>();
        await using var reconnect = new ReconnectCoordinator(
            socket, connection, state, info.Endpoint,
            backoff: Enumerable.Repeat(TimeSpan.FromMilliseconds(20), 5).ToArray(),
            delay: (d, ct) => { Interlocked.Increment(ref attempts); return Task.Delay(d, ct); });
        reconnect.StatusChanged += (_, message) => statuses.Enqueue(message);
        await new LiveSession(api, state, connection, reconnect.ConnectAsync).EnsureStartedAsync(CancellationToken.None);
        Assert.Equal(ConnectionState.Connected, state.ConnectionState);

        credentials.Token = info.ExpiredToken;
        await socket.CloseAsync(CancellationToken.None);
        await Until(() => reconnect.CurrentReconnectLoop is { IsCompleted: true }, "reconnect loop to stop");

        Assert.Equal(1, attempts);
        Assert.Equal(ConnectionState.Disconnected, state.ConnectionState);
        Assert.Equal("Cannot reconnect: Netra did not accept this computer's sign-in. It may have expired. Choose Sign in under Preferences and status to enter a new access code.", statuses.Last());
        await connection.DisposeAsync();
    }

    private static async Task Until(Func<bool> condition, string what)
    {
        var deadline = DateTime.UtcNow + Wait;
        while (!condition())
        {
            if (DateTime.UtcNow > deadline)
            {
                throw new TimeoutException($"timed out waiting for {what}");
            }

            await Task.Delay(20);
        }
    }

    // Wired exactly like App.xaml.cs, with ScriptedPlayer standing in for MediaPlayer.
    private sealed class ClientRig
    {
        private readonly ConcurrentQueue<ServerToClientEnvelope> _messages = new();
        private int _rawFrames;

        public ClientRig(ICredentialSource credentials)
        {
            Socket = new NetraWebSocketClient(credentials);
            Connection = new ConnectionManager(Socket, State);
            Interruption = new InterruptionController(Player, Connection, () => State.CurrentGenerationId, Timeline);
            Processor = new BinaryAudioFrameProcessor(Interruption);
            var assembler = new SegmentAudioAssembler();
            Interruption.GenerationFenced += (_, _) =>
            {
                Processor.ClearActiveGeneration();
                assembler.Reset();
            };
            Socket.BinaryMessageReceived += Processor.OnBinaryMessageReceived;
            Processor.AudioBytesAdmitted += assembler.OnAudioBytesAdmitted;
            var queue = new SegmentPlaybackQueue(Player, Interruption, new RecordingAudioStore(), Timeline);
            var dispatcher = new SynchronousUiDispatcher();
            assembler.SegmentCompleted += (_, segment) =>
            {
                Completed.Enqueue(segment);
                dispatcher.Invoke(() => queue.Enqueue(segment));
            };
            _ = new PlaybackAcknowledger(Player, Connection, Timeline);
            // Composed before the library/LiveSession, as in App: its snapshot
            // handler runs before LiveSession's resynchronisation waiter.
            Conversation = new ConversationViewModel(
                State, Connection, Player, Interruption, Processor, new MicrophoneCapture(Connection), dispatcher, queue, Timeline);
            Connection.MessageReceived += (_, envelope) => _messages.Enqueue(envelope);
            Socket.BinaryMessageReceived += (_, _) => Interlocked.Increment(ref _rawFrames);
            Processor.AudioBytesAdmitted += (_, frame) => Admitted.Enqueue(frame.Header);
        }

        public ClientSessionState State { get; } = new();
        public NetraWebSocketClient Socket { get; }
        public ConnectionManager Connection { get; }
        public ScriptedPlayer Player { get; } = new();
        public PlaybackTimeline Timeline { get; } = new();
        public InterruptionController Interruption { get; }
        public BinaryAudioFrameProcessor Processor { get; }
        public ConversationViewModel Conversation { get; }
        public ConcurrentQueue<CompleteSegmentAudio> Completed { get; } = new();
        public ConcurrentQueue<AudioFrameHeader> Admitted { get; } = new();
        public int RawFrames => Volatile.Read(ref _rawFrames);

        public int Count(ServerMessageType type) => _messages.Count(m => m.Type == type);

        // Through the view model's command, as the keyboard path does.
        public async Task<ResponseSegmentPayload> NavigateAsync(NavigationCommandType command)
        {
            var before = State.SessionVersion;
            var segmentsBefore = Count(ServerMessageType.ResponseSegment);
            Conversation.NavigationCommandRequest.Execute(command);
            await Until(() => State.SessionVersion > before && Count(ServerMessageType.ResponseSegment) > segmentsBefore, $"{command} reply");
            return MessageParser.ParseResponseSegment(_messages.Where(m => m.Type == ServerMessageType.ResponseSegment).Last());
        }

        // Starts the next sentence, waits for its FIRST admitted audio frame
        // (so the generation is mid-stream), then interrupts it.
        public async Task<InterruptOutcome> InterruptNextGenerationAsync(Func<Task> interrupt)
        {
            var seen = Admitted.Select(h => h.GenerationId).ToHashSet();
            var firstFrame = new TaskCompletionSource<string>(TaskCreationOptions.RunContinuationsAsynchronously);
            void OnFrame(object? sender, (AudioFrameHeader Header, ReadOnlyMemory<byte> AudioBytes) frame)
            {
                if (!seen.Contains(frame.Header.GenerationId))
                {
                    firstFrame.TrySetResult(frame.Header.GenerationId);
                }
            }

            Processor.AudioBytesAdmitted += OnFrame;
            var before = State.SessionVersion;
            Conversation.NavigationCommandRequest.Execute(NavigationCommandType.Next);
            var generation = await firstFrame.Task.WaitAsync(Wait);
            Processor.AudioBytesAdmitted -= OnFrame;
            await Until(() => State.SessionVersion > before, "navigation snapshot");
            var versionAtInterrupt = State.SessionVersion;

            var rawAtStop = RawFrames;
            var completeBeforeInterrupt = Completed.Any(s => s.GenerationId == generation);
            await interrupt();
            var admittedAtStop = Admitted.Count(h => h.GenerationId == generation);
            await Task.Delay(300); // frames already in flight when the interrupt happened
            var rawAfterGrace = RawFrames;
            await Task.Delay(1500); // longer than the rest of the paced generation
            return new InterruptOutcome(
                generation, versionAtInterrupt, completeBeforeInterrupt, admittedAtStop, rawAtStop, rawAfterGrace, RawFrames);
        }
    }

    private sealed record InterruptOutcome(
        string GenerationId, long VersionAtInterrupt, bool CompleteBeforeInterrupt, int AdmittedAtStop, int RawAtStop,
        int RawAfterGrace, int RawAtEnd);

    private sealed class SwitchableCredentials : ICredentialSource
    {
        public SwitchableCredentials(string token) => Token = token;

        public string Token { get; set; }

        public ValueTask<string?> GetBearerTokenAsync(CancellationToken cancellationToken) => ValueTask.FromResult<string?>(Token);
    }

    private sealed record LiveServerInfo(
        Uri Endpoint,
        string Token,
        string ExpiredToken,
        string SourceId,
        string SourceVersionId,
        IReadOnlyList<string> FirstSentenceIds,
        string SecondSourceId,
        string SecondSourceVersionId,
        IReadOnlyList<string> SecondFirstSentenceIds,
        string NotReadySourceId,
        string OtherSourceId)
    {
        public static LiveServerInfo Load()
        {
            using var document = JsonDocument.Parse(File.ReadAllText(Environment.GetEnvironmentVariable(LiveServerFactAttribute.Variable)!));
            var root = document.RootElement;
            string Text(string name) => root.GetProperty(name).GetString()!;
            IReadOnlyList<string> List(string name) => root.GetProperty(name).EnumerateArray().Select(e => e.GetString()!).ToList();
            return new LiveServerInfo(
                new Uri(Text("ws_endpoint")),
                Text("token"),
                Text("expired_token"),
                Text("source_id"),
                Text("source_version_id"),
                List("first_sentence_ids"),
                Text("second_source_id"),
                Text("second_source_version_id"),
                List("second_first_sentence_ids"),
                Text("not_ready_source_id"),
                Text("other_source_id"));
        }
    }
}

// Skips (visibly) unless the live server launcher's info file is configured.
public sealed class LiveServerFactAttribute : FactAttribute
{
    public const string Variable = "NETRA_LIVE_SERVER_INFO";

    public LiveServerFactAttribute()
    {
        var path = Environment.GetEnvironmentVariable(Variable);
        if (string.IsNullOrEmpty(path) || !File.Exists(path))
        {
            Skip = $"{Variable} is not set to a running serve_for_client.py info file";
        }
    }
}
