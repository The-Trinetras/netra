using Netra.Desktop.Protocol.Dto;
using Netra.Desktop.Speech;
using Xunit;

namespace Netra.Desktop.Tests;

// Push-to-talk voice input over the D-MIC microphone protocol (C1 pending):
// nothing leaves the client before the server accepts a capture, only one
// final per press becomes a turn, and STOP, a lost connection or a refusal
// end the capture with an accessible status instead of silence.
public sealed class MicrophoneCaptureTests
{
    private static readonly VoiceInputOptions Options = new();

    [Fact]
    public async Task NothingIsSentBeforeTheStartMessageCompletes()
    {
        var (mic, channel, pcm, _, _, _) = Rig.Create();

        channel.StartGate = new(TaskCreationOptions.RunContinuationsAsynchronously);
        var starting = mic.StartListeningAsync(CancellationToken.None);
        Assert.True(pcm.Speak(Pcm.Samples(320, 1)));
        Assert.True(pcm.Speak(Pcm.Samples(320, 400)));
        mic.StopListening();
        await Eventually.SettleAsync();

        var start = channel.SingleStart();
        Assert.NotEqual(Guid.Empty, start.Payload.CaptureId);
        Assert.Empty(channel.FramesSnapshot());
        Assert.Equal(1, pcm.Opened);
        channel.StartGate.SetResult();
        await starting;
        await Eventually.TrueAsync(() => channel.FramesSnapshot().Any(f => f.EndOfUtterance), "audio follows the start without an acknowledgement");
        mic.Dispose();
    }

    [Fact]
    public async Task AudioStreamsInOrderWithoutReadyAndReleaseEndsTheUtterance()
    {
        var (mic, channel, pcm, _, _, _) = Rig.Create();
        var spoken = new List<byte[]>();

        await mic.StartListeningAsync(CancellationToken.None);
        for (var i = 0; i < 6; i++)
        {
            var chunk = Pcm.Samples(320, i * 320);
            spoken.Add(chunk);
            Assert.True(pcm.Speak(chunk));
        }

        var (requestId, start) = channel.SingleStart();
        await Eventually.TrueAsync(() => channel.FramesSnapshot().Length == 1, "the first full frame is sent");

        pcm.FinalChunk = Pcm.Samples(100, 5000);
        spoken.Add(pcm.FinalChunk);
        mic.StopListening();
        await Eventually.TrueAsync(() => channel.FramesSnapshot().Length == 2, "the end frame is sent");

        var frames = channel.FramesSnapshot();
        Assert.All(frames, f => Assert.Equal(start.CaptureId, f.CaptureId));
        Assert.Equal(new long[] { 0, 1 }, frames.Select(f => f.Sequence));
        Assert.Equal(new[] { false, true }, frames.Select(f => f.EndOfUtterance));
        Assert.Equal(MicrophoneFrame.AudioBytesPerFrame, frames[0].Audio.Length);
        var received = frames.SelectMany(f => f.Audio).ToArray();
        Assert.Equal(spoken.SelectMany(c => c).ToArray(), received);
        Assert.False(mic.IsRecognitionAvailable); // only a transcript proves recognition
    }

    [Fact]
    public async Task ARefusedStartSendsNoAudioAndSaysVoiceIsNotAvailable()
    {
        var (mic, channel, pcm, statuses, _, _) = Rig.Create();

        await mic.StartListeningAsync(CancellationToken.None);
        pcm.Speak(Pcm.Samples(320, 1));
        var (requestId, _) = channel.SingleStart();
        channel.Error(requestId, ErrorCode.InvalidRequest, "invalid message");
        mic.StopListening();
        await Eventually.SettleAsync();

        Assert.All(channel.FramesSnapshot(), f => Assert.Empty(f.Audio));
        Assert.True(pcm.Aborted);
        Assert.False(mic.IsRecognitionAvailable);
        Assert.True(mic.OwnsRequest(requestId));
        var status = statuses.Last(s => s.Announce);
        Assert.Equal(VoiceInputState.Unavailable, status.State);
        Assert.Equal("Voice input is not available on this Netra server yet. Type your question instead.", status.Message);
    }

