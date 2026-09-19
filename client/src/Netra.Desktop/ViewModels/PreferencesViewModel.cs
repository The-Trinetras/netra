using System.Windows.Input;
using Netra.Desktop.Diagnostics;
using Netra.Desktop.Protocol.Dto;
using Netra.Desktop.State;

namespace Netra.Desktop.ViewModels;

// Signing in and out of this computer (decision D-CRED), provided by the
// composition root: it owns the sign-in dialog and the live session.
public interface IAccountActions
{
    bool IsSignedIn { get; }
    Task SignInAsync();
    Task SignOutAsync();
}

// Preferences/status view: exposes real progress, failure and reduced
// modes without misleading reassurance (client.md / current-scope.md).
// Nothing here is a setting yet — no persisted preference exists in the
// scaffold to expose — this is the status half of "Preferences/status".
public sealed class PreferencesViewModel : ViewModelBase
{
    private readonly ClientSessionState _sessionState;
    private readonly PlaybackTimeline? _timeline;
    private string _measurementStatus = string.Empty;

    private readonly IAccountActions? _account;
    private string _accountStatus = string.Empty;

    public PreferencesViewModel(ClientSessionState sessionState, PlaybackTimeline? timeline = null, IAccountActions? account = null)
    {
        _sessionState = sessionState;
        _timeline = timeline;
        _account = account;
        SignInCommand = new RelayCommand(_ => _ = RunAccountActionAsync(a => a.SignInAsync()), _ => _account is not null);
        RefreshAccountStatus();
    }

    // Live mode only: fixture mode has no server to sign in to.
    public bool HasAccount => _account is not null;

    public string AccountStatus
    {
        get => _accountStatus;
        private set => SetField(ref _accountStatus, value);
    }

    public ICommand SignInCommand { get; }

    // The view confirms first: signing out cannot be undone without a new code.
    public Task SignOutAsync() => RunAccountActionAsync(a => a.SignOutAsync());

    public void RefreshAccountStatus() =>
        AccountStatus = _account is null
            ? "Offline mode: no Netra server is configured, so there is nothing to sign in to."
            : _account.IsSignedIn
                ? "This computer is signed in to Netra."
                : "This computer is not signed in to Netra.";

    private async Task RunAccountActionAsync(Func<IAccountActions, Task> action)
    {
        if (_account is null)
        {
            return;
        }

        try
        {
            await action(_account);
        }
        finally
        {
            RefreshAccountStatus();
        }
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
