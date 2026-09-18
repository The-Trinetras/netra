using System.Collections.ObjectModel;
using System.IO;
using System.Windows.Input;
using Netra.Desktop.Library;
using Netra.Desktop.Networking;
using Netra.Desktop.Protocol.Dto;
using Netra.Desktop.State;

namespace Netra.Desktop.ViewModels;

// Accessible source selection + YouTube discovery/selection (client.md
// "Select material" / "Find a lecture" steps). File picking itself is done
// by the view's code-behind (Microsoft.Win32.OpenFileDialog is a WPF/Win32
// type, not something this view-model layer should depend on directly);
// this view model owns everything after a path is chosen or a dialog is
// cancelled.
//
// Awaits here deliberately do NOT use ConfigureAwait(false): commands start
// on the UI thread, and the continuations mutate bound ObservableCollections
// and properties, which WPF only allows on the dispatcher thread. (Fixture
// services complete synchronously and hid this; a real network call does not.)
public sealed class LibraryViewModel : ViewModelBase
{
    private readonly ISourcePreparationService _sourcePreparationService;
    private readonly IVideoDiscoveryService _videoDiscoveryService;
    private readonly LibraryServerAccess? _server;

    private string _statusMessage = "No source selected.";
    private string _videoSearchQuery = string.Empty;
    private VideoDiscoveryResult? _selectedVideoResult;
    private CatalogSource? _selectedAvailableSource;
    private CatalogSource? _openedSource;
    private bool _opening;

    public LibraryViewModel(
        ISourcePreparationService sourcePreparationService,
        IVideoDiscoveryService videoDiscoveryService,
        LibraryServerAccess? server = null)
    {
        _sourcePreparationService = sourcePreparationService;
        _videoDiscoveryService = videoDiscoveryService;
        _server = server;

        SearchVideosCommand = new RelayCommand(_ => FireAndForget(SearchVideosAsync));
        SelectVideoResultCommand = new RelayCommand(parameter => SelectVideoResult(parameter as VideoDiscoveryResult));
        RefreshSourcesCommand = new RelayCommand(_ => FireAndForget(() => RefreshSourcesAsync(CancellationToken.None)));
        OpenSourceCommand = new RelayCommand(
            parameter => FireAndForget(() => OpenSourceAsync(parameter as CatalogSource ?? SelectedAvailableSource, CancellationToken.None)));
    }

    public ObservableCollection<LibraryEntry> Entries { get; } = new();
    public ObservableCollection<VideoDiscoveryResult> VideoResults { get; } = new();

    // The account's sources as the server lists them (live mode only).
    public ObservableCollection<CatalogSource> AvailableSources { get; } = new();

    public bool IsServerCatalogAvailable => _server is not null;

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

    // List focus/selection only; nothing is opened until the student
    // explicitly chooses Open.
    public CatalogSource? SelectedAvailableSource
    {
        get => _selectedAvailableSource;
        set => SetField(ref _selectedAvailableSource, value);
    }

    // The source whose version the server confirmed as pinned for study.
    public CatalogSource? OpenedSource
    {
        get => _openedSource;
        private set => SetField(ref _openedSource, value);
    }

    public ICommand SearchVideosCommand { get; }
    public ICommand SelectVideoResultCommand { get; }
    public ICommand RefreshSourcesCommand { get; }
    public ICommand OpenSourceCommand { get; }

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
        var resultStatus = await _sourcePreparationService.PrepareAsync(entry, cancellationToken);
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

        var results = await _videoDiscoveryService.SearchAsync(VideoSearchQuery, CancellationToken.None);
        foreach (var result in results)
        {
            VideoResults.Add(result);
        }

        StatusMessage = $"Found {VideoResults.Count} result(s) (fixture data).";
    }

    public async Task RefreshSourcesAsync(CancellationToken cancellationToken)
    {
        if (_server is null)
        {
            StatusMessage = "Not connected to a Netra server. Server sources are not available.";
            return;
        }

        StatusMessage = "Loading your sources.";
        try
        {
            // Also the retry path when the live session could not be started
            // (server down at launch, socket refused): no restart needed.
            if (_server.Session is { } session)
            {
                await session.EnsureStartedAsync(cancellationToken);
            }

            var sources = await _server.Catalog.ListAsync(cancellationToken);

            // Keep the student's place in the list: clearing the collection
            // makes the bound ListBox push a null selection, so remember the
            // selected source by id first and restore it afterwards.
            var selectedId = SelectedAvailableSource?.SourceId;
            AvailableSources.Clear();
            foreach (var source in sources)
            {
                AvailableSources.Add(source);
            }

            SelectedAvailableSource = AvailableSources.FirstOrDefault(s => s.SourceId == selectedId);
            var ready = sources.Count(s => s.CanOpen);
            StatusMessage = sources.Count == 0
                ? "You have no sources on the server yet."
                : $"{sources.Count} source(s), {ready} ready to study.";
        }
        catch (Exception ex)
        {
            StatusMessage = FailureText.Describe(ex, "load your sources", cancellationToken);
        }
    }

    public async Task OpenSourceAsync(CatalogSource? source, CancellationToken cancellationToken)
    {
        if (_server is null)
        {
            StatusMessage = "Not connected to a Netra server. Server sources are not available.";
            return;
        }

        if (source is null)
        {
            StatusMessage = "Choose a source in the list first.";
            return;
        }

        if (!source.CanOpen)
        {
            StatusMessage = $"{source.Title} is not ready to study yet.";
            return;
        }

        // One open at a time: a second Enter/click while the first pin is in
        // flight would be a second logical action built on the same expected
        // version, and could only come back as a version conflict.
        if (_opening)
        {
            StatusMessage = "Still opening your previous choice. Please wait.";
            return;
        }

        _opening = true;
        StatusMessage = $"Opening {source.Title}.";
        try
        {
            var snapshot = await _server.Catalog.OpenAsync(source, cancellationToken);
            SnapshotReconciler.ApplyUnlessOlder(_server.SessionState, snapshot);
            OpenedSource = source;
            StatusMessage = $"Opened {source.AccessibleLabel}.";
        }
        catch (ApiErrorException ex) when (ex.Error?.Code == ErrorCode.SessionVersionConflict)
        {
            // The session moved on elsewhere. Do not retry the pin on the
            // student's behalf; fetch the authoritative snapshot so the next
            // explicit Open is built against the current version.
            StatusMessage = await ResynchronizeAfterConflictAsync(cancellationToken);
        }
        catch (Exception ex)
        {
            StatusMessage = FailureText.Describe(ex, $"open {source.Title}", cancellationToken);
        }
        finally
        {
            _opening = false;
        }
    }

    private async Task<string> ResynchronizeAfterConflictAsync(CancellationToken cancellationToken)
    {
        if (_server?.Session is not { } session)
        {
            return "Your session changed on the server. Reconnect, then choose Open again.";
        }

        try
        {
            await session.ResynchronizeAsync(cancellationToken);
            return "Your session changed on the server and has been refreshed. Choose Open again.";
        }
        catch (Exception)
        {
            return "Your session changed on the server and could not be refreshed. Choose Refresh, then Open again.";
        }
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
            await operation();
        }
        catch (Exception)
        {
            // TODO: surface command failures via StatusMessage once an
            // error-presentation policy is defined. Never let an async
            // command crash the app.
        }
    }
}
