using System.Buffers.Binary;
using System.Text.Json;
using Netra.Desktop.Protocol;

namespace Netra.Desktop.Speech;

// One microphone header (client to server), decision D-MIC. The shared
// contract (C1) is Arshad's to draft; this is the client's side of the
// decided fields, mirroring the approved server-to-client framing so one
// parser shape serves both directions. Unknown fields are not sent.
public sealed record MicrophoneFrameHeader
{
    public int Version { get; init; } = MicrophoneFrame.Version;
    public required Guid CaptureId { get; init; }

    // Per capture, from 0, strictly increasing and gap-free from this client.
    public required long Sequence { get; init; }

    // True on exactly one frame per capture: the last one. It may carry no audio.
    public required bool EndOfUtterance { get; init; }
    public string MediaType { get; init; } = MicrophoneFrame.MediaType;
}

// Builds one WebSocket binary message:
//   [4-byte unsigned big-endian header length][UTF-8 JSON header][audio bytes]
// Audio is audio/L16 at 16 kHz mono. RFC 2586/3551 define L16 samples in
// network (big-endian) byte order, while WinMM delivers little-endian, so
// the samples are byte-swapped here and nowhere else. (A server feeding
// Deepgram's little-endian `linear16` swaps them back; C1 review item.)
public static class MicrophoneFrame
{
    public const int Version = 1;
    public const string MediaType = "audio/L16;rate=16000";

    // The approved header bound for server frames; kept identical.
    public const int MaxHeaderBytes = 16 * 1024;

    // Client-local batching, not a wire limit: 100 ms of 16 kHz 16-bit mono.
    // Ten small messages a second; a release flushes the rest immediately.
    public const int AudioBytesPerFrame = 3200;

    public static byte[] Encode(MicrophoneFrameHeader header, ReadOnlySpan<byte> littleEndianPcm)
    {
        if (littleEndianPcm.Length % 2 != 0)
        {
            throw new ArgumentException("16-bit PCM must contain whole samples.", nameof(littleEndianPcm));
        }

        var json = JsonSerializer.SerializeToUtf8Bytes(header, NetraJsonSerialization.Options);
        if (json.Length > MaxHeaderBytes)
        {
            throw new InvalidOperationException("Microphone frame header exceeds the header limit.");
        }

        var frame = new byte[4 + json.Length + littleEndianPcm.Length];
        BinaryPrimitives.WriteUInt32BigEndian(frame, (uint)json.Length);
        json.CopyTo(frame, 4);

        var audio = frame.AsSpan(4 + json.Length);
        for (var i = 0; i < littleEndianPcm.Length; i += 2)
        {
            audio[i] = littleEndianPcm[i + 1];
            audio[i + 1] = littleEndianPcm[i];
        }

        return frame;
    }
}
