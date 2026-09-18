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

    // Placeholders pending a live-Windows conflict test (client.md /
    // AgentSpec §8 "exact shortcuts must pass conflict tests" — see
    // docs/team/handoffs/M5.md Gap 5). Chosen only to avoid the most common
    // OS-reserved combinations (Win+*, Ctrl+Alt+Del, Alt+Tab, Alt+F4).
    private static readonly (ModifierKeys Modifiers, Key Key) ActivationHotKey = (ModifierKeys.Control | ModifierKeys.Alt, Key.N);
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

        Loaded += OnLoaded;
        Closed += OnClosed;
        PreviewKeyDown += OnPreviewKeyDown;
        PreviewKeyUp += OnPreviewKeyUp;
    }

    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        _globalHotKeyService = new GlobalHotKeyService(this, ActivationHotKeyId);
        _globalHotKeyService.HotKeyPressed += OnActivationHotKeyPressed;

        var registered = _globalHotKeyService.TryRegister(ActivationHotKey.Modifiers, ActivationHotKey.Key);
        if (!registered)
        {
            // Not fatal — the app remains fully usable by keyboard/mouse
            // inside its own window — but must not be silently swallowed.
            // See docs/team/handoffs/M5.md Gap 5.
            _shellViewModel.Preferences.Refresh();
        }
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

    private void OnClosed(object? sender, EventArgs e) => _globalHotKeyService?.Dispose();

    private void OnStopCommandExecuted(object sender, ExecutedRoutedEventArgs e) =>
        _shellViewModel.Conversation.StopCommand.Execute(null);

    private void OnRepeatCommandExecuted(object sender, ExecutedRoutedEventArgs e) =>
        _shellViewModel.Conversation.NavigationCommandRequest.Execute(Protocol.Dto.NavigationCommandType.Repeat);

    private void OnNextCommandExecuted(object sender, ExecutedRoutedEventArgs e) =>
        _shellViewModel.Conversation.NavigationCommandRequest.Execute(Protocol.Dto.NavigationCommandType.Next);

    private void OnPreviousCommandExecuted(object sender, ExecutedRoutedEventArgs e) =>
        _shellViewModel.Conversation.NavigationCommandRequest.Execute(Protocol.Dto.NavigationCommandType.Previous);
}
