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
using Netra.Desktop.Video;
using Netra.Desktop.ViewModels;
using Netra.Desktop.Views;

namespace Netra.Desktop;

// Manual composition root (no DI container package). Wires the services
// together and shows MainWindow.
//
// Two explicit modes:
// - Live: NETRA_API_ENDPOINT names the server's WebSocket endpoint (wss, or
//   ws to loopback). The device credential lives in Windows Credential
//   Manager (decision D-CRED). On first run, or when the server no longer
//   accepts it, the sign-in dialog asks for a one-time access code and
//   exchanges it (C2 pending). LiveSession then creates a session over HTTP,
//   connects /v1/ws (session.resume restores state), and the library lists
//   the account's real sources. A failed start is retried by the library's
//   Refresh; no restart is needed.
// - Fixture: no endpoint configured. No network call is made, and the status
//   text says the data shown is fixture data.
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
    private MicrophoneCapture? _speechInputService;
    private SignedInCredentials? _credentials;
    private HttpAccessCodeExchange? _accessCodeExchange;
    private LiveSession? _liveSession;
    private LiveAccount? _account;
    private LibraryViewModel? _libraryViewModel;
    private ConversationViewModel? _conversationViewModel;
    private LecturePlayerController? _lecturePlayer;

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
        var credentials = liveEndpoint is null ? null : new SignedInCredentials(new WindowsCredentialManagerSource());
        _credentials = credentials;
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

        // Voice input over the live connection (D-MIC; C1 pending). In
        // fixture mode the socket never connects, so a press says voice
        // input needs a connection and the microphone is never opened.
        var speechInputService = new MicrophoneCapture(connectionManager);
        _speechInputService = speechInputService;
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
            _accessCodeExchange = new HttpAccessCodeExchange(NetraApiClient.HttpBaseFor(liveEndpoint));
            _reconnectCoordinator = new ReconnectCoordinator(webSocketClient, connectionManager, sessionState, liveEndpoint);
            liveSession = new LiveSession(_apiClient, sessionState, connectionManager, _reconnectCoordinator.ConnectAsync);
            _liveSession = liveSession;
            serverAccess = new LibraryServerAccess(new ApiSourceCatalog(_apiClient, sessionState), sessionState, liveSession);
        }

        // Upload goes to the server whenever there is one to talk to (C5
        // uploads/job routes). Without a live endpoint or credentials there is
        // nothing to upload to, so the fixture stands in and marks every entry
        // it touches IsFixtureSourced. YouTube discovery stays a fixture: it
        // still has no server route.
        ISourcePreparationService preparation = _apiClient is null
            ? new FixtureSourcePreparationService()
            : new HttpSourcePreparationService(_apiClient, sessionState);
        var libraryViewModel = new LibraryViewModel(
            preparation,
            new FixtureVideoDiscoveryService(),
            serverAccess);
        _libraryViewModel = libraryViewModel;
        var studyViewModel = new StudyViewModel(isLive: liveEndpoint is not null);

        // Lecture player (F8): the view owns the WebView2 the player page runs in.
        var lectureView = new LectureView(liveRegionAnnouncer);
        var lecturePlayer = new LecturePlayerController(lectureView.CreatePlayerSurface());
        _lecturePlayer = lecturePlayer;
        var lectureViewModel = new LectureViewModel(lecturePlayer);
        lectureView.DataContext = lectureViewModel;

        var conversationViewModel = new ConversationViewModel(
            sessionState,
            connectionManager,
            playbackController,
            interruptionController,
            binaryAudioFrameProcessor,
            speechInputService,
            uiDispatcher,
            segmentPlaybackQueue,
            timeline,
            lecturePlayer);
        segmentPlaybackQueue.StatusChanged += (_, message) => conversationViewModel.ReportStatus(message);
        _conversationViewModel = conversationViewModel;

        if (liveSession is not null && credentials is not null && _reconnectCoordinator is not null)
        {
            var reconnect = _reconnectCoordinator;
            _account = new LiveAccount(
                credentials,
                ShowSignIn,
                () => StartLiveSessionAsync(promptSignIn: false),
                async () =>
                {
                    await reconnect.DisconnectAsync(CancellationToken.None);
                    liveSession.Forget();
                    sessionState.Reset();
                },
                () =>
                {
                    conversationViewModel.ClearForSignOut();
                    libraryViewModel.ClearServerSources();
                    studyViewModel.ClearForSignOut();
                },
                conversationViewModel.ReportStatus);
        }

        var preferencesViewModel = new PreferencesViewModel(sessionState, timeline, _account, isLive: liveEndpoint is not null);

        var shellViewModel = new ShellViewModel(libraryViewModel, studyViewModel, conversationViewModel, preferencesViewModel, lectureViewModel);
        _shellViewModel = shellViewModel;

        var pushToTalkController = new PushToTalkController(playbackController, interruptionController, speechInputService, lecturePlayer);

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
            lectureView,
            pushToTalkController,
            focusService);
        MainWindow = mainWindow;
        mainWindow.Show();

        if (liveSession is null || _reconnectCoordinator is null || _account is null || credentials is null)
        {
            conversationViewModel.ReportStatus(modeNotice);
            return;
        }

        _reconnectCoordinator.StatusChanged += (_, message) => conversationViewModel.ReportStatus(message);
        _reconnectCoordinator.StateChanged += (_, _) => uiDispatcher.Invoke(preferencesViewModel.Refresh);
        _liveStart = new CancellationTokenSource();

        // First run: ask for the access code once the main window is up, so
        // the dialog has an owner and focus returns there afterwards.
        if (!credentials.HasCredential)
        {
            Dispatcher.BeginInvoke(new Action(() => _ = _account.SignInAsync()));
            return;
        }

        _ = StartLiveSessionAsync(promptSignIn: true);
    }

    // The sign-in dialog (UI thread, modal). Returns its final status after a
    // sign-in, or null when the student cancelled.
    private string? ShowSignIn(string? reason)
    {
        var viewModel = new SignInViewModel(_accessCodeExchange!, _credentials!, reason);
        var dialog = new SignInWindow(viewModel, new LiveRegionAnnouncer());
        if (MainWindow is { IsVisible: true } owner)
        {
            dialog.Owner = owner;
        }

        return dialog.ShowDialog() == true ? viewModel.StatusMessage : null;
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
    // cache is wired into the app yet. A credential the server no longer
    // accepts opens the sign-in dialog once (promptSignIn), never in a loop.
    private async Task StartLiveSessionAsync(bool promptSignIn)
    {
        var cancellationToken = _liveStart?.Token ?? CancellationToken.None;
        try
        {
            _conversationViewModel!.ReportStatus("Connecting to Netra.");
            await _liveSession!.EnsureStartedAsync(cancellationToken);
        }
        catch (Exception ex)
        {
            if (cancellationToken.IsCancellationRequested)
            {
                return;
            }

            if (promptSignIn && _account is not null && IsSignInProblem(ex))
            {
                await _account.SignInAsync("Netra did not accept this computer's sign-in. It may have expired.");
                return;
            }

            _conversationViewModel!.ReportStatus(
                $"Not connected. {FailureText.Describe(ex, "connect", cancellationToken)} Choose Refresh in the library to try again.");
            return;
        }

        await _libraryViewModel!.RefreshSourcesAsync(cancellationToken);
    }

    private static bool IsSignInProblem(Exception ex) =>
        ex is CredentialUnavailableException or CredentialRejectedException
        || ex is ApiErrorException { Error.Code: Protocol.Dto.ErrorCode.AuthRequired }
        || ex is ApiErrorException { StatusCode: 401 };

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
        _speechInputService?.Dispose();
        _shellViewModel?.Dispose();
        _lecturePlayer?.Dispose();
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
        _accessCodeExchange?.Dispose();
        _liveStart?.Dispose();

        if (_connectionManager is not null)
        {
            await _connectionManager.DisposeAsync().ConfigureAwait(false);
        }

        base.OnExit(e);
    }
}
