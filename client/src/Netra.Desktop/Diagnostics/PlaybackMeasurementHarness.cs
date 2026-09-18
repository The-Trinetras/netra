using System.IO;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Windows.Threading;
using Netra.Desktop.Audio;
using Netra.Desktop.Networking;
using Netra.Desktop.Protocol.Dto;
using Netra.Desktop.State;

namespace Netra.Desktop.Diagnostics;

// Local hardware measurement: `Netra.Desktop.exe --measure-playback --out <file>
// [--trials N] [--stop-after-ms M] [--audible]`.
//
// Drives the PRODUCTION audio path (frame admission, segment assembly,
// bounded queue, temp-file staging, WPF MediaPlayer, InterruptionController
// STOP) with a tone generated here, then records on one monotonic client
// clock: first frame -> player opened, STOP requested -> player stopped and
// closed, and late frames after STOP. No server, provider or network call:
// the connection is deliberately not connected, so each STOP also exercises
// "cancel could not be sent" while local silence must still happen.
//
// What it cannot show: that sound physically left the speaker, or when it
// became inaudible. Silent by default (volume 0) so running it disturbs no
// one; --audible lets a listener confirm output. Acoustic STOP-to-silence
// needs loopback capture or a listener and stays an explicit hardware gate.
public sealed class PlaybackMeasurementHarness
{
    private const int SampleRate = 16_000;
    private const int FrameAudioBytes = 4096;

    private readonly Dispatcher _dispatcher;
    private readonly int _trials;
    private readonly int _stopAfterMs;
    private readonly bool _audible;

    public PlaybackMeasurementHarness(Dispatcher dispatcher, int trials, int stopAfterMs, bool audible)
    {
        _dispatcher = dispatcher;
        _trials = trials;
        _stopAfterMs = stopAfterMs;
        _audible = audible;
    }

    public static bool TryParse(string[] args, out string outPath, out int trials, out int stopAfterMs, out bool audible)
    {
        outPath = string.Empty;
        trials = 20;
        stopAfterMs = 400;
        audible = false;
        if (!args.Contains("--measure-playback"))
        {
            return false;
        }

        for (var i = 0; i < args.Length; i++)
        {
            switch (args[i])
            {
                case "--out" when i + 1 < args.Length:
                    outPath = args[++i];
                    break;
                case "--trials" when i + 1 < args.Length && int.TryParse(args[i + 1], out var t) && t is > 0 and <= 500:
                    trials = t;
                    i++;
                    break;
                case "--stop-after-ms" when i + 1 < args.Length && int.TryParse(args[i + 1], out var s) && s is >= 0 and <= 10_000:
                    stopAfterMs = s;
                    i++;
                    break;
                case "--audible":
                    audible = true;
                    break;
            }
        }

        if (string.IsNullOrWhiteSpace(outPath))
        {
            outPath = Path.Combine(Environment.CurrentDirectory, $"netra-playback-measurement-{DateTime.Now:yyyyMMdd-HHmmss}.json");
        }

        return true;
    }

