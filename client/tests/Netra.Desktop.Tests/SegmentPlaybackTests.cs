using System.Text;
using System.Text.Json;
using System.Threading;
using Netra.Desktop.Audio;
using Netra.Desktop.Diagnostics;
using Netra.Desktop.Networking;
using Netra.Desktop.Protocol.Dto;
using Netra.Desktop.State;
using Xunit;

namespace Netra.Desktop.Tests;

// Segment assembly -> bounded queue -> player, with STOP/disconnect fencing.
// The player is a scripted double: these prove ordering, fencing and
// acknowledgement identity, not that a speaker made sound.
public sealed class SegmentPlaybackTests
{
    internal static byte[] Frame(
        string generationId, string segmentId, long sequence, byte[] audio,
        bool endOfSegment = false, bool endOfGeneration = false, string mediaType = "audio/mpeg")
    {
        var header = JsonSerializer.SerializeToUtf8Bytes(new Dictionary<string, object>
        {
            ["version"] = 1,
            ["generation_id"] = generationId,
            ["segment_id"] = segmentId,
            ["sequence"] = sequence,
            ["end_of_segment"] = endOfSegment,
            ["end_of_generation"] = endOfGeneration,
            ["media_type"] = mediaType,
        });
        var frame = new byte[4 + header.Length + audio.Length];
        frame[0] = (byte)(header.Length >> 24);
        frame[1] = (byte)(header.Length >> 16);
        frame[2] = (byte)(header.Length >> 8);
        frame[3] = (byte)header.Length;
        header.CopyTo(frame, 4);
        audio.CopyTo(frame, 4 + header.Length);
        return frame;
    }

    private static readonly byte[] A = Encoding.ASCII.GetBytes("aaa");
    private static readonly byte[] B = Encoding.ASCII.GetBytes("bbb");

    private sealed class Rig
    {
        public Rig()
        {
            SessionState.Initialize(Guid.NewGuid(), sessionVersion: 1);
            Connection = new ConnectionManager(Socket, SessionState);
            Interruption = new InterruptionController(Player, Connection, () => SessionState.CurrentGenerationId, Timeline);
            Processor = new BinaryAudioFrameProcessor(Interruption);
            Queue = new SegmentPlaybackQueue(Player, Interruption, Store, Timeline);
            Interruption.GenerationFenced += (_, _) =>
            {
                Processor.ClearActiveGeneration();
                Assembler.Reset();
            };
            Processor.AudioBytesAdmitted += Assembler.OnAudioBytesAdmitted;
            Processor.FrameRejected += (_, r) => Rejections.Add(r);
            Assembler.SegmentCompleted += (_, s) => Queue.Enqueue(s);
            Queue.StatusChanged += (_, m) => Statuses.Add(m);
        }

        public ClientSessionState SessionState { get; } = new();
        public CapturingSocket Socket { get; } = new();
        public ScriptedPlayer Player { get; } = new();
        public RecordingAudioStore Store { get; } = new();
        public PlaybackTimeline Timeline { get; } = new();
        public SegmentAudioAssembler Assembler { get; } = new();
        public ConnectionManager Connection { get; }
        public InterruptionController Interruption { get; }
        public BinaryAudioFrameProcessor Processor { get; }
        public SegmentPlaybackQueue Queue { get; }
        public List<FrameRejection> Rejections { get; } = new();
        public List<string> Statuses { get; } = new();

        // What ConversationViewModel does on response.segment.
        public void Announce(string generationId, string segmentId, string sentenceId)
        {
            Processor.AdmitGeneration(generationId);
            SessionState.CurrentGenerationId = generationId;
            Queue.RegisterSegment(generationId, segmentId, sentenceId);
        }

        public void Receive(byte[] frame) => Processor.OnBinaryMessageReceived(null, frame);
    }

    [Fact]
    public void CompleteSegmentPlaysWithItsAnnouncedSentenceId()
    {
        var rig = new Rig();
        rig.Announce("g1", "s1", "sentence-1");

        rig.Receive(Frame("g1", "s1", 0, A));
        Assert.Empty(rig.Player.Played); // nothing plays before end_of_segment

        rig.Receive(Frame("g1", "s1", 1, B, endOfSegment: true, endOfGeneration: true));

        var played = Assert.Single(rig.Player.Played);
        Assert.Equal(("g1", "s1", "sentence-1"), (played.GenerationId, played.SegmentId, played.SentenceId));
        Assert.Equal("aaabbb", Encoding.ASCII.GetString(rig.Store.Staged[0].Audio));
    }

