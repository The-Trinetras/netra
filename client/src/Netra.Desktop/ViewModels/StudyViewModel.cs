using System.Collections.ObjectModel;
using System.Windows.Input;
using Netra.Desktop.Study;
using Netra.Desktop.Library;
using Netra.Desktop.Protocol.Dto;

namespace Netra.Desktop.ViewModels;

// Passage/figure/diagram/table/basic-equation exploration (M5.md step 5).
// Navigation between detected objects and back to reading is entirely
// deterministic — no LLM call decides "which object" or "go back"; the
// student's selection index is the only input. Real content is blocked on
// docs/team/handoffs/M5.md Gap 3 (no client-visible evidence contract), so
// DetectedObjects is fixture data from Study/DetectedObject.cs; the
// navigation/return INTERACTION built here is real and independent of that
// gap.
public sealed class StudyViewModel : ViewModelBase
{
    private string _readingPositionSummary = "b12/s3 (fixture)";
    private DetectedObject? _focusedObject;
    private bool _isExploring;
    private string _statusMessage = "Reading. No object selected.";
    private string _sourceTitle = "Study";
    private string? _sourceVersionId;
    private string? _generationId;
    private readonly HashSet<string> _sentences = new();

    public StudyViewModel(bool isLive = false)
    {
        IsLive = isLive;
        foreach (var detectedObject in isLive ? Array.Empty<DetectedObject>() : OhmsLawFixture.DetectedObjects)
        {
            DetectedObjects.Add(detectedObject);
        }

        ExploreCommand = new RelayCommand(parameter => Explore(parameter as DetectedObject));
        ReturnToReadingCommand = new RelayCommand(_ => ReturnToReading(), _ => IsExploring);
        if (isLive)
        {
            _readingPositionSummary = "No source open.";
            _statusMessage = "Open a source from Library to start reading.";
        }
    }

    public ObservableCollection<DetectedObject> DetectedObjects { get; } = new();
    public ObservableCollection<string> ReadingLines { get; } = new();
    public bool IsLive { get; }
    public string SourceTitle { get => _sourceTitle; private set => SetField(ref _sourceTitle, value); }

    public void OpenSource(CatalogSource source)
    {
        ClearReading();
        _sourceVersionId = source.ActiveSourceVersionId;
        SourceTitle = source.Title;
        StatusMessage = $"Opened {source.Title}. Loading the current passage.";
    }

    public void ApplySnapshot(SessionSnapshotPayload snapshot)
    {
        if (!IsLive) return;
        if (_sourceVersionId != snapshot.ActiveSourceVersionId)
        {
            ClearReading();
            _sourceVersionId = snapshot.ActiveSourceVersionId;
            SourceTitle = _sourceVersionId is null ? "Study" : "Opened source";
        }
        ReadingPositionSummary = snapshot.CurrentBlockId is null
            ? "No source open."
            : $"Block {snapshot.CurrentBlockId}, sentence {snapshot.CurrentSentenceId}.";
    }

    public void ShowReading(ResponseSegmentPayload segment)
    {
        if (!IsLive || _sourceVersionId is null) return;
        if (_generationId != segment.GenerationId)
        {
            ClearReading();
            _generationId = segment.GenerationId;
        }
        if (_sentences.Add(segment.SentenceId)) ReadingLines.Add(segment.Text);
        StatusMessage = "Source passage. Use the reading controls to navigate, or Conversation to ask a question.";
    }

    public void ClearForSignOut()
    {
        ClearReading();
        _sourceVersionId = null;
        SourceTitle = "Study";
        ReadingPositionSummary = "No source open.";
        StatusMessage = "Open a source from Library to start reading.";
    }

    private void ClearReading()
    {
        ReadingLines.Clear();
        _sentences.Clear();
        _generationId = null;
    }

    public string ReadingPositionSummary
    {
        get => _readingPositionSummary;
        private set => SetField(ref _readingPositionSummary, value);
    }

    // The object currently being explored, or null while reading normally.
    // "Exact return to reading" means this goes back to null and
    // StatusMessage reports the same reading position it had before —
    // never a re-derived or approximate one.
    public DetectedObject? FocusedObject
    {
        get => _focusedObject;
        private set => SetField(ref _focusedObject, value);
    }

    public bool IsExploring
    {
        get => _isExploring;
        private set => SetField(ref _isExploring, value);
    }

    public string StatusMessage
    {
        get => _statusMessage;
        private set => SetField(ref _statusMessage, value);
    }

    public ICommand ExploreCommand { get; }
    public ICommand ReturnToReadingCommand { get; }

    private void Explore(DetectedObject? detectedObject)
    {
        if (detectedObject is null)
        {
            return;
        }

        FocusedObject = detectedObject;
        IsExploring = true;
        StatusMessage = $"{detectedObject.Label}: {detectedObject.AccessibleSummary}";
    }

    private void ReturnToReading()
    {
        FocusedObject = null;
        IsExploring = false;
        // Exact return: the same position string reported before exploring,
        // never advanced or approximated by the act of exploring an object.
        StatusMessage = $"Back to reading at {ReadingPositionSummary}.";
    }
}
