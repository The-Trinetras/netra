using System.Runtime.InteropServices;
using Netra.Desktop.Speech;
using Xunit;

namespace Netra.Desktop.Tests;

public sealed class WinMmPcmCaptureTests
{
    [Fact]
    public void FormatAndNativeLayoutMatchMonoPcm16()
    {
        var f = WaveFormat.Pcm16Mono;
        Assert.Equal((ushort)1, f.FormatTag);
        Assert.Equal((ushort)1, f.Channels);
        Assert.Equal(16000u, f.SamplesPerSecond);
        Assert.Equal(32000u, f.AverageBytesPerSecond);
        Assert.Equal((ushort)2, f.BlockAlign);
        Assert.Equal((ushort)16, f.BitsPerSample);
        Assert.Equal(18, Marshal.SizeOf<WaveFormat>());
        Assert.Equal(IntPtr.Size == 8 ? 48 : 32, Marshal.SizeOf<WaveHeader>());
        Assert.Equal(640, WinMmPcmCapture.BufferBytes);
        Assert.Equal(8, WinMmPcmCapture.BufferCount);
    }

    [Fact]
    public async Task ReleaseDrainsPartialBufferInOrderWithoutRepeatingConsumedAudio()
    {
        using var release = new CancellationTokenSource();
        var driver = new Driver { InitialFrames = 2, FinalPartial = true };
        var frames = new List<byte[]>();
        await new WinMmPcmCapture(driver).CaptureAsync(bytes =>
        {
            frames.Add(bytes.ToArray());
            if (frames.Count == 2) release.Cancel();
            return true;
        }, release.Token).WaitAsync(TimeSpan.FromSeconds(3));
        Assert.Equal(new byte[] { 1, 2, 9 }, frames.Select(b => b[0]).ToArray());
        Assert.All(frames, bytes => Assert.Equal(2, bytes.Length));
        Assert.Equal(WinMmPcmCapture.BufferCount, driver.HeadersAllocated);
        Assert.Equal(WinMmPcmCapture.BufferCount, driver.Unprepared);
        Assert.Equal("close", driver.Calls.Last());
        Assert.True(driver.Calls.IndexOf("reset") < driver.Calls.IndexOf("unprepare"));
    }

