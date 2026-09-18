using System.Collections.ObjectModel;
using System.Threading;
using System.Windows.Input;
using Netra.Desktop.Audio;
using Netra.Desktop.Networking;
using Netra.Desktop.Protocol;
using Netra.Desktop.Protocol.Dto;
using Netra.Desktop.Speech;
using Netra.Desktop.State;
using Netra.Desktop.Threading;

namespace Netra.Desktop.ViewModels;

public sealed class TranscriptLine
{
    public required string Speaker { get; init; }
    public required string Text { get; init; }
}

// Renamed from the scaffold's MainViewModel now that MainWindow hosts four
// views (Library/Study/Conversation/Preferences) instead of one. Behaviour
// carried over unchanged except where noted:
//  - UI-thread marshaling was added around every mutation triggered by a
//    non-UI-thread event (see Threading/IUiDispatcher.cs for why the old
//    direct mutation was a genuine bug, not a style preference).
//  - response.segment now calls BinaryAudioFrameProcessor.AdmitGeneration
//    once per new generation id (previously nothing ever called it).
//  - session.snapshot/quiz.question/error are now handled instead of
//    dropped as TODOs.
public sealed class ConversationViewModel : ViewModelBase, IDisposable
{
    private readonly ClientSessionState _sessionState;
    private readonly ConnectionManager _connectionManager;
    private readonly InterruptionController _interruptionController;
    private readonly BinaryAudioFrameProcessor _binaryAudioFrameProcessor;
    private readonly ISpeechInputService _speechInputService;
    private readonly IUiDispatcher _dispatcher;

    // Final transcripts already turned into a turn, by their stable
    // TranscriptId. Recognition providers redeliver results on reconnect
    // and retry, and each send would otherwise get a fresh request_id, so
    // the server could not tell a redelivery from a second utterance.
    private readonly HashSet<Guid> _submittedTranscriptIds = new();

    // Generation ids already admitted to BinaryAudioFrameProcessor, so a
    // later response.segment for the same still-active generation does not
    // re-admit (which would otherwise reset the per-generation sequence
    // tracker and re-open a window for replayed low sequence numbers).
    private string? _lastAdmittedGenerationId;

    private string _inputText = string.Empty;
    private string _interimTranscript = string.Empty;
    private string _statusMessage = string.Empty;

    public ConversationViewModel(
        ClientSessionState sessionState,
        ConnectionManager connectionManager,
        IPlaybackController playbackController,
        InterruptionController interruptionController,
        BinaryAudioFrameProcessor binaryAudioFrameProcessor,
        ISpeechInputService speechInputService,
        IUiDispatcher dispatcher)
    {
        _sessionState = sessionState;
        _connectionManager = connectionManager;
        _interruptionController = interruptionController;
        _binaryAudioFrameProcessor = binaryAudioFrameProcessor;
        _speechInputService = speechInputService;
        _dispatcher = dispatcher;

        // playbackController is not read directly here; ownership of local
        // playback lives in InterruptionController/PlaybackAcknowledger. It
        // is accepted so the composition root (App.xaml.cs) can pass all
        // session-scoped services through a single constructor.
        _ = playbackController;

        _connectionManager.MessageReceived += OnServerMessageReceived;
        _speechInputService.TranscriptReceived += OnTranscriptReceived;

        SubmitCommand = new RelayCommand(_ => FireAndForget(SubmitAsync), _ => CanSubmit());
        StopCommand = new RelayCommand(_ => FireAndForget(StopAsync));
        NavigationCommandRequest = new RelayCommand(parameter => FireAndForget(() => SendNavigationCommandAsync(parameter)));
    }

    public ObservableCollection<TranscriptLine> Transcript { get; } = new();

    public string InputText
    {
        get => _inputText;
        set => SetField(ref _inputText, value);
    }

    public string InterimTranscript
    {
        get => _interimTranscript;
        private set => SetField(ref _interimTranscript, value);
    }