    [Fact]
    public void SegmentsPlayInOrderAndTheNextStartsOnlyAfterTheFirstEnds()
    {
        var rig = new Rig();
        rig.Announce("g1", "s1", "sentence-1");
        rig.Queue.RegisterSegment("g1", "s2", "sentence-2");

        rig.Receive(Frame("g1", "s1", 0, A, endOfSegment: true));
        rig.Receive(Frame("g1", "s2", 1, B, endOfSegment: true, endOfGeneration: true));

        Assert.Single(rig.Player.Played);
        Assert.Equal(1, rig.Queue.QueuedCount);

        rig.Player.Open();
        rig.Player.End();

        Assert.Equal(new[] { "s1", "s2" }, rig.Player.Played.Select(p => p.SegmentId));
        Assert.Single(rig.Store.Released); // the finished segment's buffer is released
    }

    [Fact]
    public async Task StopDuringPlaybackSilencesLocallyAndLateFramesNeverPlay()
    {
        var rig = new Rig();
        rig.Announce("g1", "s1", "sentence-1");
        rig.Queue.RegisterSegment("g1", "s2", "sentence-2");
        rig.Receive(Frame("g1", "s1", 0, A, endOfSegment: true));
        rig.Player.Open();

        await rig.Interruption.StopAsync(CancelReason.UserStop, CancellationToken.None);

        Assert.Equal(PlaybackStatus.Stopped, rig.Player.CurrentSnapshot.Status);
        rig.Receive(Frame("g1", "s2", 1, B, endOfSegment: true, endOfGeneration: true)); // late audio
        Assert.Single(rig.Player.Played);
        Assert.Contains(rig.Rejections, r => r.GenerationId == "g1");
        Assert.Single(rig.Timeline.StopToLocalStopMilliseconds());
    }

    [Fact]
    public async Task StopWhileAudioIsStillInFlightFencesTheAnnouncedGeneration()
    {
        // Text arrived, nothing is playing yet (network wait). Before this
        // fix the player's snapshot had no generation, nothing was fenced
        // and the audio started after the student pressed STOP.
        var rig = new Rig();
        rig.Announce("g1", "s1", "sentence-1");
        rig.Receive(Frame("g1", "s1", 0, A));

        await rig.Interruption.StopAsync(CancelReason.UserStop, CancellationToken.None);
        rig.Receive(Frame("g1", "s1", 1, B, endOfSegment: true, endOfGeneration: true));

        Assert.Empty(rig.Player.Played);
        Assert.False(rig.Interruption.ShouldPlay("g1"));
        var cancel = Assert.Single(rig.Socket.Sent, m => m.Contains("\"response.cancel\""));
        Assert.Contains("\"generation_id\":\"g1\"", cancel);
    }

    [Fact]
    public async Task LocalStopHappensEvenWhenTheCancelCannotBeSent()
    {
        var rig = new Rig();
        rig.Announce("g1", "s1", "sentence-1");
        rig.Receive(Frame("g1", "s1", 0, A, endOfSegment: true));
        rig.Player.Open();
        rig.Socket.FailSends = true;

        await Assert.ThrowsAsync<InvalidOperationException>(
            () => rig.Interruption.StopAsync(CancelReason.UserStop, CancellationToken.None));

        Assert.Equal(PlaybackStatus.Stopped, rig.Player.CurrentSnapshot.Status);
        Assert.Contains(rig.Timeline.Snapshot(), e => e.Milestone == PlaybackMilestone.CancelFailed);
    }

    [Fact]
    public void DisconnectFencesQueuedAudioAndReconnectCannotRevive()
    {
        var rig = new Rig();
        rig.Announce("g1", "s1", "sentence-1");
        rig.Queue.RegisterSegment("g1", "s2", "sentence-2");
        rig.Receive(Frame("g1", "s1", 0, A, endOfSegment: true));
        rig.Receive(Frame("g1", "s2", 1, B, endOfSegment: true));

        rig.Socket.Drop();
        Assert.Equal(0, rig.Queue.QueuedCount);

        // A stale frame of g1 replayed after reconnect, even if re-admitted
        // by mistake, is still fenced.
        rig.Processor.AdmitGeneration("g1");
        rig.Receive(Frame("g1", "s3", 2, A, endOfSegment: true));
        Assert.Single(rig.Player.Played);
        Assert.Contains(rig.Rejections, r => r.Reason == "fenced");
    }

    [Fact]
    public void NewGenerationSupersedesTheCurrentOne()
    {
        var rig = new Rig();
        rig.Announce("g1", "s1", "sentence-1");
        rig.Receive(Frame("g1", "s1", 0, A, endOfSegment: true));
        rig.Player.Open();

        rig.Announce("g2", "t1", "sentence-9");
        rig.Receive(Frame("g2", "t1", 0, B, endOfSegment: true, endOfGeneration: true));

        Assert.Equal(1, rig.Player.StopCount);
        Assert.Equal("g2", rig.Player.Played.Last().GenerationId);
    }