    [Fact]
    public async Task AbortDropsQueuedAndPartialAudio()
    {
        using var abort = new CancellationTokenSource();
        var driver = new Driver { InitialFrames = 2, FinalPartial = true };
        int delivered = 0;
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() =>
            new WinMmPcmCapture(driver).CaptureAsync(_ =>
            {
                delivered++;
                abort.Cancel();
                return true;
            }, CancellationToken.None, abort.Token).WaitAsync(TimeSpan.FromSeconds(3)));
        Assert.Equal(1, delivered);
        Assert.Equal(WinMmPcmCapture.BufferCount, driver.Unprepared);
        Assert.Equal("close", driver.Calls.Last());
    }

    [Fact]
    public async Task AlreadyReleasedOrAbortedNeverOpensDevice()
    {
        var driver = new Driver();
        var capture = new WinMmPcmCapture(driver);
        await capture.CaptureAsync(_ => true, new CancellationToken(true));
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() =>
            capture.CaptureAsync(_ => true, default, new CancellationToken(true)));
        Assert.Empty(driver.Calls);
    }

    [Theory]
    [InlineData("open", 0)]
    [InlineData("prepare", 1)]
    [InlineData("queue", 2)]
    [InlineData("start", WinMmPcmCapture.BufferCount)]
    public async Task NativeFailureClosesDeviceAndReleasesPreparedBuffers(string fail, int prepared)
    {
        var driver = new Driver { Fail = fail };
        var error = await Assert.ThrowsAsync<InvalidOperationException>(() =>
            new WinMmPcmCapture(driver).CaptureAsync(_ => true, default));
        Assert.Contains("microphone", error.Message);
        Assert.Equal(prepared, driver.Unprepared);
        Assert.Equal(fail == "open" ? "open" : "close", driver.Calls.Last());
    }

    [Fact]
    public async Task QueueOverflowFailsAndClosesInsteadOfDroppingAudio()
    {
        var driver = new Driver { InitialFrames = 1 };
        var error = await Assert.ThrowsAsync<InvalidOperationException>(() =>
            new WinMmPcmCapture(driver).CaptureAsync(_ => false, default)
                .WaitAsync(TimeSpan.FromSeconds(3)));
        Assert.Contains("could not keep up", error.Message);
        Assert.Equal(WinMmPcmCapture.BufferCount, driver.Unprepared);
        Assert.Equal("close", driver.Calls.Last());
    }

    [Theory]
    [InlineData(1u)]
    [InlineData(642u)]
    public async Task InvalidByteCountsNeverReachConsumer(uint count)
    {
        var driver = new Driver { InitialFrames = 1, InvalidCount = count };
        int delivered = 0;
        await Assert.ThrowsAsync<InvalidOperationException>(() => new WinMmPcmCapture(driver)
            .CaptureAsync(_ => { delivered++; return true; }, default)
            .WaitAsync(TimeSpan.FromSeconds(3)));
        Assert.Equal(0, delivered);
        Assert.Equal(WinMmPcmCapture.BufferCount, driver.Unprepared);
    }

    [Fact]
    public async Task SecondCaptureIsRefusedAndStoppedCaptureCanRestart()
    {
        using var release = new CancellationTokenSource();
        var driver = new Driver();
        var capture = new WinMmPcmCapture(driver);
        var first = capture.CaptureAsync(_ => true, release.Token);
        await driver.Started.Task.WaitAsync(TimeSpan.FromSeconds(3));
        try
        {
            // CaptureAsync refuses synchronously, before any Task starts.
            Assert.Throws<InvalidOperationException>(() => { _ = capture.CaptureAsync(_ => true, default); });
        }
        finally { release.Cancel(); await first.WaitAsync(TimeSpan.FromSeconds(3)); }
        await capture.CaptureAsync(_ => true, new CancellationToken(true));
    }

    private sealed class Driver : IWaveInApi
    {
        public List<string> Calls { get; } = new();
        private readonly List<nint> _headers = new();
        private ManualResetEvent? _signal;
        public string? Fail { get; init; }
        public int InitialFrames { get; init; }
        public bool FinalPartial { get; init; }
        public uint? InvalidCount { get; init; }
        public int Unprepared { get; private set; }
        public int HeadersAllocated => _headers.Count;
        public TaskCompletionSource Started { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);

        public uint Open(out nint handle, ManualResetEvent signal)
        {
            Calls.Add("open"); _signal = signal; handle = 1;
            return Fail == "open" ? 5u : 0;
        }
        public uint Prepare(nint h, nint b)
        {
            Calls.Add("prepare"); _headers.Add(b);
            return Fail == "prepare" && _headers.Count == 2 ? 5u : 0;
        }
        public uint Add(nint h, nint b)
        {
            Calls.Add("queue");
            if (Fail == "queue" && _headers.Count == 2) return 5;
            var header = Marshal.PtrToStructure<WaveHeader>(b);
            header.Flags = 0x10; // WHDR_INQUEUE, clear WHDR_DONE like WinMM
            Marshal.StructureToPtr(header, b, false);
            return 0;
        }
        public uint Start(nint h)
        {
            Calls.Add("start");
            if (Fail == "start") return 5;
            for (int i = 0; i < InitialFrames; i++) Complete(i, (byte)(i + 1), InvalidCount ?? 2);
            Started.TrySetResult(); _signal!.Set(); return 0;
        }
        public uint Reset(nint h)
        {
            Calls.Add("reset");
            for (int i = 0; i < _headers.Count; i++)
            {
                var header = Marshal.PtrToStructure<WaveHeader>(_headers[i]);
                if ((header.Flags & 0x10) == 0) continue;
                if (FinalPartial && i == 2) Complete(i, 9, 2);
                else Complete(i, 0, 0);
            }
            return 0;
        }
        private void Complete(int index, byte value, uint count)
        {
            var ptr = _headers[index];
            var header = Marshal.PtrToStructure<WaveHeader>(ptr);
            Marshal.Copy(new[] { value, (byte)0 }, 0, header.Data, 2);
            header.BytesRecorded = count; header.Flags = 1;
            Marshal.StructureToPtr(header, ptr, false);
        }
        public uint Unprepare(nint h, nint b) { Calls.Add("unprepare"); Unprepared++; return 0; }
        public uint Close(nint h) { Calls.Add("close"); return 0; }
    }
}
