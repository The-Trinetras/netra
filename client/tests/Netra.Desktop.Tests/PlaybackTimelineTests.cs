using System.IO;
using System.Text;
using System.Text.Json;
using Netra.Desktop.Audio;
using Netra.Desktop.Diagnostics;
using Xunit;

namespace Netra.Desktop.Tests;

public sealed class PlaybackTimelineTests
{
    [Fact]
    public void StopToLocalStopIsMeasuredOnOneClock()
    {
        long now = 0;
        var timeline = new PlaybackTimeline(() => now, frequency: 1000);

        now = 100;
        timeline.Record(PlaybackMilestone.StopRequested, "g1");
        now = 103;
        timeline.Record(PlaybackMilestone.LocalStopReturned, "g1");
        now = 500;
        timeline.Record(PlaybackMilestone.StopRequested, "g2");
        now = 501;
        timeline.Record(PlaybackMilestone.LocalStopReturned, "g2");

        Assert.Equal(new[] { 3.0, 1.0 }, timeline.StopToLocalStopMilliseconds());
    }

    [Fact]
    public void EntriesAreJoinedToTheOriginatingRequestByGeneration()
    {
        var timeline = new PlaybackTimeline();
        timeline.LinkGeneration("req-1", "g1");

        timeline.Record(PlaybackMilestone.PlaybackStarted, "g1", "s1");
        timeline.Record(PlaybackMilestone.PlaybackStarted, "g-unknown", "s1");

        var entries = timeline.Snapshot();
        Assert.Equal("req-1", entries[0].RequestId);
        Assert.Null(entries[1].RequestId);
    }

    [Fact]
    public void TimelineIsBoundedAndCountsWhatItDropped()
    {
        var timeline = new PlaybackTimeline();
        for (var i = 0; i < PlaybackTimeline.MaxEntries + 10; i++)
        {
            timeline.Record(PlaybackMilestone.FrameRejected, "g1", detail: "sequence");
        }

        Assert.Equal(PlaybackTimeline.MaxEntries, timeline.Snapshot().Count);
        Assert.Equal(10, timeline.DroppedEntries);
    }

    [Fact]
    public void ExportContainsIdsAndTimingsButNoFreeTextFields()
    {
        var timeline = new PlaybackTimeline();
        timeline.LinkGeneration("req-1", "g1");
        timeline.Record(PlaybackMilestone.SegmentTextReceived, "g1", "s1");
        timeline.Record(PlaybackMilestone.AckSent, "g1", "s1", "Started");

        using var document = JsonDocument.Parse(timeline.ExportJson("unit test"));
        var root = document.RootElement;
        Assert.Equal("netra.client.playback_timeline", root.GetProperty("kind").GetString());
        var entryFields = root.GetProperty("entries")[0].EnumerateObject().Select(p => p.Name).ToHashSet();
        Assert.Equal(new HashSet<string> { "t_ms", "milestone", "request_id", "generation_id", "segment_id", "detail" }, entryFields);
    }

    [Fact]
    public void TempFileStoreWritesPlayableFilesAndRemovesThem()
    {
        var directory = Path.Combine(Path.GetTempPath(), "netra-tests", Guid.NewGuid().ToString("N"));
        var store = new TempFileSegmentAudioStore(directory);

        var uri = store.Stage(new CompleteSegmentAudio("g1", "s1", "audio/mpeg", Encoding.ASCII.GetBytes("x"), true));
        Assert.NotNull(uri);
        Assert.EndsWith(".mp3", uri!.LocalPath);
        Assert.True(File.Exists(uri.LocalPath));
        Assert.DoesNotContain("g1", uri.LocalPath); // named by a random id, not protocol ids

        Assert.Null(store.Stage(new CompleteSegmentAudio("g1", "s2", "audio/ogg", new byte[] { 1 }, true)));

        store.Release(uri);
        Assert.False(File.Exists(uri.LocalPath));
        store.Dispose();
        Assert.False(Directory.Exists(directory));
    }
}
