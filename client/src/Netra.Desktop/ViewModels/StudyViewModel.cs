using System.Collections.ObjectModel;
using System.Windows.Input;
using Netra.Desktop.Study;

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

    public StudyViewModel()
    {
        foreach (var detectedObject in OhmsLawFixture.DetectedObjects)
        {
            DetectedObjects.Add(detectedObject);
        }

        ExploreCommand = new RelayCommand(parameter => Explore(parameter as DetectedObject));
        ReturnToReadingCommand = new RelayCommand(_ => ReturnToReading(), _ => IsExploring);
    }

    public ObservableCollection<DetectedObject> DetectedObjects { get; } = new();

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
