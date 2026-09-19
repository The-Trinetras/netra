namespace Netra.Desktop.Threading;

// Marshals a callback onto the UI thread. ConnectionManager.MessageReceived
// and ISpeechInputService.TranscriptReceived fire from the WebSocket receive
// loop, and voice status from the capture's own tasks — none is the UI
// thread, and WPF collection views (ObservableCollection bound to a
// ListBox/ItemsControl) are not safe to mutate off it. Every handler that
// touches bound state goes through this instead of calling WPF types directly,
// so tests can substitute a synchronous double and run without a live
// Dispatcher/Application.
public interface IUiDispatcher
{
    void Invoke(Action action);
}

// Test double: runs the action immediately on the calling thread. Suitable
// only for single-threaded unit tests that want deterministic ordering
// without spinning up a WPF Dispatcher.
public sealed class SynchronousUiDispatcher : IUiDispatcher
{
    public void Invoke(Action action) => action();
}
