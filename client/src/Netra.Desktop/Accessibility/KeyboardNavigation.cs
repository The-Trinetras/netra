using System.Windows.Input;

namespace Netra.Desktop.Accessibility;

// Global keyboard gestures for the ten deterministic navigation commands in
// CLAUDE.md ("Deterministic commands") and for moving around the window.
// Keyboard operation is mandatory for the whole app; these gestures work
// regardless of which control has focus, via Window-level CommandBindings
// (see Views/MainWindow.xaml). ShortcutGuide lists the same gestures in
// words for the Preferences view, and a test keeps the two in step.
//
// Chosen to stay clear of NVDA (NVDA-key combinations, and Ctrl+Alt+N, its
// desktop shortcut), JAWS and Narrator (Insert, Caps Lock and Windows-key
// combinations) and Windows itself. A live conflict test on Windows with
// NVDA is still open (M5-SHORTCUT).
public static class KeyboardCommands
{
    public static readonly RoutedUICommand Stop = Command("Stop", nameof(Stop), new KeyGesture(Key.Escape));
    public static readonly RoutedUICommand Pause = Command("Pause", nameof(Pause), new KeyGesture(Key.P, ModifierKeys.Control));
    public static readonly RoutedUICommand Continue = Command("Continue", nameof(Continue), new KeyGesture(Key.P, ModifierKeys.Control | ModifierKeys.Shift));
    public static readonly RoutedUICommand Repeat = Command("Repeat", nameof(Repeat), new KeyGesture(Key.R, ModifierKeys.Control));
    public static readonly RoutedUICommand Next = Command("Next", nameof(Next), new KeyGesture(Key.Right, ModifierKeys.Control));
    public static readonly RoutedUICommand Previous = Command("Previous", nameof(Previous), new KeyGesture(Key.Left, ModifierKeys.Control));
    public static readonly RoutedUICommand WhereAmI = Command("Where am I", nameof(WhereAmI), new KeyGesture(Key.L, ModifierKeys.Control));
    public static readonly RoutedUICommand BackToReading = Command("Back to reading", nameof(BackToReading), new KeyGesture(Key.B, ModifierKeys.Control));
    public static readonly RoutedUICommand UndoJump = Command("Undo jump", nameof(UndoJump), new KeyGesture(Key.U, ModifierKeys.Control));
    public static readonly RoutedUICommand ReturnToQuestion = Command("Return to question", nameof(ReturnToQuestion), new KeyGesture(Key.Q, ModifierKeys.Control));

    // Ctrl+1 to Ctrl+5 open the tabs in order; F1 opens the shortcut list.
    public static readonly RoutedUICommand GoToTab = new("Go to tab", nameof(GoToTab), typeof(KeyboardCommands));
    public static readonly RoutedUICommand ShowShortcuts = Command("Keyboard shortcuts", nameof(ShowShortcuts), new KeyGesture(Key.F1));

    private static RoutedUICommand Command(string text, string name, KeyGesture gesture) =>
        new(text, name, typeof(KeyboardCommands), new InputGestureCollection { gesture });
}