    public async Task<string> RunAsync()
    {
        var timeline = new PlaybackTimeline();
        var sessionState = new ClientSessionState();
        sessionState.Initialize(Guid.NewGuid(), sessionVersion: 1);
        await using var socket = new NetraWebSocketClient(); // never connected
        var connection = new ConnectionManager(socket, sessionState);
        using var player = new PlaybackController { Volume = _audible ? 0.5 : 0.0 };
        var interruption = new InterruptionController(player, connection, () => sessionState.CurrentGenerationId, timeline);
        var processor = new BinaryAudioFrameProcessor(interruption);
        var assembler = new SegmentAudioAssembler();
        using var store = new TempFileSegmentAudioStore();
        using var queue = new SegmentPlaybackQueue(player, interruption, store, timeline);
        player.PlaybackFailed += queue.OnPlaybackFailed;
        interruption.GenerationFenced += (_, _) =>
        {
            processor.ClearActiveGeneration();
            assembler.Reset();
        };
        processor.AudioBytesAdmitted += assembler.OnAudioBytesAdmitted;
        processor.FrameRejected += (_, r) => timeline.Record(PlaybackMilestone.FrameRejected, r.GenerationId, detail: r.Reason);
        assembler.SegmentStarted += (_, s) => timeline.Record(PlaybackMilestone.FirstFrameReceived, s.GenerationId, s.SegmentId);
        assembler.SegmentCompleted += (_, s) =>
        {
            timeline.Record(PlaybackMilestone.SegmentAudioComplete, s.GenerationId, s.SegmentId);
            queue.Enqueue(s);
        };

        var wav = Tone(seconds: 3.0);
        var trialResults = new List<object>();
        var lateFramesRejected = 0;
        processor.FrameRejected += (_, _) => lateFramesRejected++;

        for (var trial = 0; trial < _trials; trial++)
        {
            var generationId = Guid.NewGuid().ToString();
            var requestId = Guid.NewGuid().ToString();
            const string segmentId = "seg-0";
            timeline.LinkGeneration(requestId, generationId);
            timeline.Record(PlaybackMilestone.SegmentTextReceived, generationId, segmentId);
            processor.AdmitGeneration(generationId);
            sessionState.CurrentGenerationId = generationId;
            queue.RegisterSegment(generationId, segmentId, "sentence-0");

            var frames = Frames(generationId, segmentId, wav, trial * 10_000L);
            var opened = WaitForStatus(player, PlaybackStatus.Playing, TimeSpan.FromSeconds(5));
            foreach (var frame in frames)
            {
                processor.OnBinaryMessageReceived(null, frame);
            }

            var openedOk = await opened;
            string outcome;
            long positionBeforeStopMs = 0;
            long positionAfterStopMs = 0;
            if (!openedOk)
            {
                outcome = player.CurrentSnapshot.Status == PlaybackStatus.Stopped ? "media_failed" : "not_opened_within_5s";
            }
            else
            {
                await Task.Delay(_stopAfterMs);
                positionBeforeStopMs = player.PlayerPositionMs;
                try
                {
                    await interruption.StopAsync(CancelReason.UserStop, CancellationToken.None);
                }
                catch (InvalidOperationException)
                {
                    // Expected: not connected. Local stop already happened.
                }

                await Task.Delay(100);
                positionAfterStopMs = player.PlayerPositionMs;
                outcome = player.HasSource ? "player_still_has_source" : "stopped_and_closed";
            }

            // Late audio for the stopped generation (a replayed tail).
            processor.OnBinaryMessageReceived(null, Frames(generationId, "seg-late", wav[..FrameAudioBytes], trial * 10_000L + 5_000)[0]);

            trialResults.Add(new
            {
                trial,
                generation_id = generationId,
                outcome,
                position_before_stop_ms = positionBeforeStopMs,
                position_100ms_after_stop_ms = positionAfterStopMs,
            });

            await Task.Delay(150);
        }

        var stops = timeline.StopToLocalStopMilliseconds();
        var startup = FirstFrameToPlaybackStarted(timeline);
        var summary = new
        {
            kind = "netra.client.playback_measurement",
            labelled = "Real WPF MediaPlayer/Media Foundation on this machine with a locally generated tone. "
                + "No server, provider or network. Acoustic output/silence NOT measured.",
            audible = _audible,
            trials = _trials,
            stop_after_ms = _stopAfterMs,
            tone = new { sample_rate = SampleRate, seconds = 3.0, media_type = "audio/wav" },
            stop_to_player_stopped_ms = Stats(stops),
            first_frame_to_player_opened_ms = Stats(startup),
            late_frames_rejected = lateFramesRejected,
            trial_results = trialResults,
            timeline = JsonDocument.Parse(timeline.ExportJson("--measure-playback harness")).RootElement,
        };

        return JsonSerializer.Serialize(summary, new JsonSerializerOptions { WriteIndented = true });
    }