    [Fact]
    public async Task AProviderRefusalCarriesTheServersSafeMessage()
    {
        var (mic, channel, _, statuses, _, _) = Rig.Create();

        await mic.StartListeningAsync(CancellationToken.None);
        var (requestId, _) = channel.SingleStart();
        channel.Error(requestId, ErrorCode.ResourceUnavailable, "Today's speech allowance is used up.");

        var status = statuses.Last(s => s.Announce);
        Assert.Equal(VoiceInputState.Unavailable, status.State);
        Assert.Equal("Voice input is not available. Today's speech allowance is used up. Type your question instead.", status.Message);
    }

    [Fact]
    public async Task AStalledStartEndsTheCaptureWithoutSendingAudio()
    {
        var (mic, channel, pcm, statuses, _, delays) = Rig.Create();

        channel.StartGate = new(TaskCreationOptions.RunContinuationsAsynchronously);
        var starting = mic.StartListeningAsync(CancellationToken.None);
        pcm.Speak(Pcm.Samples(320, 1));
        delays.Elapse(Options.StartTimeout);
        await Eventually.TrueAsync(() => statuses.Any(s => s.Announce), "the timeout is announced");
        var (requestId, start) = channel.SingleStart();
        channel.StartGate.SetResult();
        await starting;
        await Eventually.SettleAsync();

        Assert.True(pcm.Aborted);
        Assert.Equal(VoiceInputState.Unavailable, statuses.Last(s => s.Announce).State);
        // Only the closing frame for the late acceptance: no audio.
        var frame = Assert.Single(channel.FramesSnapshot());
        Assert.True(frame.EndOfUtterance);
        Assert.Empty(frame.Audio);
    }

    [Fact]
    public async Task InterimTextAndAFinalBeforeTheReleaseAreCaptionsOnly()
    {
        var (mic, channel, _, _, transcripts, _) = Rig.Create();

        await mic.StartListeningAsync(CancellationToken.None);
        var (requestId, start) = channel.SingleStart();
        channel.Transcript(start.CaptureId, requestId, "next", isFinal: false);
        channel.Transcript(start.CaptureId, requestId, "next question", isFinal: true);

        Assert.Equal(2, transcripts.Count);
        Assert.All(transcripts, t => Assert.False(t.IsFinal));
        Assert.True(mic.IsListening);
    }

    [Fact]
    public async Task OnePressGivesExactlyOneFinalEvenWhenFinalsRepeat()
    {
        var (mic, channel, _, _, transcripts, _) = Rig.Create();
        var (requestId, captureId) = await SpeakAndReleaseAsync(mic, channel);

        var transcriptId = Guid.NewGuid();
        channel.Transcript(captureId, requestId, " What is Ohm's law? ", isFinal: true, transcriptId);
        channel.Transcript(captureId, requestId, " What is Ohm's law? ", isFinal: true, transcriptId);
        channel.Transcript(captureId, requestId, "something else", isFinal: true);

        var final = Assert.Single(transcripts, t => t.IsFinal);
        Assert.Equal("What is Ohm's law?", final.Text);
        Assert.Equal(transcriptId, final.TranscriptId);
        Assert.False(mic.IsListening);
    }

    [Fact]
    public async Task ATranscriptForAnotherCaptureIsIgnored()
    {
        var (mic, channel, _, _, transcripts, _) = Rig.Create();
        var (requestId, _) = await SpeakAndReleaseAsync(mic, channel);

        channel.Transcript(Guid.NewGuid(), requestId, "not this capture", isFinal: true);
        await Eventually.SettleAsync();

        Assert.Empty(transcripts);
    }

