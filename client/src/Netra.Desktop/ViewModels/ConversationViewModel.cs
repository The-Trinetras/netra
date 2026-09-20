using System.Collections.ObjectModel;
using System.Threading;
using System.Windows.Input;
using Netra.Desktop.Audio;
using Netra.Desktop.Diagnostics;
using Netra.Desktop.Networking;
using Netra.Desktop.Protocol;
using Netra.Desktop.Protocol.Dto;
using Netra.Desktop.Speech;
using Netra.Desktop.State;
using Netra.Desktop.Threading;
using Netra.Desktop.Video;

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
    private readonly SegmentPlaybackQueue? _playbackQueue;
    private readonly PlaybackTimeline? _timeline;
    private readonly ILecturePause? _lecture;
    private readonly IPlaybackController _playbackController;

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
    private string _voiceStatus = "Voice input off.";
    private string _voiceTranscript = string.Empty;
    private string _activeSourceSummary = "Open a source from Library to ask about your material.";
    private string? _namedSourceVersionId;
    private readonly HashSet<Guid> _staleRequests = new();
    private readonly Queue<Guid> _staleRequestOrder = new();

    public ConversationViewModel(
        ClientSessionState sessionState,
        ConnectionManager connectionManager,
        IPlaybackController playbackController,
        InterruptionController interruptionController,
        BinaryAudioFrameProcessor binaryAudioFrameProcessor,
        ISpeechInputService speechInputService,
        IUiDispatcher dispatcher,
        SegmentPlaybackQueue? playbackQueue = null,
        PlaybackTimeline? timeline = null,
        ILecturePause? lecture = null)
    {
        _lecture = lecture;
        _playbackQueue = playbackQueue;
        _timeline = timeline;
        _sessionState = sessionState;
        _connectionManager = connectionManager;
        _interruptionController = interruptionController;
        _binaryAudioFrameProcessor = binaryAudioFrameProcessor;
        _speechInputService = speechInputService;
        _dispatcher = dispatcher;

        // Local playback belongs to InterruptionController/PlaybackAcknowledger;
        // it is read here only to pause and continue speech at once, before
        // the server hears of it.
        _playbackController = playbackController;

        _connectionManager.MessageReceived += OnServerMessageReceived;
        _speechInputService.TranscriptReceived += OnTranscriptReceived;
        _speechInputService.StatusChanged += OnVoiceStatusChanged;

        SubmitCommand = new RelayCommand(_ => FireAndForget(SubmitTypedAsync, "send your question"), _ => CanSubmit());
        StopCommand = new RelayCommand(_ => FireAndForget(StopAsync, "stop"));
        NavigationCommandRequest = new RelayCommand(parameter => FireAndForget(() => SendNavigationCommandAsync(parameter), "send that command"));
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

    // Shown, not announced: it changes while the microphone is open, and a
    // screen reader reading it aloud would be recorded into the question.
    // Announcements that matter arrive through StatusMessage once capture ends.
    public string VoiceStatus
    {
        get => _voiceStatus;
        private set => SetField(ref _voiceStatus, value);
    }

    // Kept after release so the student can see what was heard from any tab,
    // even after a response replaces the general status message.
    public string VoiceTranscript
    {
        get => _voiceTranscript;
        private set => SetField(ref _voiceTranscript, value);
    }

    public string ActiveSourceSummary
    {
        get => _activeSourceSummary;
        private set => SetField(ref _activeSourceSummary, value);
    }

    public void SetOpenedSource(Library.CatalogSource source)
    {
        _namedSourceVersionId = source.ActiveSourceVersionId;
        ActiveSourceSummary = $"Studying: {source.Title}. Ask about this source, or choose another in Library.";
    }

    public ICommand SubmitCommand { get; }
    public ICommand StopCommand { get; }
    public ICommand NavigationCommandRequest { get; }
    public event EventHandler<SessionSnapshotPayload>? SessionSnapshotReceived;
    public event EventHandler<ResponseSegmentPayload>? SourceReadingReceived;

    public void ReadOpenedSource() => FireAndForget(async () =>
    {
        _speechInputService.AbortListening();
        await _interruptionController.StopAsync(CancelReason.Navigation, CancellationToken.None).ConfigureAwait(false);
        await _connectionManager.SendNavigationCommandAsync(new NavigationCommandPayload
        {
            Command = NavigationCommandType.Repeat,
            NavigationUnit = NavigationUnit.Block,
            ExpectedSessionVersion = _sessionState.SessionVersion,
        }, CancellationToken.None).ConfigureAwait(false);
    }, "read the opened source");

    private bool CanSubmit() => !string.IsNullOrWhiteSpace(InputText);

    private Task SubmitTypedAsync()
    {
        var utterance = InputText.Trim();
        if (utterance.Length == 0)
        {
            return Task.CompletedTask;
        }

        InputText = string.Empty;
        return SubmitTypedAsync(utterance);
    }

    // A playing lecture is paused first, so Netra's answer never talks over
    // it and the question is about where it stopped. (A spoken question has
    // already paused it at the push-to-talk key.) Sending that time with the
    // turn waits for C8 on the server.
    private async Task SubmitTypedAsync(string utterance)
    {
        if (_lecture is not null)
        {
            await _lecture.PauseForQuestionAsync(CancellationToken.None).ConfigureAwait(false);
        }

        await SubmitAsync(utterance, Protocol.Dto.InputMode.Keyboard).ConfigureAwait(false);
    }

    // The typed draft in InputText is left alone for a voice turn: speaking
    // must not overwrite what the student was typing.
    private async Task SubmitAsync(string utterance, Protocol.Dto.InputMode inputMode)
    {
        var speaker = inputMode == Protocol.Dto.InputMode.Voice ? "You (voice)" : "You";
        _dispatcher.Invoke(() => Transcript.Add(new TranscriptLine { Speaker = speaker, Text = utterance }));

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
        // STOP also ends a capture in progress: nothing of it is sent on.
        _speechInputService.AbortListening();

        // Local stop is synchronous inside InterruptionController.StopAsync;
        // the server is only notified after playback has already halted.
        // Local silence and fencing do not depend on that notification (a
        // disconnect fences the generation too), so a cancel that cannot be
        // sent must not contradict the stop the student already heard.
        try
        {
            await _interruptionController.StopAsync(CancelReason.UserStop, CancellationToken.None).ConfigureAwait(false);
        }
        catch (Exception)
        {
        }

        ReportStatus("Stopped.");
    }

    private async Task SendNavigationCommandAsync(object? parameter)
    {
        if (parameter is not NavigationCommandType command)
        {
            return;
        }

        // Pause silences Netra at once, like STOP but keeping what was paused;
        // continue resumes only a paused, uncancelled segment (the player
        // refuses cancelled ones). The server then pauses or resumes its own
        // generation, or reads onward when nothing was paused.
        if (command == NavigationCommandType.Pause)
        {
            _playbackController.Pause();
        }
        else if (command == NavigationCommandType.Continue)
        {
            _playbackController.Resume();
        }

        await _connectionManager.SendNavigationCommandAsync(
            new NavigationCommandPayload
            {
                Command = command,
                ExpectedSessionVersion = _sessionState.SessionVersion,
            },
            CancellationToken.None).ConfigureAwait(false);
    }

    // Fires from the WebSocket receive loop (asr.transcript), not the UI
    // thread — every mutation below is marshaled.
    private void OnTranscriptReceived(object? sender, TranscriptReceivedEventArgs e)
    {
        if (!e.IsFinal)
        {
            // Interim transcript: live captioning only. Must never trigger
            // navigation or be submitted as a turn. An interim "next"
            // followed by a final "next question" must therefore not
            // navigate on the interim.
            _dispatcher.Invoke(() =>
            {
                InterimTranscript = e.Text;
                VoiceTranscript = e.Text;
            });
            return;
        }

        // A provider that redelivers the same final result must not create
        // a second turn (client.md: "Deduplicate repeated final events
        // according to protocol identity").
        lock (_submittedTranscriptIds)
        {
            if (!_submittedTranscriptIds.Add(e.TranscriptId))
            {
                return;
            }
        }

        _dispatcher.Invoke(() =>
        {
            InterimTranscript = string.Empty;
            VoiceTranscript = $"Heard: {e.Text}";
            // Read back what was heard so a misrecognition can be caught.
            StatusMessage = $"Heard: {e.Text}";
        });
        FireAndForget(() => SubmitAsync(e.Text, Protocol.Dto.InputMode.Voice), "send your question");
    }

    private void OnVoiceStatusChanged(object? sender, VoiceInputStatus status)
    {
        _dispatcher.Invoke(() =>
        {
            VoiceStatus = status.Message;
            if (status.State is VoiceInputState.Listening or VoiceInputState.Unavailable or VoiceInputState.Failed or VoiceInputState.Off)
            {
                InterimTranscript = string.Empty;
                VoiceTranscript = string.Empty;
            }

            if (status.Announce)
            {
                StatusMessage = status.Message;
            }
        });
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
        if (_staleRequests.Contains(envelope.RequestId)) return;
        var segment = MessageParser.ParseResponseSegment(envelope);
        _timeline?.LinkGeneration(envelope.RequestId.ToString(), segment.GenerationId);
        _timeline?.Record(PlaybackMilestone.SegmentTextReceived, segment.GenerationId, segment.SegmentId);

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
                // Registered before its audio can complete: M1 sends a
                // segment's text before its frames, and the queue refuses
                // audio it cannot acknowledge by this sentence id.
                _playbackQueue?.RegisterSegment(segment.GenerationId, segment.SegmentId, segment.SentenceId);
                var isReading = segment.Kind is null
                    && _sessionState.ActiveSourceVersionId is not null
                    && segment.SegmentId == _sessionState.CurrentBlockId;
                Transcript.Add(new TranscriptLine { Speaker = isReading ? "Source" : "Netra", Text = segment.Text });
                if (isReading)
                {
                    SourceReadingReceived?.Invoke(this, segment);
                }
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
            // Initialize() is the right primitive here (SnapshotReconciler),
            // shared with the HTTP source-selection path.
            if (!SnapshotReconciler.ApplyUnlessOlder(_sessionState, snapshot))
            {
                if (_staleRequests.Add(envelope.RequestId)) _staleRequestOrder.Enqueue(envelope.RequestId);
                while (_staleRequestOrder.Count > 128) _staleRequests.Remove(_staleRequestOrder.Dequeue());
                return;
            }
            if (_namedSourceVersionId != snapshot.ActiveSourceVersionId)
            {
                _namedSourceVersionId = snapshot.ActiveSourceVersionId;
                ActiveSourceSummary = snapshot.ActiveSourceVersionId is null
                    ? "Open a source from Library to ask about your material."
                    : "A source is open. Ask about it here, or choose another in Library.";
            }
            SessionSnapshotReceived?.Invoke(this, snapshot);

            // Snapshots also answer navigation; only the one answering a
            // resume restores anything, and only a place worth telling about
            // is announced (D-open-5). The connection status says "Connected."
            if (envelope.RequestId == _connectionManager.LastResumeRequestId)
            {
                var restored = RestoredPlace(snapshot);
                if (restored is not null)
                {
                    StatusMessage = restored;
                }
            }
        });
    }

    private static string? RestoredPlace(SessionSnapshotPayload snapshot) => snapshot switch
    {
        { PendingQuestion: not null } => "Your place is restored. A question is waiting for your answer.",
        { InteractionMode: SessionInteractionMode.Reading } => "Your place in the reading is restored.",
        { InteractionMode: SessionInteractionMode.TutorLesson or SessionInteractionMode.Quiz } => "Your place in the lesson is restored.",
        _ => null,
    };

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
        // Voice input reports its own errors in its own words.
        if (_speechInputService is IVoiceRequestOwner voice && voice.OwnsRequest(envelope.RequestId))
        {
            return;
        }

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

    // Signing out on a shared computer: the next student must not find the
    // previous one's conversation on screen.
    public void ClearForSignOut() => _dispatcher.Invoke(() =>
    {
        _speechInputService.AbortListening();
        Transcript.Clear();
        VoiceTranscript = string.Empty;
        InputText = string.Empty;
        InterimTranscript = string.Empty;
        _lastAdmittedGenerationId = null;
        _namedSourceVersionId = null;
        ActiveSourceSummary = "Open a source from Library to ask about your material.";
        _staleRequests.Clear();
        _staleRequestOrder.Clear();
    });

    // Accessible status from services that are not view models (playback
    // queue, push-to-talk). Marshaled: callers may be on any thread.
    public void ReportStatus(string message) => _dispatcher.Invoke(() => StatusMessage = message);

    // Never lets an async command crash the app, and never fails silently:
    // a student who cannot see the screen must hear that nothing happened.
    private async void FireAndForget(Func<Task> operation, string action)
    {
        try
        {
            await operation().ConfigureAwait(false);
        }
        catch (Exception ex)
        {
            ReportStatus(FailureText.Describe(ex, action, CancellationToken.None));
        }
    }

    public void Dispose()
    {
        _connectionManager.MessageReceived -= OnServerMessageReceived;
        _speechInputService.TranscriptReceived -= OnTranscriptReceived;
        _speechInputService.StatusChanged -= OnVoiceStatusChanged;
    }
}
