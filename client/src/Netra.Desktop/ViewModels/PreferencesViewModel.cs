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

    public PreferencesViewModel(ClientSessionState sessionState)
    {
        _sessionState = sessionState;
    }

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
