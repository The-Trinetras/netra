using System.Text;
using System.Text.Json;
using Netra.Desktop.Audio;
using Netra.Desktop.Protocol;
using Xunit;

namespace Netra.Desktop.Tests;

// Every structural rule in
// shared/contracts/protocol/v1/audio_frame_header.schema.json, exercised
// against the actual parser the receive path uses (BinaryAudioFrame.Parse),
// not a standalone decoder helper never wired to anything.
public sealed class BinaryAudioFrameTests
{
    [Fact]
    public void RoundTripsHeaderAndAudioBytes()
    {
        var audioBytes = new byte[] { 0, 1, 2, 3 };
        var frame = BuildFrame(HeaderJson(sequence: 5), audioBytes);

        var (header, remaining) = BinaryAudioFrame.Parse(frame);

        Assert.Equal(5, header.Sequence);
        Assert.Equal("gen-1", header.GenerationId);
        Assert.True(remaining.Span.SequenceEqual(audioBytes));
    }

    [Fact]
    public void RejectsFrameShorterThanLengthPrefix()
    {
        Assert.Throws<AudioFrameException>(() => BinaryAudioFrame.Parse(new byte[] { 0, 0 }));
    }

    [Fact]
    public void RejectsZeroDeclaredLength()
    {
        var frame = LengthPrefix(0).Concat(Encoding.UTF8.GetBytes("remaining")).ToArray();
        Assert.Throws<AudioFrameException>(() => BinaryAudioFrame.Parse(frame));
    }

    [Fact]
    public void RejectsDeclaredLengthExceedingActualFrameBytes()
    {
        // The length must be validated against actual available bytes
        // BEFORE any attempt to parse — the case a corrupt frame would use
        // to make a naive parser read past the buffer.
        var frame = LengthPrefix(1000).Concat(Encoding.UTF8.GetBytes("too short")).ToArray();
        Assert.Throws<AudioFrameException>(() => BinaryAudioFrame.Parse(frame));
    }

    [Fact]
    public void RejectsHeaderExceeding16KiB()
    {
        var oversizedHeader = new byte[BinaryAudioFrame.MaxHeaderBytes + 1];
        var frame = LengthPrefix(oversizedHeader.Length).Concat(oversizedHeader).ToArray();
        Assert.Throws<AudioFrameException>(() => BinaryAudioFrame.Parse(frame));
    }

    [Fact]
    public void RejectsUnknownHeaderField()
    {
        var raw = new Dictionary<string, object?>
        {
            ["version"] = 1,
            ["generation_id"] = "gen-1",
            ["segment_id"] = "seg-1",
            ["sequence"] = 0,
            ["end_of_segment"] = false,
            ["end_of_generation"] = false,
            ["media_type"] = "audio/mpeg",
            ["unexpected_field"] = "value",
        };
        var frame = BuildFrame(JsonSerializer.Serialize(raw), Array.Empty<byte>());

        Assert.Throws<AudioFrameException>(() => BinaryAudioFrame.Parse(frame));
    }

    [Fact]
    public void RejectsWrongFieldType()
    {
        var raw = new Dictionary<string, object?>
        {
            ["version"] = 1,
            ["generation_id"] = "gen-1",
            ["segment_id"] = "seg-1",
            ["sequence"] = "not-a-number",
            ["end_of_segment"] = false,
            ["end_of_generation"] = false,
            ["media_type"] = "audio/mpeg",
        };
        var frame = BuildFrame(JsonSerializer.Serialize(raw), Array.Empty<byte>());

        Assert.Throws<AudioFrameException>(() => BinaryAudioFrame.Parse(frame));
    }

    [Fact]
    public void RejectsUnsupportedVersion()
    {
        var frame = BuildFrame(HeaderJson(version: 2), Array.Empty<byte>());
        Assert.Throws<AudioFrameException>(() => BinaryAudioFrame.Parse(frame));
    }

    [Fact]
    public void SequenceTrackerAdmitsStrictlyIncreasingSequence()
    {
        var tracker = new GenerationSequenceTracker("gen-1");
        Assert.True(tracker.Admit(Header(sequence: 0)));
        Assert.True(tracker.Admit(Header(sequence: 1)));
    }

    [Fact]
    public void SequenceTrackerPermitsGaps()
    {
        var tracker = new GenerationSequenceTracker("gen-1");
        Assert.True(tracker.Admit(Header(sequence: 0)));
        Assert.True(tracker.Admit(Header(sequence: 5)));
    }

    [Fact]
    public void SequenceTrackerRejectsDuplicateSequence()
    {
        var tracker = new GenerationSequenceTracker("gen-1");
        Assert.True(tracker.Admit(Header(sequence: 3)));
        Assert.False(tracker.Admit(Header(sequence: 3)));
    }

    [Fact]
    public void SequenceTrackerRejectsDecreasingSequence()
    {
        var tracker = new GenerationSequenceTracker("gen-1");
        Assert.True(tracker.Admit(Header(sequence: 5)));
        Assert.False(tracker.Admit(Header(sequence: 2)));
    }

    [Fact]
    public void SequenceTrackerRejectsFrameForADifferentGeneration()
    {
        var tracker = new GenerationSequenceTracker("gen-1");
        Assert.False(tracker.Admit(Header(generationId: "gen-2", sequence: 0)));
    }

    private static AudioFrameHeader Header(string generationId = "gen-1", long sequence = 0) => new()
    {
        Version = 1,
        GenerationId = generationId,
        SegmentId = "seg-1",
        Sequence = sequence,
        EndOfSegment = false,
        EndOfGeneration = false,
        MediaType = "audio/mpeg",
    };

    private static string HeaderJson(int version = 1, long sequence = 0) =>
        JsonSerializer.Serialize(new Dictionary<string, object?>
        {
            ["version"] = version,
            ["generation_id"] = "gen-1",
            ["segment_id"] = "seg-1",
            ["sequence"] = sequence,
            ["end_of_segment"] = false,
            ["end_of_generation"] = false,
            ["media_type"] = "audio/mpeg",
        });

    private static byte[] LengthPrefix(int length)
    {
        var bytes = new byte[4];
        bytes[0] = (byte)(length >> 24);
        bytes[1] = (byte)(length >> 16);
        bytes[2] = (byte)(length >> 8);
        bytes[3] = (byte)length;
        return bytes;
    }

    private static byte[] BuildFrame(string headerJson, byte[] audioBytes)
    {
        var headerBytes = Encoding.UTF8.GetBytes(headerJson);
        return LengthPrefix(headerBytes.Length).Concat(headerBytes).Concat(audioBytes).ToArray();
    }
}