    [Fact]
    public void AudioForASegmentThatWasNeverAnnouncedIsNotPlayed()
    {
        var rig = new Rig();
        rig.Announce("g1", "s1", "sentence-1");

        rig.Receive(Frame("g1", "unknown", 0, A, endOfSegment: true));

        Assert.Empty(rig.Player.Played);
        Assert.Contains(rig.Timeline.Snapshot(), e => e.Detail == "unannounced_segment");
    }

    [Fact]
    public void UnsupportedMediaTypeKeepsTextAndSaysSo()
    {
        var rig = new Rig();
        rig.Announce("g1", "s1", "sentence-1");

        rig.Receive(Frame("g1", "s1", 0, A, endOfSegment: true, mediaType: "audio/x-unknown"));

        Assert.Empty(rig.Player.Played);
        Assert.Single(rig.Statuses);
        Assert.Contains(rig.Timeline.Snapshot(), e => e.Detail == "unsupported_media_type");
    }

    [Fact]
    public void PlaybackFailureDropsTheRestOfTheGenerationInsteadOfSkippingAhead()
    {
        var rig = new Rig();
        rig.Announce("g1", "s1", "sentence-1");
        rig.Queue.RegisterSegment("g1", "s2", "sentence-2");
        rig.Receive(Frame("g1", "s1", 0, A, endOfSegment: true));
        rig.Receive(Frame("g1", "s2", 1, B, endOfSegment: true));

        rig.Queue.OnPlaybackFailed(null, "g1");

        Assert.Single(rig.Player.Played);
        Assert.Equal(0, rig.Queue.QueuedCount);
        Assert.Single(rig.Statuses);
    }

    [Fact]
    public void QueueIsBounded()
    {
        var rig = new Rig();
        rig.Announce("g1", "s0", "sentence-0");
        for (var i = 1; i <= SegmentPlaybackQueue.MaxQueuedSegments + 2; i++)
        {
            rig.Queue.RegisterSegment("g1", $"s{i}", $"sentence-{i}");
        }

        for (var i = 0; i <= SegmentPlaybackQueue.MaxQueuedSegments + 2; i++)
        {
            rig.Receive(Frame("g1", $"s{i}", i, A, endOfSegment: true));
        }

        Assert.Equal(SegmentPlaybackQueue.MaxQueuedSegments, rig.Queue.QueuedCount);
        Assert.NotEmpty(rig.Statuses);
    }

    [Fact]
    public void AssemblerDiscardsAnIncompleteSegmentWhenAnotherStarts()
    {
        var assembler = new SegmentAudioAssembler();
        var completed = new List<CompleteSegmentAudio>();
        var discarded = new List<DiscardedSegmentAudio>();
        assembler.SegmentCompleted += (_, s) => completed.Add(s);
        assembler.SegmentDiscarded += (_, d) => discarded.Add(d);

        assembler.OnAudioBytesAdmitted(null, (Header("g1", "s1", 0, false), A));
        assembler.OnAudioBytesAdmitted(null, (Header("g1", "s2", 1, true), B));

        Assert.Equal("incomplete_segment", Assert.Single(discarded).Reason);
        Assert.Equal("bbb", Encoding.ASCII.GetString(Assert.Single(completed).Audio));
    }

    [Fact]
    public void AssemblerDiscardsOversizedAndMixedEncodingSegmentsRatherThanTruncating()
    {
        var assembler = new SegmentAudioAssembler();
        var completed = new List<CompleteSegmentAudio>();
        var discarded = new List<DiscardedSegmentAudio>();
        assembler.SegmentCompleted += (_, s) => completed.Add(s);
        assembler.SegmentDiscarded += (_, d) => discarded.Add(d);

        var big = new byte[SegmentAudioAssembler.MaxSegmentBytes / 2 + 1];
        assembler.OnAudioBytesAdmitted(null, (Header("g1", "s1", 0, false), big));
        assembler.OnAudioBytesAdmitted(null, (Header("g1", "s1", 1, true), big));

        assembler.OnAudioBytesAdmitted(null, (Header("g1", "s2", 2, false), A));
        assembler.OnAudioBytesAdmitted(null, (Header("g1", "s2", 3, true) with { MediaType = "audio/wav" }, B));

        Assert.Empty(completed);
        Assert.Equal(2, discarded.Count);
    }

    private static AudioFrameHeader Header(string generationId, string segmentId, long sequence, bool endOfSegment) =>
        new()
        {
            Version = 1,
            GenerationId = generationId,
            SegmentId = segmentId,
            Sequence = sequence,
            EndOfSegment = endOfSegment,
            EndOfGeneration = false,
            MediaType = "audio/mpeg",
        };
}
