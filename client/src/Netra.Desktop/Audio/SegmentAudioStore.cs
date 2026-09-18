using System.IO;

namespace Netra.Desktop.Audio;

// Hands a complete segment to MediaPlayer, which only opens a URI. Each
// segment becomes one transient file under a per-process directory, named
// by a random id (never by text or ids) and deleted after it stops or ends.
// This is a playback buffer, not a cache: nothing is reused across segments
// or runs, and the directory is removed on shutdown.
public interface ISegmentAudioStore : IDisposable
{
    // Returns null when the media type has no known container extension;
    // the caller keeps accessible text and reports the audio as unplayable.
    Uri? Stage(CompleteSegmentAudio segment);

    void Release(Uri staged);
}

public sealed class TempFileSegmentAudioStore : ISegmentAudioStore
{
    // Media types WPF MediaPlayer (Media Foundation) can open from a file.
    // audio_frame_header.schema.json does not enumerate a provider list, so
    // anything else is refused explicitly rather than guessed.
    private static readonly Dictionary<string, string> ExtensionByMediaType = new(StringComparer.OrdinalIgnoreCase)
    {
        ["audio/mpeg"] = ".mp3",
        ["audio/mp3"] = ".mp3",
        ["audio/wav"] = ".wav",
        ["audio/wave"] = ".wav",
        ["audio/x-wav"] = ".wav",
    };

    private readonly string _directory;
    private readonly object _lock = new();
    private readonly HashSet<string> _pendingDeletes = new(StringComparer.OrdinalIgnoreCase);

    public TempFileSegmentAudioStore()
        : this(Path.Combine(Path.GetTempPath(), "Netra", "playback", Environment.ProcessId.ToString()))
    {
    }

    public TempFileSegmentAudioStore(string directory)
    {
        _directory = directory;
    }

    public static bool IsSupported(string mediaType) => ExtensionByMediaType.ContainsKey(mediaType);

    public Uri? Stage(CompleteSegmentAudio segment)
    {
        if (!ExtensionByMediaType.TryGetValue(segment.MediaType, out var extension))
        {
            return null;
        }

        Directory.CreateDirectory(_directory);
        var path = Path.Combine(_directory, Guid.NewGuid().ToString("N") + extension);
        File.WriteAllBytes(path, segment.Audio);
        RetryPendingDeletes();
        return new Uri(path, UriKind.Absolute);
    }

    // MediaPlayer can hold the file until it opens the next source, so a
    // failed delete is retried on the next Stage/Release and at Dispose.
    public void Release(Uri staged)
    {
        lock (_lock)
        {
            _pendingDeletes.Add(staged.LocalPath);
        }

        RetryPendingDeletes();
    }

    private void RetryPendingDeletes()
    {
        lock (_lock)
        {
            _pendingDeletes.RemoveWhere(TryDelete);
        }
    }

    private static bool TryDelete(string path)
    {
        try
        {
            File.Delete(path);
            return true;
        }
        catch (IOException)
        {
            return false;
        }
        catch (UnauthorizedAccessException)
        {
            return false;
        }
    }

    public void Dispose()
    {
        RetryPendingDeletes();
        try
        {
            if (Directory.Exists(_directory))
            {
                Directory.Delete(_directory, recursive: true);
            }
        }
        catch (IOException)
        {
            // A file still held by the player at shutdown; the OS temp
            // cleanup removes it. Never block exit on it.
        }
        catch (UnauthorizedAccessException)
        {
        }
    }
}
