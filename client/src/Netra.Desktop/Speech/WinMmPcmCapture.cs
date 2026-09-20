using System.Runtime.InteropServices;

namespace Netra.Desktop.Speech;

// Local device boundary only. PCM here is signed 16-bit LITTLE-endian, mono,
// 16 kHz; MicrophoneFrame preserves this byte order for the D-MIC wire form.
// Eight 20 ms native buffers bound device memory and give the capture thread
// 160 ms of headroom on a busy machine (screen reader, speech, GC) before
// WinMM would run out of buffers. The consumer must enqueue without blocking
// and return false on overflow; no audio is silently dropped.
public sealed class WinMmPcmCapture : IPcmSource
{
    internal const int BufferBytes = 640;
    internal const int BufferCount = 8;
    private readonly IWaveInApi _api;
    private int _active;
    private bool _cleanupFailed;

    public WinMmPcmCapture() : this(new WaveInApi()) { }
    internal WinMmPcmCapture(IWaveInApi api) => _api = api;

    // stop = key release: return the final partial buffer before completion.
    // abort = STOP/disconnect/shutdown: discard remaining audio. The C1 consumer
    // must fence by capture identity as well; neither token submits a turn.
    public Task CaptureAsync(Func<ReadOnlyMemory<byte>, bool> acceptFrame,
        CancellationToken stop, CancellationToken abort = default)
    {
        ArgumentNullException.ThrowIfNull(acceptFrame);
        if (Interlocked.CompareExchange(ref _active, 1, 0) != 0)
            throw new InvalidOperationException("Microphone capture is already running.");
        // A dedicated thread: it blocks on device events for the whole press.
        return Task.Factory.StartNew(() =>
        {
            try
            {
                if (_cleanupFailed)
                    throw new InvalidOperationException("Restart Netra before using the microphone again.");
                Capture(acceptFrame, stop, abort);
            }
            finally { Volatile.Write(ref _active, 0); }
        }, CancellationToken.None, TaskCreationOptions.LongRunning, TaskScheduler.Default);
    }

    private void Capture(Func<ReadOnlyMemory<byte>, bool> accept, CancellationToken stop, CancellationToken abort)
    {
        abort.ThrowIfCancellationRequested();
        if (stop.IsCancellationRequested) return;
        var signal = new ManualResetEvent(false);
        var buffers = new List<WaveBuffer>();
        nint handle = 0;
        bool opened = false;
        int next = 0;
        try
        {
            Check(_api.Open(out handle, signal), "open");
            opened = true;
            for (int i = 0; i < BufferCount; i++)
            {
                abort.ThrowIfCancellationRequested();
                if (stop.IsCancellationRequested) return;
                var buffer = new WaveBuffer();
                buffers.Add(buffer);
                Check(_api.Prepare(handle, buffer.Header), "prepare");
                buffer.Prepared = true;
                Check(_api.Add(handle, buffer.Header), "queue");
            }
            abort.ThrowIfCancellationRequested();
            if (stop.IsCancellationRequested) return;
            Check(_api.Start(handle), "start");
            var waits = new[] { abort.WaitHandle, stop.WaitHandle, signal };
            while (!stop.IsCancellationRequested && !abort.IsCancellationRequested)
            {
                WaitHandle.WaitAny(waits);
                // Reset BEFORE scanning, so a completion during the scan is
                // still signalled for the next iteration. Drain in queue order.
                signal.Reset();
                if (!stop.IsCancellationRequested && !abort.IsCancellationRequested)
                    Drain(requeue: true);
            }
            Check(_api.Reset(handle), "stop");
            abort.ThrowIfCancellationRequested();
            Drain(requeue: false);
        }
        finally
        {
            bool failed = false;
            if (opened)
            {
                failed = _api.Reset(handle) != 0;
                foreach (var buffer in buffers)
                {
                    if (buffer.Prepared && _api.Unprepare(handle, buffer.Header) != 0)
                    {
                        // Never free a buffer the driver still owns.
                        failed = true;
                        continue;
                    }
                    buffer.Release();
                }
                if (_api.Close(handle) != 0)
                {
                    failed = true;
                    // A faulty driver may still signal this handle. Keep it
                    // valid rather than recycle it for an unrelated object.
                    signal.SafeWaitHandle.SetHandleAsInvalid();
                }
            }
            signal.Dispose();
            if (failed)
            {
                _cleanupFailed = true;
                throw new InvalidOperationException("The microphone could not close. Restart Netra before trying again.");
            }
        }

        void Drain(bool requeue)
        {
            for (int count = 0; count < buffers.Count; count++)
            {
                abort.ThrowIfCancellationRequested();
                if (requeue && stop.IsCancellationRequested) return;
                var buffer = buffers[next];
                var header = Marshal.PtrToStructure<WaveHeader>(buffer.Header);
                if ((header.Flags & 1) == 0) return; // WHDR_DONE
                if (header.BytesRecorded > BufferBytes || header.BytesRecorded % 2 != 0)
                    throw new InvalidOperationException("The microphone returned an invalid audio buffer.");
                if (header.BytesRecorded > 0)
                {
                    byte[] bytes = new byte[header.BytesRecorded];
                    Marshal.Copy(buffer.Data, bytes, 0, bytes.Length);
                    if (!accept(bytes))
                        throw new InvalidOperationException("Voice input could not keep up. Please try again.");
                }
                // A release can race the decision to requeue. Clear consumed
                // bytes while we own this completed buffer so reset/drain
                // cannot deliver that same audio twice.
                header.BytesRecorded = 0;
                Marshal.StructureToPtr(header, buffer.Header, false);
                next = (next + 1) % buffers.Count;
                if (requeue && !stop.IsCancellationRequested && !abort.IsCancellationRequested)
                {
                    // Do not rewrite flags maintained by WinMM. AddBuffer
                    // clears WHDR_DONE and takes ownership again.
                    Check(_api.Add(handle, buffer.Header), "queue");
                }
            }
        }
    }

