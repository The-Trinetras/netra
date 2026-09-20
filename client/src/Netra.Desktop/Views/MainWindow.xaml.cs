using System.Windows;
using System.Windows.Input;
using Netra.Desktop.Accessibility;
using Netra.Desktop.ViewModels;

namespace Netra.Desktop.Views;

public partial class MainWindow : Window
{
    // Arbitrary but unique per-process id RegisterHotKey uses to identify
    // this hotkey in WM_HOTKEY; it is not a system-wide registry value.
    private const int ActivationHotKeyId = 0xA11CE;

    // Activation shortcut candidates, tried in order; the first that Windows
    // grants is used and shown in Preferences and status. Never Ctrl+Alt+N:
    // that is NVDA's desktop shortcut to start or restart NVDA, which a
    // blind student may need when the screen reader stops responding. A live
    // conflict test with NVDA and JAWS on Windows is still open
    // (M5-SHORTCUT; client.md / AgentSpec §8).
    private static readonly (ModifierKeys Modifiers, Key Key, string Spoken)[] ActivationCandidates =
    {
        (ModifierKeys.Control | ModifierKeys.Alt | ModifierKeys.Shift, Key.N, "Control+Alt+Shift+N"),
        (ModifierKeys.Control | ModifierKeys.Alt | ModifierKeys.Shift, Key.F9, "Control+Alt+Shift+F9"),
    };

    // Push-to-talk, inside Netra's window only (see PushToTalkController).
    // Plain F9 is not an NVDA, JAWS or Narrator command.
    private const Key PushToTalkKey = Key.F9;

    private readonly ShellViewModel _shellViewModel;
    private readonly PushToTalkController _pushToTalkController;
    private readonly IFocusService _focusService;
    private GlobalHotKeyService? _globalHotKeyService;

    public MainWindow(
        ShellViewModel shellViewModel,
        LibraryView libraryView,
        StudyView studyView,
        ConversationView conversationView,
        PreferencesView preferencesView,
        LectureView lectureView,
        PushToTalkController pushToTalkController,
        IFocusService focusService)
    {
        InitializeComponent();

        _shellViewModel = shellViewModel;
        _pushToTalkController = pushToTalkController;
        _focusService = focusService;

        libraryView.DataContext = shellViewModel.Library;
        studyView.DataContext = shellViewModel.Study;
        // conversationView already has its DataContext set in its own
        // constructor (it also needs its ILiveRegionAnnouncer there).
        preferencesView.DataContext = shellViewModel.Preferences;

        LibraryTab.Content = libraryView;
        StudyTab.Content = studyView;
        ConversationTab.Content = conversationView;
        PreferencesTab.Content = preferencesView;
        LectureTab.Content = lectureView;
        // lectureView's DataContext is the LectureViewModel it was built with.

        shellViewModel.ShowLectureRequested += (_, _) =>
        {
            LectureTab.IsSelected = true;
            _focusService.MoveFocusToFirstFocusable(lectureView);
        };

        Loaded += OnLoaded;
        Closed += OnClosed;
        Deactivated += OnDeactivated;
        PreviewKeyDown += OnPreviewKeyDown;
        PreviewKeyUp += OnPreviewKeyUp;
    }

    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        _globalHotKeyService = new GlobalHotKeyService(this, ActivationHotKeyId);
        _globalHotKeyService.HotKeyPressed += OnActivationHotKeyPressed;

        string? registered = null;
        foreach (var candidate in ActivationCandidates)
        {
            if (_globalHotKeyService.TryRegister(candidate.Modifiers, candidate.Key))
            {
                registered = candidate.Spoken;
                break;
            }
        }

