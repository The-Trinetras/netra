using System.Runtime.ExceptionServices;
using System.Windows;
using System.Windows.Threading;

namespace Netra.Desktop.Tests.Views;

// Runs a test on its own STA thread with a real (off-screen) WPF window, the
// way LibraryViewBindingTests does. Binding and automation-property evidence
// only: what NVDA actually says still needs a Windows/NVDA session.
internal static class WpfTestHost
{
    public static void RunOnSta(Action test)
    {
        Exception? failure = null;
        var thread = new Thread(() =>
        {
            try
            {
                test();
            }
            catch (Exception ex)
            {
                failure = ex;
            }
            finally
            {
                Dispatcher.CurrentDispatcher.InvokeShutdown();
            }
        });
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        thread.Join();
        if (failure is not null)
        {
            ExceptionDispatchInfo.Capture(failure).Throw();
        }
    }

    public static void Pump() =>
        Dispatcher.CurrentDispatcher.Invoke(DispatcherPriority.ApplicationIdle, new Action(() => { }));

    public static Window Show(UIElement content)
    {
        var window = new Window
        {
            Content = content,
            Width = 800,
            Height = 600,
            Left = -10000,
            Top = -10000,
            ShowInTaskbar = false,
            ShowActivated = false,
        };
        window.Show();
        Pump();
        return window;
    }
}