    [Fact]
    public async Task StopDiscardsTheCaptureClosesTheStreamAndIgnoresItsLateFinal()
    {
        var (mic, channel, pcm, _, transcripts, _) = Rig.Create();

        await mic.StartListeningAsync(CancellationToken.None);
        var (requestId, start) = channel.SingleStart();
        pcm.Speak(Pcm.Samples(1600, 1));
        await Eventually.TrueAsync(() => channel.FramesSnapshot().Length == 1, "a full frame is sent");

        mic.AbortListening();
        pcm.Speak(Pcm.Samples(1600, 1));
        await Eventually.TrueAsync(() => channel.FramesSnapshot().Length == 2, "the closing frame is sent");
        channel.Transcript(start.CaptureId, requestId, "late final", isFinal: true);
        await Eventually.SettleAsync();

        var frames = channel.FramesSnapshot();
        Assert.Equal(2, frames.Length);
        Assert.True(frames[1].EndOfUtterance);
        Assert.Empty(frames[1].Audio);
        Assert.True(pcm.Aborted);
        Assert.Empty(transcripts);
        Assert.False(mic.IsListening);
    }

    [Fact]
    public async Task StopBeforeStartCompletesSendsNoAudioAndClosesTheStream()
    {
        var (mic, channel, pcm, statuses, _, _) = Rig.Create();

        channel.StartGate = new(TaskCreationOptions.RunContinuationsAsynchronously);
        var starting = mic.StartListeningAsync(CancellationToken.None);
        pcm.Speak(Pcm.Samples(1600, 1));
        mic.AbortListening();
        var (requestId, start) = channel.SingleStart();
        channel.StartGate.SetResult();
        await starting;
        await Eventually.TrueAsync(() => channel.FramesSnapshot().Length == 1, "the closing frame is sent");
        await Eventually.SettleAsync();

        var frame = Assert.Single(channel.FramesSnapshot());
        Assert.True(frame.EndOfUtterance);
        Assert.Empty(frame.Audio);
        Assert.DoesNotContain(statuses, s => s.Announce);
        Assert.False(mic.IsRecognitionAvailable);
    }

    [Fact]
    public async Task WithoutAConnectionTheMicrophoneIsNeverOpened()
    {
        var (mic, channel, pcm, statuses, _, _) = Rig.Create();
        channel.IsConnected = false;

        await mic.StartListeningAsync(CancellationToken.None);

        Assert.Equal(0, pcm.Opened);
        Assert.Empty(channel.Starts);
        var status = Assert.Single(statuses);
        Assert.Equal(VoiceInputState.Unavailable, status.State);
        Assert.True(status.Announce);
        Assert.False(mic.IsListening);
    }

    [Fact]
    public async Task ALostConnectionEndsTheCaptureAndVoiceMustBeAcceptedAgain()
    {
        var (mic, channel, pcm, statuses, transcripts, _) = Rig.Create();

        await mic.StartListeningAsync(CancellationToken.None);
        var (requestId, start) = channel.SingleStart();
        channel.Transcript(start.CaptureId, requestId, "listening", isFinal: false);
        Assert.True(mic.IsRecognitionAvailable);
        transcripts.Clear();

        channel.Drop();
        channel.IsConnected = true;
        channel.Transcript(start.CaptureId, requestId, "after the drop", isFinal: true);
        await Eventually.SettleAsync();

        Assert.False(mic.IsRecognitionAvailable);
        Assert.True(pcm.Aborted);
        Assert.Empty(transcripts);
        Assert.Empty(channel.FramesSnapshot());
        Assert.Equal(VoiceInputState.Failed, statuses.Last(s => s.Announce).State);
    }

    [Fact]
    public async Task OverflowingTheBufferStopsTheCaptureInsteadOfDroppingAudio()
    {
        var (mic, channel, pcm, statuses, _, _) = Rig.Create(new VoiceInputOptions { MaxBufferedAudioBytes = 1280 });

        channel.StartGate = new(TaskCreationOptions.RunContinuationsAsynchronously);
        var starting = mic.StartListeningAsync(CancellationToken.None);
        Assert.True(pcm.Speak(Pcm.Samples(320, 1)));
        Assert.True(pcm.Speak(Pcm.Samples(320, 1)));
        Assert.False(pcm.Speak(Pcm.Samples(320, 1)));
        await Eventually.TrueAsync(() => statuses.Any(s => s.Announce), "the failure is announced");
        var (requestId, start) = channel.SingleStart();
        channel.StartGate.SetResult();
        await starting;
        await Eventually.SettleAsync();

        var status = statuses.Last(s => s.Announce);
        Assert.Equal(VoiceInputState.Failed, status.State);
        Assert.Equal("Voice input could not keep up. Please try again. Type your question instead.", status.Message);
        Assert.DoesNotContain(channel.FramesSnapshot(), f => f.Audio.Length > 0);
    }

