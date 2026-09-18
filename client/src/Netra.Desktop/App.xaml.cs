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
// this task scaffolds together and shows MainWindow. Deliberately does NOT
// call ConnectionManager.ConnectAsync anywhere: no server endpoint is
// configured yet, and this scaffold must not make real backend calls.
public partial class App : Application
{
    private ConnectionManager? _connectionManager;
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

        // No credential source and no endpoint are configured: M1 has not
        // exposed the /v1/ws route or a credential issuance path (INT-10),
        // so ConnectAsync would refuse to connect even if it were called.
        var webSocketClient = new NetraWebSocketClient(credentials: null);
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

        var libraryViewModel = new LibraryViewModel(
            new FixtureSourcePreparationService(),
            new FixtureVideoDiscoveryService());
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

        if (_connectionManager is not null)
        {
            await _connectionManager.DisposeAsync().ConfigureAwait(false);
        }

        base.OnExit(e);
    }
}
