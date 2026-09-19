using System.Threading;
using System.Windows;
using Netra.Desktop.Accessibility;
using Netra.Desktop.Audio;
using Netra.Desktop.Diagnostics;
using Netra.Desktop.Library;
using Netra.Desktop.Networking;
using Netra.Desktop.Speech;
using Netra.Desktop.State;
using Netra.Desktop.Threading;
using Netra.Desktop.ViewModels;
using Netra.Desktop.Views;

namespace Netra.Desktop;

// Manual composition root (no DI container package). Wires the services
// together and shows MainWindow.
//
// Two explicit modes:
// - Live: NETRA_API_ENDPOINT names the server's WebSocket endpoint (wss, or
//   ws to loopback). The bearer credential is read from Windows Credential
//   Manager (WindowsCredentialManagerSource). LiveSession creates a session
//   over HTTP, connects /v1/ws (session.resume restores state), and the
//   library lists the account's real sources. A failed start is retried by
//   the library's Refresh; no restart is needed.
// - Fixture: no endpoint configured. No network call is made, and the status
//   text says the data shown is fixture data.
// Credential issuance (PKCE sign-in) and the production credential store are
// still undecided (INT-10a); a stored operator token is an integration aid,
// not a production sign-in flow.
public partial class App : Application
{
    public const string EndpointVariable = "NETRA_API_ENDPOINT";