    private static void Check(uint result, string operation)
    {
        if (result != 0)
            throw new InvalidOperationException($"The microphone could not {operation}. Check the device and Windows microphone permission. (Code {result})");
    }

    private sealed class WaveBuffer
    {
        public nint Data { get; }
        public nint Header { get; }
        public bool Prepared { get; set; }
        public WaveBuffer()
        {
            Data = Marshal.AllocHGlobal(BufferBytes);
            try
            {
                Header = Marshal.AllocHGlobal(Marshal.SizeOf<WaveHeader>());
                Marshal.Copy(new byte[BufferBytes], 0, Data, BufferBytes);
                Marshal.StructureToPtr(new WaveHeader { Data = Data, BufferLength = BufferBytes }, Header, false);
            }
            catch
            {
                if (Header != 0) Marshal.FreeHGlobal(Header);
                Marshal.FreeHGlobal(Data);
                throw;
            }
        }
        public void Release()
        {
            Marshal.Copy(new byte[BufferBytes], 0, Data, BufferBytes);
            Marshal.FreeHGlobal(Header);
            Marshal.FreeHGlobal(Data);
        }
    }
}

[StructLayout(LayoutKind.Sequential)]
internal struct WaveHeader
{
    public nint Data;
    public uint BufferLength;
    public uint BytesRecorded;
    public nuint User;
    public uint Flags;
    public uint Loops;
    public nint Next;
    public nuint Reserved;
}

[StructLayout(LayoutKind.Sequential, Pack = 2)]
internal struct WaveFormat
{
    public ushort FormatTag, Channels;
    public uint SamplesPerSecond, AverageBytesPerSecond;
    public ushort BlockAlign, BitsPerSample, ExtraSize;
    public static WaveFormat Pcm16Mono => new()
    {
        FormatTag = 1, Channels = 1, SamplesPerSecond = 16000,
        AverageBytesPerSecond = 32000, BlockAlign = 2, BitsPerSample = 16,
    };
}

internal interface IWaveInApi
{
    uint Open(out nint handle, ManualResetEvent signal);
    uint Prepare(nint handle, nint header);
    uint Add(nint handle, nint header);
    uint Start(nint handle);
    uint Reset(nint handle);
    uint Unprepare(nint handle, nint header);
    uint Close(nint handle);
}

internal sealed class WaveInApi : IWaveInApi
{
    private static readonly uint HeaderSize = (uint)Marshal.SizeOf<WaveHeader>();
    public uint Open(out nint handle, ManualResetEvent signal)
    {
        if (!OperatingSystem.IsWindows())
            throw new PlatformNotSupportedException("Microphone capture requires Windows.");
        var format = WaveFormat.Pcm16Mono;
        return waveInOpen(out handle, uint.MaxValue, ref format,
            signal.SafeWaitHandle.DangerousGetHandle(), 0, 0x50000); // CALLBACK_EVENT
    }
    public uint Prepare(nint h, nint b) => waveInPrepareHeader(h, b, HeaderSize);
    public uint Add(nint h, nint b) => waveInAddBuffer(h, b, HeaderSize);
    public uint Start(nint h) => waveInStart(h);
    public uint Reset(nint h) => waveInReset(h);
    public uint Unprepare(nint h, nint b) => waveInUnprepareHeader(h, b, HeaderSize);
    public uint Close(nint h) => waveInClose(h);

    [DllImport("winmm.dll", ExactSpelling = true)]
    private static extern uint waveInOpen(out nint h, uint device, ref WaveFormat format, nint callback, nuint instance, uint flags);
    [DllImport("winmm.dll", ExactSpelling = true)] private static extern uint waveInPrepareHeader(nint h, nint b, uint size);
    [DllImport("winmm.dll", ExactSpelling = true)] private static extern uint waveInAddBuffer(nint h, nint b, uint size);
    [DllImport("winmm.dll", ExactSpelling = true)] private static extern uint waveInStart(nint h);
    [DllImport("winmm.dll", ExactSpelling = true)] private static extern uint waveInReset(nint h);
    [DllImport("winmm.dll", ExactSpelling = true)] private static extern uint waveInUnprepareHeader(nint h, nint b, uint size);
    [DllImport("winmm.dll", ExactSpelling = true)] private static extern uint waveInClose(nint h);
}
