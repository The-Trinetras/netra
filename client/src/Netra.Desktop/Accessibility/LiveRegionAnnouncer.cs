using System.Windows.Automation;
using System.Windows.Automation.Peers;
using System.Windows.Controls;

namespace Netra.Desktop.Accessibility;

// Announces dynamic text changes to screen readers via UI Automation live
// regions. This is the primary accessible-feedback mechanism for the app —
// NVDA, JAWS and Narrator all observe AutomationEvents.LiveRegionChanged
// natively, with no provider-specific integration required.
public interface ILiveRegionAnnouncer
{
    void Announce(TextBlock liveRegionHost, string message, AutomationLiveSetting politeness = AutomationLiveSetting.Polite);
}

public sealed class LiveRegionAnnouncer : ILiveRegionAnnouncer
{
    public void Announce(
        TextBlock liveRegionHost, string message, AutomationLiveSetting politeness = AutomationLiveSetting.Polite)
    {
        AutomationProperties.SetLiveSetting(liveRegionHost, politeness);
        liveRegionHost.Text = message;

        var peer = AutomationPeer.FromElement(liveRegionHost)
            ?? UIElementAutomationPeer.CreatePeerForElement(liveRegionHost);
        peer?.RaiseAutomationEvent(AutomationEvents.LiveRegionChanged);
    }
}
