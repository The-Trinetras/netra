using System.Buffers.Binary;
using Netra.Desktop.Speech;
using Xunit;

namespace Netra.Desktop.Tests;

// Microphone frames (D-MIC; approved C1): the approved server framing shape,
// with the decided header fields and audio/L16 in little-endian byte order.
public sealed class MicrophoneFrameTests
{
    [Fact]
    public void LayoutIsLengthPrefixedStrictHeaderThenLittleEndianSamples()
    {
        var captureId = Guid.NewGuid();
        var pcm = new byte[] { 0x01, 0x02, 0xFF, 0x7F };

        var frame = MicrophoneFrame.Encode(
            new MicrophoneFrameHeader { CaptureId = captureId, Sequence = 7, EndOfUtterance = true }, pcm);

        var declared = (int)BinaryPrimitives.ReadUInt32BigEndian(frame);
        Assert.Equal(frame.Length - 4 - pcm.Length, declared);
        var decoded = MicrophoneFrameReader.Read(frame);
        Assert.Equal(captureId, decoded.CaptureId);
        Assert.Equal(7, decoded.Sequence);
        Assert.True(decoded.EndOfUtterance);
        Assert.Equal("audio/L16;rate=16000", decoded.MediaType);
        Assert.Equal(1, decoded.Version);
        Assert.Equal(pcm, decoded.Audio);
    }

    [Fact]
    public void AnEndFrameMayCarryNoAudio()
    {
        var frame = MicrophoneFrame.Encode(
            new MicrophoneFrameHeader { CaptureId = Guid.NewGuid(), Sequence = 0, EndOfUtterance = true },
            ReadOnlySpan<byte>.Empty);

        Assert.Empty(MicrophoneFrameReader.Read(frame).Audio);
    }

    [Fact]
    public void HalfASampleIsRefused()
    {
        Assert.Throws<ArgumentException>(() => MicrophoneFrame.Encode(
            new MicrophoneFrameHeader { CaptureId = Guid.NewGuid(), Sequence = 0, EndOfUtterance = false },
            new byte[] { 1, 2, 3 }));
    }
}