    [Fact]
    public async Task AMicrophoneThatCannotOpenIsReportedWithItsReason()
    {
        var (mic, _, pcm, statuses, _, _) = Rig.Create();
        pcm.FailOnOpen = new InvalidOperationException("The microphone could not open. Check the device and Windows microphone permission. (Code 4)");

        await mic.StartListeningAsync(CancellationToken.None);
        await Eventually.TrueAsync(() => statuses.Any(s => s.Announce), "the failure is announced");

        var status = statuses.Last(s => s.Announce);
        Assert.Equal(VoiceInputState.Failed, status.State);
        Assert.StartsWith("The microphone could not open.", status.Message);
    }

    [Fact]
    public async Task ASynchronousDeviceFailureIsReportedAndAllowsAnotherPress()
    {
        var channel = new FakeAsrChannel();
        var pcm = new ClosingPcmSource { ThrowOnOpen = true };
        using var mic = new MicrophoneCapture(channel, pcm);
        var statuses = new List<VoiceInputStatus>();
        mic.StatusChanged += (_, status) => statuses.Add(status);

        await mic.StartListeningAsync(CancellationToken.None);

        Assert.False(mic.IsListening);
        Assert.Contains(statuses, s => s.State == VoiceInputState.Failed && s.Message.Contains("device unavailable"));
        pcm.ThrowOnOpen = false;
        await mic.StartListeningAsync(CancellationToken.None);
        Assert.True(mic.IsListening);
        Assert.Equal(2, pcm.Opened);
        mic.AbortListening();
        pcm.CloseFirst.TrySetResult();
    }

