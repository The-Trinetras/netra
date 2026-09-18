using System.Collections.ObjectModel;
using System.IO;
using System.Windows.Input;
using Netra.Desktop.Library;

namespace Netra.Desktop.ViewModels;

// Accessible source selection + YouTube discovery/selection (client.md
// "Select material" / "Find a lecture" steps). File picking itself is done
// by the view's code-behind (Microsoft.Win32.OpenFileDialog is a WPF/Win32
// type, not something this view-model layer should depend on directly);
// this view model owns everything after a path is chosen or a dialog is
// cancelled.
public sealed class LibraryViewModel : ViewModelBase
{
    private readonly ISourcePreparationService _sourcePreparationService;
    private readonly IVideoDiscoveryService _videoDiscoveryService;

    private string _statusMessage = "No source selected.";
    private string _videoSearchQuery = string.Empty;
    private VideoDiscoveryResult? _selectedVideoResult;

    public LibraryViewModel(
        ISourcePreparationService sourcePreparationService,
        IVideoDiscoveryService videoDiscoveryService)
    {
        _sourcePreparationService = sourcePreparationService;
        _videoDiscoveryService = videoDiscoveryService;

        SearchVideosCommand = new RelayCommand(_ => FireAndForget(SearchVideosAsync));
        SelectVideoResultCommand = new RelayCommand(parameter => SelectVideoResult(parameter as VideoDiscoveryResult));
    }

    public ObservableCollection<LibraryEntry> Entries { get; } = new();
    public ObservableCollection<VideoDiscoveryResult> VideoResults { get; } = new();

    public string StatusMessage
    {
        get => _statusMessage;
        private set => SetField(ref _statusMessage, value);
    }

    public string VideoSearchQuery
    {
        get => _videoSearchQuery;
        set => SetField(ref _videoSearchQuery, value);
    }

    // The exact selected result, retained per client.md — asking "open the
    // third one" later resolves against THIS, not a re-run search.
    public VideoDiscoveryResult? SelectedVideoResult
    {
        get => _selectedVideoResult;
        private set => SetField(ref _selectedVideoResult, value);
    }

    public ICommand SearchVideosCommand { get; }
    public ICommand SelectVideoResultCommand { get; }

    // Called by the view's code-behind after Microsoft.Win32.OpenFileDialog
    // returns. filePath is null when the dialog was cancelled — a cancelled
    // selection must upload nothing (client.md: "A cancelled selection
    // uploads nothing"), so this returns immediately without creating an
    // entry or calling the preparation service.
    public async Task OnFileSelectedAsync(string? filePath, CancellationToken cancellationToken)
    {
        if (string.IsNullOrEmpty(filePath))
        {
            StatusMessage = "Selection cancelled. Nothing uploaded.";
            return;
        }

        var entry = new LibraryEntry
        {
            EntryId = Guid.NewGuid(),
            FileName = Path.GetFileName(filePath),
            FullPath = filePath,
            Status = LibrarySourceStatus.Selected,
        };
        Entries.Add(entry);
        StatusMessage = $"Selected {entry.FileName}. Processing.";

        entry.Status = LibrarySourceStatus.Processing;
        var resultStatus = await _sourcePreparationService.PrepareAsync(entry, cancellationToken).ConfigureAwait(false);
        entry.Status = resultStatus;

        StatusMessage = resultStatus == LibrarySourceStatus.Ready
            ? $"{entry.FileName} ready to study{(entry.IsFixtureSourced ? " (fixture data)" : string.Empty)}."
            : $"{entry.FileName} could not be prepared.";
    }

    // Public so tests can await the actual search instead of racing the
    // fire-and-forget wrapper SearchVideosCommand uses.
    public async Task SearchVideosAsync()
    {
        if (string.IsNullOrWhiteSpace(VideoSearchQuery))
        {
            return;
        }

        VideoResults.Clear();
        SelectedVideoResult = null;

        var results = await _videoDiscoveryService.SearchAsync(VideoSearchQuery, CancellationToken.None)
            .ConfigureAwait(false);
        foreach (var result in results)
        {
            VideoResults.Add(result);
        }

        StatusMessage = $"Found {VideoResults.Count} result(s) (fixture data).";
    }

    private void SelectVideoResult(VideoDiscoveryResult? result)
    {
        if (result is null)
        {
            return;
        }

        SelectedVideoResult = result;
        StatusMessage = $"Selected result {result.Ordinal}: {result.Title}.";
    }

    private static async void FireAndForget(Func<Task> operation)
    {
        try
        {
            await operation().ConfigureAwait(false);
        }
        catch (Exception)
        {
            // TODO: surface command failures via StatusMessage once an
            // error-presentation policy is defined. Never let an async
            // command crash the app.
        }
    }
}
