using System.Windows.Input;

namespace Netra.Desktop.Accessibility;

// Global keyboard gestures for the deterministic navigation commands listed
// in CLAUDE.md ("Deterministic commands"). Keyboard operation is mandatory
// for the whole app; these gestures work regardless of which control has
// focus, via Window-level CommandBindings (see Views/MainWindow.xaml).
public static class KeyboardCommands
{
    public static readonly RoutedUICommand Stop = new(
        "Stop", nameof(Stop), typeof(KeyboardCommands),
        new InputGestureCollection { new KeyGesture(Key.Escape) });

    public static readonly RoutedUICommand Repeat = new(
        "Repeat", nameof(Repeat), typeof(KeyboardCommands),
        new InputGestureCollection { new KeyGesture(Key.R, ModifierKeys.Control) });

    public static readonly RoutedUICommand Next = new(
        "Next", nameof(Next), typeof(KeyboardCommands),
        new InputGestureCollection { new KeyGesture(Key.Right, ModifierKeys.Control) });

    public static readonly RoutedUICommand Previous = new(
        "Previous", nameof(Previous), typeof(KeyboardCommands),
        new InputGestureCollection { new KeyGesture(Key.Left, ModifierKeys.Control) });
}