    private Task<bool> WaitForStatus(PlaybackController player, PlaybackStatus wanted, TimeSpan timeout)
    {
        var done = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        EventHandler<PlaybackSnapshot>? handler = null;
        handler = (_, snapshot) =>
        {
            if (snapshot.Status == wanted)
            {
                done.TrySetResult(true);
            }
            else if (snapshot.Status == PlaybackStatus.Stopped)
            {
                done.TrySetResult(false);
            }
        };
        player.SnapshotChanged += handler;
        var timer = new DispatcherTimer(timeout, DispatcherPriority.Normal, (_, _) => done.TrySetResult(false), _dispatcher);
        timer.Start();
        return done.Task.ContinueWith(
            t =>
            {
                timer.Stop();
                player.SnapshotChanged -= handler;
                return t.Result;
            },
            TaskScheduler.FromCurrentSynchronizationContext());
    }

    private static List<double> FirstFrameToPlaybackStarted(PlaybackTimeline timeline)
    {
        var firstFrame = new Dictionary<string, long>(StringComparer.Ordinal);
        var results = new List<double>();
        foreach (var entry in timeline.Snapshot())
        {
            if (entry.GenerationId is null)
            {
                continue;
            }

            if (entry.Milestone == PlaybackMilestone.FirstFrameReceived)
            {
                firstFrame.TryAdd(entry.GenerationId, entry.Timestamp);
            }
            else if (entry.Milestone == PlaybackMilestone.PlaybackStarted && firstFrame.TryGetValue(entry.GenerationId, out var start))
            {
                results.Add(timeline.ToMilliseconds(entry.Timestamp - start));
            }
        }

        return results;
    }

    private static object Stats(IReadOnlyList<double> values)
    {
        if (values.Count == 0)
        {
            return new { n = 0 };
        }

        var sorted = values.OrderBy(v => v).ToArray();
        double Pct(double p) => sorted[Math.Min(sorted.Length - 1, (int)Math.Round(p / 100 * (sorted.Length - 1)))];
        return new
        {
            n = sorted.Length,
            min = Math.Round(sorted[0], 3),
            p50 = Math.Round(Pct(50), 3),
            p95 = Math.Round(Pct(95), 3),
            max = Math.Round(sorted[^1], 3),
        };
    }

    private static byte[] Tone(double seconds)
    {
        var samples = (int)(SampleRate * seconds);
        using var stream = new MemoryStream();
        using var writer = new BinaryWriter(stream, Encoding.ASCII, leaveOpen: true);
        writer.Write(Encoding.ASCII.GetBytes("RIFF"));
        writer.Write(36 + samples * 2);
        writer.Write(Encoding.ASCII.GetBytes("WAVEfmt "));
        writer.Write(16);
        writer.Write((short)1);
        writer.Write((short)1);
        writer.Write(SampleRate);
        writer.Write(SampleRate * 2);
        writer.Write((short)2);
        writer.Write((short)16);
        writer.Write(Encoding.ASCII.GetBytes("data"));
        writer.Write(samples * 2);
        for (var i = 0; i < samples; i++)
        {
            writer.Write((short)(Math.Sin(2 * Math.PI * 440 * i / SampleRate) * 6000));
        }

        writer.Flush();
        return stream.ToArray();
    }

    private static List<byte[]> Frames(string generationId, string segmentId, byte[] audio, long firstSequence)
    {
        var frames = new List<byte[]>();
        for (var offset = 0; offset < audio.Length; offset += FrameAudioBytes)
        {
            var chunk = audio.AsSpan(offset, Math.Min(FrameAudioBytes, audio.Length - offset));
            var last = offset + FrameAudioBytes >= audio.Length;
            var header = JsonSerializer.SerializeToUtf8Bytes(new Dictionary<string, object>
            {
                ["version"] = 1,
                ["generation_id"] = generationId,
                ["segment_id"] = segmentId,
                ["sequence"] = firstSequence + frames.Count,
                ["end_of_segment"] = last,
                ["end_of_generation"] = last,
                ["media_type"] = "audio/wav",
            });
            var frame = new byte[4 + header.Length + chunk.Length];
            frame[0] = (byte)(header.Length >> 24);
            frame[1] = (byte)(header.Length >> 16);
            frame[2] = (byte)(header.Length >> 8);
            frame[3] = (byte)header.Length;
            header.CopyTo(frame, 4);
            chunk.CopyTo(frame.AsSpan(4 + header.Length));
            frames.Add(frame);
        }

        return frames;
    }
}
