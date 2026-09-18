using Netra.Desktop.Diagnostics;
using Netra.Desktop.Protocol.Dto;
using Netra.Desktop.State;

namespace Netra.Desktop.ViewModels;

// Preferences/status view: exposes real progress, failure and reduced
// modes without misleading reassurance (client.md / current-scope.md).
// Nothing here is a setting yet — no persisted preference exists in the
// scaffold to expose — this is the status half of "Preferences/status".
public sealed class PreferencesViewModel : ViewModelBase
{
    private readonly ClientSessionState _sessionState;
    private readonly PlaybackTimeline? _timeline;
    private string _measurementStatus = string.Empty;

    public PreferencesViewModel(ClientSessionState sessionState, PlaybackTimeline? timeline = null)
    {
        _sessionState = sessionState;
        _timeline = timeline;
    }

    // Local playback measurements (Diagnostics/PlaybackTimeline). Saved only
    // when the student or tester chooses to; nothing is uploaded.
    public bool HasMeasurements => _timeline is not null;

    public string MeasurementStatus
    {
        get => _measurementStatus;
        private set => SetField(ref _measurementStatus, value);
    }

    public string? ExportMeasurements(string environmentNote)
    {
        if (_timeline is null)
        {
            return null;
        }

        var json = _timeline.ExportJson(environmentNote);
        MeasurementStatus = $"Saved {_timeline.Snapshot().Count} playback measurements.";
        return json;
    }

    public void ClearMeasurements()
    {
        _timeline?.Clear();
        MeasurementStatus = "Playback measurements cleared.";
    }

    public void ReportMeasurementFailure(string message) => MeasurementStatus = message;

    public ConnectionState ConnectionState => _sessionState.ConnectionState;

    public SessionInteractionMode InteractionMode => _sessionState.InteractionMode;

    // Always true today: every Library/Study data source in this pass is an
    // explicit fixture (docs/team/handoffs/M5.md Gaps 1 and 3). This flag
    // exists so the UI never has to be edited to stop lying once a real
    // source is wired — the moment a real implementation reports
    // IsFixtureSourced = false, this should follow it rather than being
    // hardcoded true.
    public bool IsUsingFixtureData => true;

    public void Refresh()
    {
        OnPropertyChanged(nameof(ConnectionState));
        OnPropertyChanged(nameof(InteractionMode));
    }
}
