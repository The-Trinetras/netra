using System.Windows;
using Netra.Desktop.Accessibility;
using Netra.Desktop.Audio;
using Netra.Desktop.Networking;
using Netra.Desktop.Speech;
using Netra.Desktop.State;
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
    private MainViewModel? _mainViewModel;

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
        // See Audio/BinaryAudioFrameProcessor.cs: turning admitted frames
        // into actual MediaPlayer output is a separate, unimplemented
        // integration step — WPF's MediaPlayer plays from a Uri/stream, not
        // incremental byte chunks, and no response-delivery path exists yet
        // to call AdmitGeneration when a new response starts speaking.
        var binaryAudioFrameProcessor = new BinaryAudioFrameProcessor(interruptionController);
        interruptionController.GenerationFenced += (_, _) => binaryAudioFrameProcessor.ClearActiveGeneration();
        webSocketClient.BinaryMessageReceived += binaryAudioFrameProcessor.OnBinaryMessageReceived;

        var speechInputService = new MicrophoneCapture();
        var liveRegionAnnouncer = new LiveRegionAnnouncer();

        var mainViewModel = new MainViewModel(
            sessionState,
            connectionManager,
            playbackController,
            interruptionController,
            speechInputService);
        _mainViewModel = mainViewModel;

        var mainWindow = new MainWindow(mainViewModel, liveRegionAnnouncer);
        MainWindow = mainWindow;
        mainWindow.Show();
    }

    protected override async void OnExit(ExitEventArgs e)
    {
        _mainViewModel?.Dispose();
        _playbackAcknowledger?.Dispose();
        _playbackController?.Dispose();

        if (_connectionManager is not null)
        {
            await _connectionManager.DisposeAsync().ConfigureAwait(false);
        }

        base.OnExit(e);
    }
}