    [Fact]
    public async Task AQuickNewPressWaitsForThePreviousDeviceToCloseAndThenTranscribes()
    {
        var channel = new FakeAsrChannel();
        var pcm = new ClosingPcmSource();
        using var mic = new MicrophoneCapture(channel, pcm);
        var final = new TaskCompletionSource<TranscriptReceivedEventArgs>(TaskCreationOptions.RunContinuationsAsynchronously);
        mic.TranscriptReceived += (_, transcript) => { if (transcript.IsFinal) final.TrySetResult(transcript); };

        await mic.StartListeningAsync(CancellationToken.None);
        mic.StopListening();
        await mic.StartListeningAsync(CancellationToken.None);
        Assert.Equal(1, pcm.Opened);
        pcm.CloseFirst.SetResult();
        await pcm.SecondOpened.Task.WaitAsync(TimeSpan.FromSeconds(3));

        var (request, start) = channel.Starts.Last();
        channel.OnFrameSending = frame =>
        {
            if (frame.CaptureId == start.CaptureId && frame.EndOfUtterance)
                channel.Transcript(start.CaptureId, request, "my second question", isFinal: true);
        };
        mic.StopListening();

        Assert.Equal("my second question", (await final.Task.WaitAsync(TimeSpan.FromSeconds(3))).Text);
        Assert.Equal(2, pcm.Opened);
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public async Task ReleasingOrAbortingAQueuedPressNeverReopensTheMicrophone(bool abort)
    {
        var channel = new FakeAsrChannel();
        var pcm = new ClosingPcmSource();
        using var mic = new MicrophoneCapture(channel, pcm);
        await mic.StartListeningAsync(CancellationToken.None);
        mic.StopListening();
        await mic.StartListeningAsync(CancellationToken.None);
        if (abort) mic.AbortListening();
        else mic.StopListening();

        pcm.CloseFirst.SetResult();
        await Eventually.SettleAsync();

        Assert.Equal(1, pcm.Opened);
        Assert.False(mic.IsListening);
    }

    [Fact]
    public async Task AnEmptyFinalSaysSoAndSubmitsNothing()
    {
        var (mic, channel, _, statuses, transcripts, delays) = Rig.Create();
        var (requestId, captureId) = await SpeakAndReleaseAsync(mic, channel);

        channel.Transcript(captureId, requestId, "   ", isFinal: true);

        Assert.DoesNotContain(transcripts, t => t.IsFinal);
        Assert.Equal("Netra did not catch that. Hold the talk key and try again.", statuses.Last(s => s.Announce).Message);
    }

    [Fact]
    public async Task NoFinalInTimeIsReported()
    {
        var (mic, channel, _, statuses, transcripts, delays) = Rig.Create();
        var (requestId, captureId) = await SpeakAndReleaseAsync(mic, channel);

        delays.Elapse(Options.FinalTranscriptTimeout);
        await Eventually.TrueAsync(() => statuses.Any(s => s.Announce), "the timeout is announced");
        channel.Transcript(captureId, requestId, "too late", isFinal: true);

        Assert.Empty(transcripts);
        Assert.Equal("No transcript arrived. Try again, or type your question.", statuses.Last(s => s.Announce).Message);
    }

    [Fact]
    public async Task HoldingPastTheMaximumSendsWhatWasSaid()
    {
        var (mic, channel, pcm, statuses, _, delays) = Rig.Create();

        await mic.StartListeningAsync(CancellationToken.None);
        var (requestId, start) = channel.SingleStart();
        pcm.Speak(Pcm.Samples(320, 1));
        delays.Elapse(Options.MaxCaptureDuration);
        await Eventually.TrueAsync(() => channel.FramesSnapshot().Any(f => f.EndOfUtterance), "the utterance ends");

        Assert.False(mic.IsListening);
        Assert.Equal(640, channel.FramesSnapshot().Sum(f => f.Audio.Length));
        Assert.Equal("Voice input stopped after 60 seconds. Sending what you said.", statuses.Single(s => s.Announce).Message);
    }

    [Fact]
    public async Task ANewPressSupersedesTheLastCaptureWhoseLateFinalIsIgnored()
    {
        var (mic, channel, _, _, transcripts, _) = Rig.Create();
        var (firstRequest, firstCapture) = await SpeakAndReleaseAsync(mic, channel);

        await mic.StartListeningAsync(CancellationToken.None);
        channel.Transcript(firstCapture, firstRequest, "old question", isFinal: true);
        await Eventually.SettleAsync();

        Assert.Empty(transcripts);
        Assert.Equal(2, channel.Starts.Count);
        Assert.True(mic.IsListening);
    }

    [Fact]
    public async Task ALostSendWhileStreamingEndsTheCaptureAudibly()
    {
        var (mic, channel, pcm, statuses, _, _) = Rig.Create();

        await mic.StartListeningAsync(CancellationToken.None);
        var (requestId, start) = channel.SingleStart();
        channel.FailFrameSends = true;
        pcm.Speak(Pcm.Samples(1600, 1));
        await Eventually.TrueAsync(() => statuses.Any(s => s.Announce), "the failure is announced");

        Assert.Equal(VoiceInputState.Failed, statuses.Last(s => s.Announce).State);
        Assert.True(pcm.Aborted);
    }

    // The server may answer the end frame before the send's continuation
    // runs; that final must still count as the whole utterance.
    [Fact]
    public async Task AFinalRacingTheEndFrameSendIsStillAccepted()
    {
        var (mic, channel, _, _, transcripts, _) = Rig.Create();
        await mic.StartListeningAsync(CancellationToken.None);
        var (requestId, start) = channel.SingleStart();
        channel.OnFrameSending = frame =>
        {
            if (frame.EndOfUtterance)
            {
                channel.Transcript(start.CaptureId, requestId, "fast answer", isFinal: true);
            }
        };

        mic.StopListening();
        await Eventually.TrueAsync(() => transcripts.Any(t => t.IsFinal), "the final is accepted");

        Assert.Equal("fast answer", Assert.Single(transcripts).Text);
    }

    [Fact]
    public async Task DisposingEndsACaptureInProgress()
    {
        var (mic, channel, pcm, _, _, _) = Rig.Create();
        await mic.StartListeningAsync(CancellationToken.None);

        mic.Dispose();

        Assert.True(pcm.Aborted);
        Assert.False(mic.IsListening);
        await Assert.ThrowsAsync<ObjectDisposedException>(() => mic.StartListeningAsync(CancellationToken.None));
        Assert.Single(channel.Starts);
    }

    [Fact]
    public async Task ACancelledPressDoesNotOpenTheMicrophone()
    {
        var (mic, channel, pcm, _, _, _) = Rig.Create();

        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => mic.StartListeningAsync(new CancellationToken(true)));

        Assert.Equal(0, pcm.Opened);
        Assert.Empty(channel.Starts);
    }