    private ConnectionManager? _connectionManager;
    private ReconnectCoordinator? _reconnectCoordinator;
    private NetraApiClient? _apiClient;
    private CancellationTokenSource? _liveStart;
    private PlaybackController? _playbackController;
    private PlaybackAcknowledger? _playbackAcknowledger;
    private SegmentPlaybackQueue? _segmentPlaybackQueue;
    private TempFileSegmentAudioStore? _segmentAudioStore;
    private ShellViewModel? _shellViewModel;

    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);

        if (PlaybackMeasurementHarness.TryParse(e.Args, out var outPath, out var trials, out var stopAfterMs, out var audible))
        {
            RunPlaybackMeasurement(outPath, trials, stopAfterMs, audible);
            return;
        }

        var sessionState = new ClientSessionState();
        var timeline = new PlaybackTimeline();

        var (liveEndpoint, modeNotice) = ResolveLiveEndpoint();
        var credentials = liveEndpoint is null ? null : new WindowsCredentialManagerSource();
        var webSocketClient = new NetraWebSocketClient(credentials);
        var connectionManager = new ConnectionManager(webSocketClient, sessionState);
        _connectionManager = connectionManager;

        var playbackController = new PlaybackController();
        _playbackController = playbackController;

        var interruptionController = new InterruptionController(
            playbackController, connectionManager, () => sessionState.CurrentGenerationId, timeline);
        _playbackAcknowledger = new PlaybackAcknowledger(playbackController, connectionManager, timeline);

        // Server audio path: frame admission (audio_frame_header schema)
        // -> per-segment assembly -> bounded UI-thread queue -> MediaPlayer.
        // ConversationViewModel admits a generation when its response.segment
        // arrives and registers each segment's sentence id for acks.
        var binaryAudioFrameProcessor = new BinaryAudioFrameProcessor(interruptionController);
        var segmentAssembler = new SegmentAudioAssembler();
        interruptionController.GenerationFenced += (_, _) =>
        {
            binaryAudioFrameProcessor.ClearActiveGeneration();
            segmentAssembler.Reset();
        };
        webSocketClient.BinaryMessageReceived += binaryAudioFrameProcessor.OnBinaryMessageReceived;
        binaryAudioFrameProcessor.AudioBytesAdmitted += segmentAssembler.OnAudioBytesAdmitted;
        binaryAudioFrameProcessor.FrameRejected += (_, rejection) =>
            timeline.Record(PlaybackMilestone.FrameRejected, rejection.GenerationId, detail: rejection.Reason);
        segmentAssembler.SegmentStarted += (_, started) =>
            timeline.Record(PlaybackMilestone.FirstFrameReceived, started.GenerationId, started.SegmentId);
        segmentAssembler.SegmentDiscarded += (_, discarded) =>
            timeline.Record(PlaybackMilestone.SegmentDiscarded, discarded.GenerationId, discarded.SegmentId, discarded.Reason);

        _segmentAudioStore = new TempFileSegmentAudioStore();
        var segmentPlaybackQueue = new SegmentPlaybackQueue(playbackController, interruptionController, _segmentAudioStore, timeline);
        _segmentPlaybackQueue = segmentPlaybackQueue;
        playbackController.PlaybackFailed += segmentPlaybackQueue.OnPlaybackFailed;

        var speechInputService = new MicrophoneCapture();
        var liveRegionAnnouncer = new LiveRegionAnnouncer();
        var focusService = new FocusService();
        var uiDispatcher = new WpfUiDispatcher(Dispatcher);
        segmentAssembler.SegmentCompleted += (_, segment) =>
        {
            timeline.Record(PlaybackMilestone.SegmentAudioComplete, segment.GenerationId, segment.SegmentId);
            uiDispatcher.Invoke(() => segmentPlaybackQueue.Enqueue(segment));
        };

        LibraryServerAccess? serverAccess = null;
        LiveSession? liveSession = null;
        if (liveEndpoint is not null && credentials is not null)
        {
            _apiClient = new NetraApiClient(NetraApiClient.HttpBaseFor(liveEndpoint), credentials);
            _reconnectCoordinator = new ReconnectCoordinator(webSocketClient, connectionManager, sessionState, liveEndpoint);
            liveSession = new LiveSession(_apiClient, sessionState, connectionManager, _reconnectCoordinator.ConnectAsync);
            serverAccess = new LibraryServerAccess(new ApiSourceCatalog(_apiClient, sessionState), sessionState, liveSession);
        }

        // Upload and YouTube discovery stay fixture services: the upload/job
        // contract is still empty and discovery has no server route yet.
        var libraryViewModel = new LibraryViewModel(
            new FixtureSourcePreparationService(),
            new FixtureVideoDiscoveryService(),
            serverAccess);
        var studyViewModel = new StudyViewModel();
        var preferencesViewModel = new PreferencesViewModel(sessionState, timeline);
        var conversationViewModel = new ConversationViewModel(
            sessionState,
            connectionManager,
            playbackController,
            interruptionController,
            binaryAudioFrameProcessor,
            speechInputService,
            uiDispatcher,
            segmentPlaybackQueue,
            timeline);
        segmentPlaybackQueue.StatusChanged += (_, message) => conversationViewModel.ReportStatus(message);

        var shellViewModel = new ShellViewModel(libraryViewModel, studyViewModel, conversationViewModel, preferencesViewModel);
        _shellViewModel = shellViewModel;

        var pushToTalkController = new PushToTalkController(
            playbackController,
            interruptionController,
            speechInputService,
            () => conversationViewModel.ReportStatus("Voice input is not available in this version. Type your question instead."));

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

        if (liveSession is null || _reconnectCoordinator is null)
        {
            conversationViewModel.ReportStatus(modeNotice);
            return;
        }

        _reconnectCoordinator.StatusChanged += (_, message) => conversationViewModel.ReportStatus(message);
        _liveStart = new CancellationTokenSource();
        _ = StartLiveSessionAsync(liveSession, libraryViewModel, conversationViewModel, _liveStart.Token);
    }

    // Endpoint from the environment; the value is validated, never logged.
    private static (Uri? Endpoint, string Notice) ResolveLiveEndpoint()
    {
        const string fixture = "Offline mode: no Netra server is configured. Sources and lecture results shown are fixture data.";
        var configured = Environment.GetEnvironmentVariable(EndpointVariable);
        if (string.IsNullOrWhiteSpace(configured))
        {
            return (null, fixture);
        }

        try
        {
            return (ServerEndpoint.Validate(new Uri(configured.Trim(), UriKind.Absolute)), string.Empty);
        }
        catch (Exception ex) when (ex is UriFormatException or InvalidServerEndpointException)
        {
            return (null, "Offline mode: the configured Netra server address is not valid. Shown data is fixture data.");
        }
    }

    // UI thread (network waits are awaited, never blocked on). Starts the
    // live session, then loads sources. On failure the status says why and
    // how to retry; it does not promise offline reading: no study-material
    // cache is wired into the app yet.
    private static async Task StartLiveSessionAsync(
        ILiveSession live, LibraryViewModel library, ConversationViewModel conversation, CancellationToken cancellationToken)
    {
        try
        {
            conversation.ReportStatus("Connecting to Netra.");
            await live.EnsureStartedAsync(cancellationToken);
        }
        catch (Exception ex)
        {
            if (!cancellationToken.IsCancellationRequested)
            {
                conversation.ReportStatus(
                    $"Not connected. {FailureText.Describe(ex, "connect", cancellationToken)} Choose Refresh in the library to try again.");
            }

            return;
        }

        await library.RefreshSourcesAsync(cancellationToken);
    }

    // Local hardware measurement mode; no window, no server. Exit code 0 on a
    // written report, 1 otherwise.
    private async void RunPlaybackMeasurement(string outPath, int trials, int stopAfterMs, bool audible)
    {
        ShutdownMode = ShutdownMode.OnExplicitShutdown;
        var exitCode = 1;
        try
        {
            var report = await new PlaybackMeasurementHarness(Dispatcher, trials, stopAfterMs, audible).RunAsync();
            System.IO.File.WriteAllText(outPath, report);
            exitCode = 0;
        }
        catch (Exception ex)
        {
            System.IO.File.WriteAllText(outPath, "{\"error\":\"" + ex.GetType().Name + "\"}");
        }

        Shutdown(exitCode);
    }

    protected override async void OnExit(ExitEventArgs e)
    {
        _shellViewModel?.Dispose();
        _playbackAcknowledger?.Dispose();
        _segmentPlaybackQueue?.Dispose();
        _playbackController?.Dispose();
        _segmentAudioStore?.Dispose();

        // Stop the start-up attempt before disposing what it uses.
        _liveStart?.Cancel();
        if (_reconnectCoordinator is not null)
        {
            await _reconnectCoordinator.DisposeAsync().ConfigureAwait(true);
        }

        _apiClient?.Dispose();
        _liveStart?.Dispose();

        if (_connectionManager is not null)
        {
            await _connectionManager.DisposeAsync().ConfigureAwait(false);
        }

        base.OnExit(e);
    }
}