        // Not fatal when none registers — Netra stays fully usable inside its
        // own window — but it is told, never silently swallowed.
        _shellViewModel.Preferences.SetActivationShortcut(registered);
    }

    // Activation focuses Netra WITHOUT opening the microphone (client.md /
    // AgentSpec §8). It must never call ISpeechInputService.
    private void OnActivationHotKeyPressed(object? sender, EventArgs e)
    {
        Dispatcher.Invoke(() =>
        {
            if (WindowState == WindowState.Minimized)
            {
                WindowState = WindowState.Normal;
            }

            Activate();
            _focusService.MoveFocusToFirstFocusable(ShellTabControl);
        });
    }

    // Push-to-talk is scoped to this window (see PushToTalkController /
    // GlobalHotKeyService for why). Key-repeat while held is filtered
    // inside PushToTalkController, not here, so this handler can fire on
    // every repeat without re-triggering interrupt/StartListening.
    private async void OnPreviewKeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key != PushToTalkKey)
        {
            return;
        }

        e.Handled = true;
        await _pushToTalkController.OnKeyDownAsync(default).ConfigureAwait(true);
    }

    private void OnPreviewKeyUp(object sender, KeyEventArgs e)
    {
        if (e.Key != PushToTalkKey)
        {
            return;
        }

        e.Handled = true;
        _pushToTalkController.OnKeyUp();
    }

    // The push-to-talk key-up would go to another window: discard the capture.
    private void OnDeactivated(object? sender, EventArgs e) => _pushToTalkController.OnFocusLost();

    private void OnClosed(object? sender, EventArgs e) => _globalHotKeyService?.Dispose();

    private void OnStopCommandExecuted(object sender, ExecutedRoutedEventArgs e) =>
        _shellViewModel.Conversation.StopCommand.Execute(null);

    // The nine deterministic commands besides STOP, from anywhere in the window.
    private void OnNavigationCommandExecuted(object sender, ExecutedRoutedEventArgs e)
    {
        Protocol.Dto.NavigationCommandType? command = e.Command switch
        {
            _ when e.Command == KeyboardCommands.Pause => Protocol.Dto.NavigationCommandType.Pause,
            _ when e.Command == KeyboardCommands.Continue => Protocol.Dto.NavigationCommandType.Continue,
            _ when e.Command == KeyboardCommands.Repeat => Protocol.Dto.NavigationCommandType.Repeat,
            _ when e.Command == KeyboardCommands.Next => Protocol.Dto.NavigationCommandType.Next,
            _ when e.Command == KeyboardCommands.Previous => Protocol.Dto.NavigationCommandType.Previous,
            _ when e.Command == KeyboardCommands.WhereAmI => Protocol.Dto.NavigationCommandType.WhereAmI,
            _ when e.Command == KeyboardCommands.BackToReading => Protocol.Dto.NavigationCommandType.BackToReading,
            _ when e.Command == KeyboardCommands.UndoJump => Protocol.Dto.NavigationCommandType.UndoJump,
            _ when e.Command == KeyboardCommands.ReturnToQuestion => Protocol.Dto.NavigationCommandType.ReturnToQuestion,
            _ => null,
        };
        if (command is { } navigation)
        {
            _shellViewModel.Conversation.NavigationCommandRequest.Execute(navigation);
        }
    }

    private void OnGoToTabExecuted(object sender, ExecutedRoutedEventArgs e)
    {
        if (e.Parameter is string text && int.TryParse(text, out var index) && index >= 0 && index < ShellTabControl.Items.Count)
        {
            ShellTabControl.SelectedIndex = index;
            if (ShellTabControl.SelectedContent is UIElement content)
            {
                Dispatcher.BeginInvoke(() => _focusService.MoveFocusToFirstFocusable(content), System.Windows.Threading.DispatcherPriority.Input);
            }
        }
    }

    private void OnShowShortcutsExecuted(object sender, ExecutedRoutedEventArgs e)
    {
        PreferencesTab.IsSelected = true;
        if (PreferencesTab.Content is PreferencesView preferences)
        {
            Dispatcher.BeginInvoke(preferences.FocusShortcuts, System.Windows.Threading.DispatcherPriority.Input);
        }
    }
}
