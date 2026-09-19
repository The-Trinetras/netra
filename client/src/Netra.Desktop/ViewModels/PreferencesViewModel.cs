using System.Windows.Input;
using Netra.Desktop.Accessibility;
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
    private readonly bool _isLive;
    private string _accountStatus = string.Empty;
    private string _activationShortcut = "Activation shortcut: not registered yet.";

    public PreferencesViewModel(
        ClientSessionState sessionState, PlaybackTimeline? timeline = null, IAccountActions? account = null, bool isLive = false)
    {
        _sessionState = sessionState;
        _timeline = timeline;
        _account = account;
        _isLive = isLive;
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

    // Which parts of what the student sees are real server data and which
    // are still labelled fixtures, stated per part, never reassuringly.
    public string DataSourceNotice => _isLive
        ? "Your sources, study session and conversation come from your Netra server. Lecture search results, file preparation, and the figures and tables in the Study tab are still fixture data, not your material."
        : "Offline mode: no Netra server is configured. Everything shown is fixture data, not your material.";

    public string ActivationShortcut
    {
        get => _activationShortcut;
        private set => SetField(ref _activationShortcut, value);
    }

    // Every keyboard shortcut in words (F1 lands here).
    public IReadOnlyList<Shortcut> Shortcuts { get; } =
        ShortcutGuide.Everywhere.Concat(ShortcutGuide.LectureTab.Select(s => s with { Keys = s.Keys + " in the Lecture tab" })).ToList();

    // Reported by the window after it tries the activation candidates.
    public void SetActivationShortcut(string? keys) =>
        ActivationShortcut = keys is null
            ? "No activation shortcut: other programs already use Netra's choices. Use Alt+Tab to reach Netra."
            : $"Activation shortcut: {keys} brings Netra to the front from anywhere. It never opens the microphone.";

    public void Refresh()
    {
        OnPropertyChanged(nameof(ConnectionState));
        OnPropertyChanged(nameof(InteractionMode));
    }
}
