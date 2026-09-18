using System.Windows;
using Netra.Desktop.Accessibility;
using Netra.Desktop.Audio;
using Netra.Desktop.Library;
using Netra.Desktop.Networking;
using Netra.Desktop.Speech;
using Netra.Desktop.State;
using Netra.Desktop.Threading;
using Netra.Desktop.ViewModels;
using Netra.Desktop.Views;

namespace Netra.Desktop;

// Manual composition root (no DI container package). Wires the services
// this task scaffolds together and shows MainWindow. Deliberately does NOT
// call ConnectionManager.ConnectAsync anywhere: no server endpoint is
// configured yet, and this scaffold must not make real backend calls.
public partial class App : Application
{
    private ConnectionManager? _connectionManager;
    private PlaybackController? _playbackController;
    private PlaybackAcknowledger? _playbackAcknowledger;
    private ShellViewModel? _shellViewModel;

    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);

        var sessionState = new ClientSessionState();
        var webSocketClient = new NetraWebSocketClient();
        var connectionManager = new ConnectionManager(webSocketClient, sessionState);
        _connectionManager = connectionManager;

        var playbackController = new PlaybackController();
        _playbackController = playbackController;

        var interruptionController = new InterruptionController(playbackController, connectionManager);
        _playbackAcknowledger = new PlaybackAcknowledger(playbackController, connectionManager);

        // Gates binary server->client audio frames (audio_frame_header
        // schema) before anything downstream may treat them as playable.
        // ConversationViewModel now calls AdmitGeneration when a new
        // response.segment generation starts (see docs/team/handoffs/M5.md
        // Gap 4 for what remains unwired: turning admitted bytes into
        // actual MediaPlayer/speaker output).
        var binaryAudioFrameProcessor = new BinaryAudioFrameProcessor(interruptionController);
        interruptionController.GenerationFenced += (_, _) => binaryAudioFrameProcessor.ClearActiveGeneration();
        webSocketClient.BinaryMessageReceived += binaryAudioFrameProcessor.OnBinaryMessageReceived;

        var speechInputService = new MicrophoneCapture();
        var liveRegionAnnouncer = new LiveRegionAnnouncer();
        var focusService = new FocusService();
        var uiDispatcher = new WpfUiDispatcher(Dispatcher);

        var libraryViewModel = new LibraryViewModel(
            new FixtureSourcePreparationService(),
            new FixtureVideoDiscoveryService());
        var studyViewModel = new StudyViewModel();
        var preferencesViewModel = new PreferencesViewModel(sessionState);
        var conversationViewModel = new ConversationViewModel(
            sessionState,
            connectionManager,
            playbackController,
            interruptionController,
            binaryAudioFrameProcessor,
            speechInputService,
            uiDispatcher);

        var shellViewModel = new ShellViewModel(libraryViewModel, studyViewModel, conversationViewModel, preferencesViewModel);
        _shellViewModel = shellViewModel;

        var pushToTalkController = new PushToTalkController(playbackController, interruptionController, speechInputService);

        var libraryView = new LibraryView();
        var studyView = new StudyView();
        var conversationView = new ConversationView(conversationViewModel, liveRegionAnnouncer);
        var preferencesView = new PreferencesView();

        var mainWindow = new MainWindow(
            shellViewModel,
            libraryView,
            studyView,
            conversationView,
            preferencesView,
            pushToTalkController,
            focusService);
        MainWindow = mainWindow;
        mainWindow.Show();
    }

    protected override async void OnExit(ExitEventArgs e)
    {
        _shellViewModel?.Dispose();
        _playbackAcknowledger?.Dispose();
        _playbackController?.Dispose();

        if (_connectionManager is not null)
        {
            await _connectionManager.DisposeAsync().ConfigureAwait(false);
        }

        base.OnExit(e);
    }
}
