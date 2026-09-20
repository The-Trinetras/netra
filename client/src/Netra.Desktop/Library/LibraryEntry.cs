using System.ComponentModel;
using System.Runtime.CompilerServices;

namespace Netra.Desktop.Library;

// Status of a locally-selected source as it moves toward being study-ready.
// "Processing"/"Ready"/"Failed" must only ever be driven by an actual
// preparation result (fixture or real) — never asserted at selection time,
// per client.md: "announce meaningful processing changes" and current-scope.md:
// "announce readiness only for capabilities actually available."
public enum LibrarySourceStatus
{
    Selected,
    Processing,
    Ready,
    Failed,
}

// Notifies: the library list binds Status OneWay and preparation changes it
// after the row is already on screen. Without INotifyPropertyChanged the row
// keeps whatever it rendered first, so a finished source went on announcing
// "Processing" — the stale announcement client.md rules out ("announce
// meaningful processing changes").
public sealed class LibraryEntry : INotifyPropertyChanged
{
    private LibrarySourceStatus _status = LibrarySourceStatus.Selected;
    private bool _isFixtureSourced = true;

    public required Guid EntryId { get; init; }
    public required string FileName { get; init; }
    public required string FullPath { get; init; }

    public LibrarySourceStatus Status
    {
        get => _status;
        set => Set(ref _status, value);
    }

    // True whenever Status was reached through a fixture/local double rather
    // than an authorized server response. Never silently promoted to false;
    // client.md: "fixture mode must be explicit and never pretend a server
    // mutation succeeded."
    public bool IsFixtureSourced
    {
        get => _isFixtureSourced;
        set => Set(ref _isFixtureSourced, value);
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    private void Set<T>(ref T field, T value, [CallerMemberName] string? propertyName = null)
    {
        if (EqualityComparer<T>.Default.Equals(field, value))
        {
            return;
        }

        field = value;
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
    }
}
