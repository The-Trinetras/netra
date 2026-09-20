namespace System.Windows.Input;

// Test-only stand-in for WPF's CommandManager, so the linked RelayCommand
// compiles without PresentationCore. ICommand itself is cross-platform
// (System.ObjectModel). Nothing raises RequerySuggested here; tests read
// CanExecute directly.
internal static class CommandManager
{
    public static event EventHandler? RequerySuggested
    {
        add { }
        remove { }
    }
}
