namespace Netra.Desktop.Accessibility;

public interface IScreenReaderService
{
    void Speak(string message, bool interrupt = false);
}

// Placeholder for a direct screen-reader integration. WPF's standard
// accessible controls plus UI Automation live regions (LiveRegionAnnouncer)
// already cover the core reading flow without depending on any specific
// screen reader, per CLAUDE.md ("never require sighted interaction for a
// core reading flow").
//
// TODO: a direct NVDA integration (e.g. the NVDA Controller Client DLL,
// nvdaControllerClient64.dll via P/Invoke) is not implemented here. That
// would add a native binary dependency, which is out of scope for a
// boilerplate scaffold and needs an explicit decision before it's
// introduced (CLAUDE.md "Claude Code behavior": no unrequested
// installs/dependencies). Prefer ILiveRegionAnnouncer until that decision
// is made.
public sealed class ScreenReaderService : IScreenReaderService
{
    public void Speak(string message, bool interrupt = false)
    {
        // TODO: wire to the chosen screen-reader integration once approved.
    }
}
