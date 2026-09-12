using System.Collections.ObjectModel;
using System.Threading;
using System.Windows.Input;
using Netra.Desktop.Audio;
using Netra.Desktop.Networking;
using Netra.Desktop.Protocol;
using Netra.Desktop.Protocol.Dto;
using Netra.Desktop.Speech;
using Netra.Desktop.State;

namespace Netra.Desktop.ViewModels;

public sealed class TranscriptLine
{
    public required string Speaker { get; init; }
    public required string Text { get; init; }
}

public sealed class MainViewModel : ViewModelBase, IDisposable
{
    private readonly ClientSessionState _sessionState;
    private readonly ConnectionManager _connectionManager;
    private readonly InterruptionController _interruptionController;
    private readonly ISpeechInputService _speechInputService;

    // Final transcripts already turned into a turn, by their stable
    // TranscriptId. Recognition providers redeliver results on reconnect
    // and retry, and each send would otherwise get a fresh request_id, so
    // the server could not tell a redelivery from a second utterance.
    private readonly HashSet<Guid> _submittedTranscriptIds = new();

    private string _inputText = string.Empty;
    private string _interimTranscript = string.Empty;
    private string _statusMessage = string.Empty;

    public MainViewModel(
        ClientSessionState sessionState,
        ConnectionManager connectionManager,
        IPlaybackController playbackController,
        InterruptionController interruptionController,
        ISpeechInputService speechInputService)
    {
        _sessionState = sessionState;
        _connectionManager = connectionManager;
        _interruptionController = interruptionController;
        _speechInputService = speechInputService;

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

    private void OnTranscriptReceived(object? sender, TranscriptReceivedEventArgs e)
    {
        if (!e.IsFinal)
        {
            // Interim transcript: live captioning only. Must never trigger
            // navigation or be submitted as a turn. An interim "next"
            // followed by a final "next question" must therefore not
            // navigate on the interim.
            InterimTranscript = e.Text;
            return;
        }

        // A provider that redelivers the same final result must not create
        // a second turn (client.md: "Deduplicate repeated final events
        // according to protocol identity").
        if (!_submittedTranscriptIds.Add(e.TranscriptId))
        {
            return;
        }

        InterimTranscript = string.Empty;
        InputText = e.Text;
        FireAndForget(() => SubmitAsync(Protocol.Dto.InputMode.Voice));
    }

    private void OnServerMessageReceived(object? sender, ServerToClientEnvelope envelope)
    {
        switch (envelope.Type)
        {
            case ServerMessageType.ResponseSegment:
                var segment = MessageParser.ParseResponseSegment(envelope);
                _sessionState.CurrentGenerationId = segment.GenerationId;

                if (!_interruptionController.IsCancelled(segment.GenerationId))
                {
                    Transcript.Add(new TranscriptLine { Speaker = "Tutor", Text = segment.Text });
                }

                break;

            case ServerMessageType.SessionSnapshot:
            case ServerMessageType.QuizQuestion:
            case ServerMessageType.Error:
                // TODO: handle once these payload contracts are typed (see
                // Protocol/Dto/ServerPayloads.cs).
                break;
        }
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
