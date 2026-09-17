using System.Windows.Threading;

namespace Netra.Desktop.Threading;

// Marshals a callback onto the UI thread. ConnectionManager.MessageReceived
// fires from the WebSocket receive loop and ISpeechInputService.TranscriptReceived
// will fire from a future recognition-provider thread — neither is the UI
// thread, and WPF collection views (ObservableCollection bound to a
// ListBox/ItemsControl) are not safe to mutate off it. Every handler that
// touches bound state goes through this instead of calling WPF types directly,
// so tests can substitute a synchronous double and run without a live
// Dispatcher/Application.
public interface IUiDispatcher
{
    void Invoke(Action action);
}

public sealed class WpfUiDispatcher : IUiDispatcher
{
    private readonly Dispatcher _dispatcher;

    public WpfUiDispatcher(Dispatcher dispatcher)
    {
        _dispatcher = dispatcher;
    }

    public void Invoke(Action action)
    {
        if (_dispatcher.CheckAccess())
        {
            action();
            return;
        }

        _dispatcher.Invoke(action);
    }
}

// Test double: runs the action immediately on the calling thread. Suitable
// only for single-threaded unit tests that want deterministic ordering
// without spinning up a WPF Dispatcher.
public sealed class SynchronousUiDispatcher : IUiDispatcher
{
    public void Invoke(Action action) => action();
}
