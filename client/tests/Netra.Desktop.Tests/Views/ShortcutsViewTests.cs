using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Input;
using Netra.Desktop.Accessibility;
using Netra.Desktop.Protocol.Dto;
using Netra.Desktop.State;
using Netra.Desktop.ViewModels;
using Netra.Desktop.Views;
using Xunit;

namespace Netra.Desktop.Tests.Views;

// The real gestures and views (WPF): the spoken shortcut list matches the
// gestures the window actually binds, every contracted command has a named
// button with its shortcut, and F1 lands on the first shortcut.
public sealed class ShortcutsViewTests
{
    [Fact]
    public void TheSpokenListMatchesTheBoundGestures()
    {
        var bound = new Dictionary<string, RoutedUICommand>
        {
            ["Escape"] = KeyboardCommands.Stop,
            ["Control+P"] = KeyboardCommands.Pause,
            ["Control+Shift+P"] = KeyboardCommands.Continue,
            ["Control+Right arrow"] = KeyboardCommands.Next,
            ["Control+Left arrow"] = KeyboardCommands.Previous,
            ["Control+R"] = KeyboardCommands.Repeat,
            ["Control+L"] = KeyboardCommands.WhereAmI,
            ["Control+B"] = KeyboardCommands.BackToReading,
            ["Control+U"] = KeyboardCommands.UndoJump,
            ["Control+Q"] = KeyboardCommands.ReturnToQuestion,
            ["F1"] = KeyboardCommands.ShowShortcuts,
        };

        foreach (var (keys, command) in bound)
        {
            Assert.Contains(ShortcutGuide.Everywhere, s => s.Keys == keys);
            var gesture = Assert.IsType<KeyGesture>(Assert.Single(command.InputGestures.Cast<InputGesture>()));
            Assert.Equal(keys, Spoken(gesture));
        }
    }

    [Fact]
    public void EveryContractedCommandHasANamedButtonWithItsShortcut()
    {
        WpfTestHost.RunOnSta(() =>
        {
            var viewModel = ConversationViewModelFactory();
            var view = new ConversationView(viewModel, new LiveRegionAnnouncer());
            var window = WpfTestHost.Show(view);
            try
            {
                var panel = (WrapPanel)view.FindName("ReadingControls");
                var buttons = panel.Children.OfType<Button>().ToList();
                var commands = buttons.Select(b => (NavigationCommandType)b.CommandParameter).ToHashSet();
                foreach (var command in Enum.GetValues<NavigationCommandType>().Where(c => c != NavigationCommandType.Stop))
                {
                    Assert.Contains(command, commands);
                }

                Assert.All(buttons, b =>
                {
                    Assert.False(string.IsNullOrEmpty(AutomationProperties.GetName(b)));
                    Assert.False(string.IsNullOrEmpty(AutomationProperties.GetAcceleratorKey(b)));
                });
            }
            finally
            {
                window.Close();
            }
        });
    }

    [Fact]
    public void F1LandsOnTheFirstShortcut()
    {
        WpfTestHost.RunOnSta(() =>
        {
            var view = new PreferencesView { DataContext = new PreferencesViewModel(new ClientSessionState()) };
            var window = WpfTestHost.Show(view);
            try
            {
                view.FocusShortcuts();
                WpfTestHost.Pump();

                var list = (ListBox)view.FindName("ShortcutList");
                Assert.Equal(0, list.SelectedIndex);
                Assert.True(list.IsKeyboardFocusWithin);
            }
            finally
            {
                window.Close();
            }
        });
    }

    private static string Spoken(KeyGesture gesture)
    {
        var parts = new List<string>();
        if (gesture.Modifiers.HasFlag(ModifierKeys.Control))
        {
            parts.Add("Control");
        }

        if (gesture.Modifiers.HasFlag(ModifierKeys.Alt))
        {
            parts.Add("Alt");
        }

        if (gesture.Modifiers.HasFlag(ModifierKeys.Shift))
        {
            parts.Add("Shift");
        }

        parts.Add(gesture.Key switch
        {
            Key.Right => "Right arrow",
            Key.Left => "Left arrow",
            _ => gesture.Key.ToString(),
        });
        return string.Join("+", parts);
    }

    private static ConversationViewModel ConversationViewModelFactory()
    {
        var state = new ClientSessionState();
        state.Initialize(Guid.NewGuid(), 1);
        var connection = new Networking.ConnectionManager(new CapturingSocket(), state);
        var player = new ScriptedPlayer();
        var interruption = new Audio.InterruptionController(player, connection);
        return new ConversationViewModel(
            state, connection, player, interruption, new Audio.BinaryAudioFrameProcessor(interruption),
            new Speech.MicrophoneCapture(connection, new FakePcmSource()), new Threading.SynchronousUiDispatcher());
    }
}
