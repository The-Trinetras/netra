using System.Collections.Concurrent;
using System.Diagnostics;
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

        // Only this account's sources; another account's source is invisible.
        var mine = Assert.Single(library.AvailableSources, s => s.SourceId == info.SourceId);
        Assert.DoesNotContain(library.AvailableSources, s => s.SourceId == info.OtherSourceId);
        Assert.True(mine.CanOpen);

        await library.OpenSourceAsync(mine, CancellationToken.None);
        Assert.Same(mine, library.OpenedSource);
        Assert.Equal(info.SourceVersionId, state.ActiveSourceVersionId);
        Assert.Equal(1, state.SessionVersion);

        // Stale expected version: typed conflict carrying the server's version.
        var stale = await Assert.ThrowsAsync<ApiErrorException>(() => api.SelectSourceAsync(
            state.SessionId, Guid.NewGuid(), info.SourceVersionId, 0, CancellationToken.None));
        Assert.Equal(409, stale.StatusCode);
        Assert.Equal(ErrorCode.SessionVersionConflict, stale.Error?.Code);
        Assert.Equal(1, stale.Error?.CurrentSessionVersion);

        // Same request id twice = one pin, replayed.
        var requestId = Guid.NewGuid();
        var first = await api.SelectSourceAsync(state.SessionId, requestId, info.SourceVersionId, 1, CancellationToken.None);
        var again = await api.SelectSourceAsync(state.SessionId, requestId, info.SourceVersionId, 1, CancellationToken.None);
        Assert.False(first.Replayed);
        Assert.True(again.Replayed);
        Assert.Equal(first.Snapshot.SessionVersion, again.Snapshot.SessionVersion);

        // Credentials: absent -> nothing sent; wrong -> AUTH_REQUIRED.
        using var anonymous = new NetraApiClient(NetraApiClient.HttpBaseFor(info.Endpoint), new NetraApiClientTests.FixedCredentials(null));
        await Assert.ThrowsAsync<CredentialUnavailableException>(() => anonymous.CreateSessionAsync(CancellationToken.None));
        using var wrong = new NetraApiClient(NetraApiClient.HttpBaseFor(info.Endpoint), new NetraApiClientTests.FixedCredentials("not-a-real-token"));
        var refused = await Assert.ThrowsAsync<ApiErrorException>(() => wrong.CreateSessionAsync(CancellationToken.None));
        Assert.Equal(401, refused.StatusCode);
        Assert.Equal(ErrorCode.AuthRequired, refused.Error?.Code);
    }

    [LiveServerFact]
    public async Task ReadAloudAcknowledgeStopAndReconnectOverTheRealSocket()
    {
        var info = LiveServerInfo.Load();
        var credentials = new NetraApiClientTests.FixedCredentials(info.Token);
        using var api = new NetraApiClient(NetraApiClient.HttpBaseFor(info.Endpoint), credentials);
        var state = new ClientSessionState();
        var created = await api.CreateSessionAsync(CancellationToken.None);
        SnapshotReconciler.Apply(state, created.Snapshot, created.SessionId);
        var catalog = new ApiSourceCatalog(api, state);
        var mine = (await catalog.ListAsync(CancellationToken.None)).Single(s => s.SourceId == info.SourceId);
        SnapshotReconciler.ApplyUnlessOlder(state, await catalog.OpenAsync(mine, CancellationToken.None));

        // The same wiring as App.xaml.cs, with ScriptedPlayer standing in for MediaPlayer.
        var socket = new NetraWebSocketClient(credentials);
        var connection = new ConnectionManager(socket, state);
        var player = new ScriptedPlayer();
        var timeline = new PlaybackTimeline();
        var interruption = new InterruptionController(player, connection, () => state.CurrentGenerationId, timeline);
        var processor = new BinaryAudioFrameProcessor(interruption);
        var assembler = new SegmentAudioAssembler();
        interruption.GenerationFenced += (_, _) =>
        {
            processor.ClearActiveGeneration();
            assembler.Reset();
        };
        socket.BinaryMessageReceived += processor.OnBinaryMessageReceived;
        processor.AudioBytesAdmitted += assembler.OnAudioBytesAdmitted;
        var store = new RecordingAudioStore();
        using var queue = new SegmentPlaybackQueue(player, interruption, store, timeline);
        var dispatcher = new SynchronousUiDispatcher();
        var completed = new ConcurrentQueue<CompleteSegmentAudio>();
        assembler.SegmentCompleted += (_, segment) =>
        {
            completed.Enqueue(segment);
            dispatcher.Invoke(() => queue.Enqueue(segment));
        };
        using var acknowledger = new PlaybackAcknowledger(player, connection, timeline);
        using var conversation = new ConversationViewModel(
            state, connection, player, interruption, processor, new MicrophoneCapture(), dispatcher, queue, timeline);

        var messages = new ConcurrentQueue<ServerToClientEnvelope>();
        connection.MessageReceived += (_, envelope) => messages.Enqueue(envelope);
        var rawFrames = 0;
        socket.BinaryMessageReceived += (_, _) => Interlocked.Increment(ref rawFrames);
        var admitted = new ConcurrentQueue<AudioFrameHeader>();
        processor.AudioBytesAdmitted += (_, frame) => admitted.Enqueue(frame.Header);

        await using var reconnect = new ReconnectCoordinator(
            socket, connection, state, info.Endpoint, backoff: new[] { TimeSpan.FromMilliseconds(50), TimeSpan.FromMilliseconds(200) });
        await reconnect.ConnectAsync(CancellationToken.None);
        await Until(() => messages.Any(m => m.Type == ServerMessageType.SessionSnapshot), "resume snapshot");
        Assert.Equal(info.SourceVersionId, state.ActiveSourceVersionId);

        // 1. Read the next sentence aloud: text, then real binary frames the
        //    client admits, assembles and queues under the announced sentence.
        var versionBefore = state.SessionVersion;
        conversation.NavigationCommandRequest.Execute(NavigationCommandType.Next);
        await Until(() => state.SessionVersion > versionBefore, "navigation snapshot");
        var segment = await First(messages, ServerMessageType.ResponseSegment);
        var spoken = MessageParser.ParseResponseSegment(segment);
        Assert.Equal(info.FirstSentenceIds[1], spoken.SentenceId);
        await Until(() => completed.Any(s => s.GenerationId == spoken.GenerationId), "segment audio");
        var audio = completed.Single(s => s.GenerationId == spoken.GenerationId);
        Assert.Equal("audio/mpeg", audio.MediaType);
        Assert.True(audio.Audio.Length > 0);
        var headers = admitted.Where(h => h.GenerationId == spoken.GenerationId).ToList();
        Assert.Equal(Enumerable.Range(0, headers.Count).Select(i => (long)i), headers.Select(h => h.Sequence));
        Assert.True(headers[^1].EndOfSegment && headers[^1].EndOfGeneration);
        await Until(() => player.Played.Count == 1, "queued playback");
        Assert.Equal(spoken.SentenceId, player.Played[0].SentenceId);

        // 2. Playback acknowledgement: started, then completed. The server
        //    records the acknowledged sentence and pushes a snapshot.
        player.Open();
        player.End();
        await Until(() => state.LastAcknowledgedSentenceId == spoken.SentenceId, "acknowledged sentence");

        // 3. STOP mid-generation: local fence first, then response.cancel;
        //    no further frame of that generation is admitted, nothing more is
        //    assembled for it, and the server stops producing it.
        var stoppedFirstFrame = new TaskCompletionSource<string>(TaskCreationOptions.RunContinuationsAsynchronously);
        processor.AudioBytesAdmitted += (_, frame) =>
        {
            if (frame.Header.GenerationId != spoken.GenerationId)
            {
                stoppedFirstFrame.TrySetResult(frame.Header.GenerationId);
            }
        };
        var errorsBefore = messages.Count(m => m.Type == ServerMessageType.Error);
        conversation.NavigationCommandRequest.Execute(NavigationCommandType.Next);
        var stoppedGeneration = await stoppedFirstFrame.Task.WaitAsync(Wait);
        var stopWatch = Stopwatch.StartNew();
        await interruption.StopAsync(CancelReason.UserStop, CancellationToken.None);
        var localStopMs = stopWatch.Elapsed.TotalMilliseconds;
        Assert.True(interruption.IsCancelled(stoppedGeneration));
        var admittedAtStop = admitted.Count(h => h.GenerationId == stoppedGeneration);
        await Task.Delay(300); // frames already in flight when STOP was sent
        var rawAfterGrace = Volatile.Read(ref rawFrames);
        await Task.Delay(1500); // longer than the rest of the paced generation
        Assert.Equal(admittedAtStop, admitted.Count(h => h.GenerationId == stoppedGeneration));
        Assert.DoesNotContain(completed, s => s.GenerationId == stoppedGeneration);
        Assert.Equal(rawAfterGrace, Volatile.Read(ref rawFrames));
        Assert.Equal(errorsBefore, messages.Count(m => m.Type == ServerMessageType.Error));
        Assert.Single(player.Played);

        // 4. Drop and reconnect: session.resume restores the same version,
        //    pinned source and acknowledged sentence; old audio never resumes.
        var versionAtDrop = state.SessionVersion;
        var snapshotsBefore = messages.Count(m => m.Type == ServerMessageType.SessionSnapshot);
        await socket.CloseAsync(CancellationToken.None);
        await Until(() => messages.Count(m => m.Type == ServerMessageType.SessionSnapshot) > snapshotsBefore, "reconnect snapshot");
        Assert.Equal(ConnectionState.Connected, state.ConnectionState);
        Assert.Equal(versionAtDrop, state.SessionVersion);
        Assert.Equal(info.SourceVersionId, state.ActiveSourceVersionId);
        Assert.Equal(spoken.SentenceId, state.LastAcknowledgedSentenceId);
        Assert.Single(player.Played);

        Console.WriteLine($"live: frames={Volatile.Read(ref rawFrames)} local_stop_ms={localStopMs:F2}");
        await connection.DisposeAsync();
    }

    private static async Task<ServerToClientEnvelope> First(ConcurrentQueue<ServerToClientEnvelope> messages, ServerMessageType type)
    {
        await Until(() => messages.Any(m => m.Type == type), type.ToString());
        return messages.First(m => m.Type == type);
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

    private sealed record LiveServerInfo(
        Uri Endpoint, string Token, string SourceId, string SourceVersionId, string OtherSourceId, IReadOnlyList<string> FirstSentenceIds)
    {
        public const string Variable = "NETRA_LIVE_SERVER_INFO";

        public static LiveServerInfo Load()
        {
            using var document = JsonDocument.Parse(File.ReadAllText(Environment.GetEnvironmentVariable(Variable)!));
            var root = document.RootElement;
            return new LiveServerInfo(
                new Uri(root.GetProperty("ws_endpoint").GetString()!),
                root.GetProperty("token").GetString()!,
                root.GetProperty("source_id").GetString()!,
                root.GetProperty("source_version_id").GetString()!,
                root.GetProperty("other_source_id").GetString()!,
                root.GetProperty("first_sentence_ids").EnumerateArray().Select(e => e.GetString()!).ToList());
        }
    }
}

// Skips (visibly) unless the live server launcher's info file is configured.
public sealed class LiveServerFactAttribute : FactAttribute
{
    public LiveServerFactAttribute()
    {
        var path = Environment.GetEnvironmentVariable("NETRA_LIVE_SERVER_INFO");
        if (string.IsNullOrEmpty(path) || !File.Exists(path))
        {
            Skip = "NETRA_LIVE_SERVER_INFO is not set to a running serve_for_client.py info file";
        }
    }
}