    public string StatusMessage
    {
        get => _statusMessage;
        private set => SetField(ref _statusMessage, value);
    }

    public ICommand SubmitCommand { get; }
    public ICommand StopCommand { get; }
    public ICommand NavigationCommandRequest { get; }

    private bool CanSubmit() => !string.IsNullOrWhiteSpace(InputText);

    private Task SubmitAsync() => SubmitAsync(Protocol.Dto.InputMode.Keyboard);

    private async Task SubmitAsync(Protocol.Dto.InputMode inputMode)
    {
        var utterance = InputText.Trim();
        if (utterance.Length == 0)
        {
            return;
        }

        InputText = string.Empty;
        Transcript.Add(new TranscriptLine { Speaker = "You", Text = utterance });

        await _connectionManager.SendTurnSubmitAsync(
            new TurnSubmitPayload
            {
                Utterance = utterance,
                // Reports how the turn actually arrived. Labelling a spoken
                // turn as keyboard input would misreport it to the server,
                // which uses input_mode to reason about transcription
                // correction and reduced modes.
                InputMode = inputMode,
                ExpectedSessionVersion = _sessionState.SessionVersion,
                ClientTimestamp = DateTimeOffset.UtcNow,
            },
            CancellationToken.None).ConfigureAwait(false);
    }

    private async Task StopAsync()
    {
        // Local stop is synchronous inside InterruptionController.StopAsync;
        // the server is only notified after playback has already halted.
        await _interruptionController.StopAsync(CancelReason.UserStop, CancellationToken.None).ConfigureAwait(false);
        StatusMessage = "Stopped.";
    }

    private async Task SendNavigationCommandAsync(object? parameter)
    {
        if (parameter is not NavigationCommandType command)
        {
            return;
        }

        await _connectionManager.SendNavigationCommandAsync(
            new NavigationCommandPayload
            {
                Command = command,
                ExpectedSessionVersion = _sessionState.SessionVersion,
            },
            CancellationToken.None).ConfigureAwait(false);
    }

    // Fires from a future recognition-provider thread, not the UI thread —
    // every mutation below is marshaled.
    private void OnTranscriptReceived(object? sender, TranscriptReceivedEventArgs e)
    {
        if (!e.IsFinal)
        {
            // Interim transcript: live captioning only. Must never trigger
            // navigation or be submitted as a turn. An interim "next"
            // followed by a final "next question" must therefore not
            // navigate on the interim.
            _dispatcher.Invoke(() => InterimTranscript = e.Text);
            return;
        }

        // A provider that redelivers the same final result must not create
        // a second turn (client.md: "Deduplicate repeated final events
        // according to protocol identity").
        if (!_submittedTranscriptIds.Add(e.TranscriptId))
        {
            return;
        }

        _dispatcher.Invoke(() => InterimTranscript = string.Empty);
        _dispatcher.Invoke(() => InputText = e.Text);
        FireAndForget(() => SubmitAsync(Protocol.Dto.InputMode.Voice));
    }

    // Fires from the WebSocket receive loop thread, not the UI thread.
    private void OnServerMessageReceived(object? sender, ServerToClientEnvelope envelope)
    {
        switch (envelope.Type)
        {
            case ServerMessageType.ResponseSegment:
                HandleResponseSegment(envelope);
                break;

            case ServerMessageType.SessionSnapshot:
                HandleSessionSnapshot(envelope);
                break;

            case ServerMessageType.QuizQuestion:
                HandleQuizQuestion(envelope);
                break;

            case ServerMessageType.Error:
                HandleError(envelope);
                break;
        }
    }