    private static async Task<(Guid RequestId, Guid CaptureId)> SpeakAndReleaseAsync(MicrophoneCapture mic, FakeAsrChannel channel)
    {
        await mic.StartListeningAsync(CancellationToken.None);
        var (requestId, start) = channel.Starts.Last();
        mic.StopListening();
        await Eventually.TrueAsync(() => channel.FramesSnapshot().Any(f => f.CaptureId == start.CaptureId && f.EndOfUtterance), "the utterance ends");
        return (requestId, start.CaptureId);
    }

    // Models WinMM's exclusive handle: cancellation starts asynchronous
    // cleanup, but the device remains busy until CloseFirst completes.
    private sealed class ClosingPcmSource : IPcmSource
    {
        private int _active;
        public int Opened { get; private set; }
        public bool ThrowOnOpen { get; set; }
        public TaskCompletionSource CloseFirst { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
        public TaskCompletionSource SecondOpened { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);

        public Task CaptureAsync(Func<ReadOnlyMemory<byte>, bool> acceptFrame, CancellationToken stop, CancellationToken abort)
        {
            Opened++;
            if (ThrowOnOpen) throw new InvalidOperationException("Microphone device unavailable.");
            if (Interlocked.Exchange(ref _active, 1) != 0)
                throw new InvalidOperationException("Microphone capture is already running.");
            return RunAsync();

            async Task RunAsync()
            {
                try
                {
                    if (Opened == 1)
                    {
                        await CloseFirst.Task;
                        abort.ThrowIfCancellationRequested();
                    }
                    else
                    {
                        var released = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
                        using var stopRegistration = stop.Register(() => released.TrySetResult());
                        using var abortRegistration = abort.Register(() => released.TrySetCanceled(abort));
                        SecondOpened.TrySetResult();
                        await released.Task;
                    }
                }
                finally
                {
                    Volatile.Write(ref _active, 0);
                }
            }
        }
    }

    private sealed record Rig(
        MicrophoneCapture Mic,
        FakeAsrChannel Channel,
        FakePcmSource Pcm,
        List<VoiceInputStatus> Statuses,
        List<TranscriptReceivedEventArgs> Transcripts,
        ManualDelays Delays)
    {
        public static Rig Create(VoiceInputOptions? options = null)
        {
            var channel = new FakeAsrChannel();
            var pcm = new FakePcmSource();
            var delays = new ManualDelays();
            var mic = new MicrophoneCapture(channel, pcm, options ?? Options, delays.Delay);
            var statuses = new List<VoiceInputStatus>();
            var transcripts = new List<TranscriptReceivedEventArgs>();
            mic.StatusChanged += (_, s) => { lock (statuses) { statuses.Add(s); } };
            mic.TranscriptReceived += (_, t) => { lock (transcripts) { transcripts.Add(t); } };
            return new Rig(mic, channel, pcm, statuses, transcripts, delays);
        }
    }
}