    private void HandleResponseSegment(ServerToClientEnvelope envelope)
    {
        var segment = MessageParser.ParseResponseSegment(envelope);

        // Admit the generation before it can be treated as playable, and
        // only once per generation id — a later segment.response for the
        // SAME still-active generation must not reset the sequence tracker.
        // This is the wiring the scaffold left as a TODO: nothing previously
        // ever called AdmitGeneration, so BinaryAudioFrameProcessor could
        // never actually pass a frame through even once headers parsed
        // correctly.
        if (!string.Equals(_lastAdmittedGenerationId, segment.GenerationId, StringComparison.Ordinal))
        {
            _binaryAudioFrameProcessor.AdmitGeneration(segment.GenerationId);
            _lastAdmittedGenerationId = segment.GenerationId;
        }

        _dispatcher.Invoke(() =>
        {
            _sessionState.CurrentGenerationId = segment.GenerationId;

            if (!_interruptionController.IsCancelled(segment.GenerationId))
            {
                Transcript.Add(new TranscriptLine { Speaker = "Tutor", Text = segment.Text });
            }
        });
    }

    private void HandleSessionSnapshot(ServerToClientEnvelope envelope)
    {
        var snapshot = MessageParser.ParseSessionSnapshot(envelope);

        _dispatcher.Invoke(() =>
        {
            // Snapshot carries the server's authoritative version; it is
            // not a client mutation, so it must reconcile even when it does
            // not strictly increase (e.g. the very first snapshot after
            // Initialize()). TryAdvanceSessionVersion only ever moves the
            // counter forward, which is correct for OUR mutations but wrong
            // for accepting a freshly-reconciled snapshot on connect —
            // Initialize() is the right primitive here.
            _sessionState.Initialize(_sessionState.SessionId, snapshot.SessionVersion);
            _sessionState.InteractionMode = snapshot.InteractionMode;
            _sessionState.ActiveSourceVersionId = snapshot.ActiveSourceVersionId;
            _sessionState.CurrentBlockId = snapshot.CurrentBlockId;
            _sessionState.CurrentSentenceId = snapshot.CurrentSentenceId;
            _sessionState.LastAcknowledgedSentenceId = snapshot.LastAcknowledgedSentenceId;
            _sessionState.ActiveTutorLessonId = snapshot.ActiveLesson?.LessonId.ToString();
            _sessionState.PendingQuestionId = snapshot.PendingQuestion?.QuestionId;
            _sessionState.LastResultSetId = snapshot.LastResultSet?.ResultSetId;

            StatusMessage = $"Session restored: {snapshot.InteractionMode}.";
        });
    }

    private void HandleQuizQuestion(ServerToClientEnvelope envelope)
    {
        var question = MessageParser.ParseQuizQuestion(envelope);

        _dispatcher.Invoke(() =>
        {
            _sessionState.PendingQuestionId = question.QuestionId;
            // The prompt is public-safe by construction (QuizQuestionPayload
            // structurally excludes answer_key/rubric); it is therefore safe
            // to surface as ordinary transcript/status text.
            Transcript.Add(new TranscriptLine { Speaker = "Question", Text = question.Prompt });
        });
    }

    private void HandleError(ServerToClientEnvelope envelope)
    {
        var error = MessageParser.ParseError(envelope);

        _dispatcher.Invoke(() =>
        {
            if (error.CurrentSessionVersion is { } currentVersion)
            {
                _sessionState.Initialize(_sessionState.SessionId, currentVersion);
            }

            // error.message is the schema's own "safe/accessible text only"
            // field; error.code/retryable/correlation_id are for programmatic
            // handling, not for reading aloud, so only Message is surfaced.
            StatusMessage = error.Message;
        });
    }

    private static async void FireAndForget(Func<Task> operation)
    {
        try
        {
            await operation().ConfigureAwait(false);
        }
        catch (Exception)
        {
            // TODO: surface command failures via StatusMessage/IScreenReaderService
            // once an error-presentation policy is defined. Never let an
            // async command crash the app.
        }
    }

    public void Dispose()
    {
        _connectionManager.MessageReceived -= OnServerMessageReceived;
        _speechInputService.TranscriptReceived -= OnTranscriptReceived;
    }
}
