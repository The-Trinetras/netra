using Netra.Desktop.Audio;
using Netra.Desktop.Protocol.Dto;
using Netra.Desktop.Speech;

namespace Netra.Desktop.Accessibility;

// Push-to-talk / press-to-interrupt, scoped to the app window (see
// GlobalHotKeyService for why this is not a system-wide hook). A view's
// code-behind wires PreviewKeyDown/PreviewKeyUp for the configured key to
// OnKeyDown/OnKeyUp; this class contains no WPF Window/UIElement
// dependency so it is unit-testable without a live window.
//
// client.md: "Support push-to-talk, press-to-interrupt and immediate local
// STOP... Do not silently capture or retain microphone audio outside the
// selected mode." Capture starts only on key-down and ends only on
// key-up — there is no timer-based or VAD-based continuation.
public sealed class PushToTalkController
{
    private readonly IPlaybackController _playbackController;
    private readonly InterruptionController _interruptionController;
    private readonly ISpeechInputService _speechInputService;
    private readonly Action? _onRecognitionUnavailable;

    // Guards against a key-repeat WM_KEYDOWN storm (held keys generate
    // repeated events) re-triggering interrupt/StartListening every few
    // milliseconds while already listening.
    private bool _isHeld;

    public PushToTalkController(
        IPlaybackController playbackController,
        InterruptionController interruptionController,
        ISpeechInputService speechInputService,
        Action? onRecognitionUnavailable = null)
    {
        _playbackController = playbackController;
        _interruptionController = interruptionController;
        _speechInputService = speechInputService;
        _onRecognitionUnavailable = onRecognitionUnavailable;
    }

    public async Task OnKeyDownAsync(CancellationToken cancellationToken)
    {
        if (_isHeld)
        {
            // Key-repeat while already held: capture is already running,
            // do not re-interrupt or restart it.
            return;
        }

        _isHeld = true;

        // Press-to-interrupt: only meaningful if Netra is currently
        // speaking. CancelReason has no dedicated "interrupted by
        // push-to-talk" value (see docs/team/handoffs/M5.md Gap 6); reusing
        // UserStop is a semantic approximation pending M1 review, not a
        // silent contract change (no new enum member is invented on the
        // wire).
        if (_playbackController.CurrentSnapshot.Status is State.PlaybackStatus.Playing or State.PlaybackStatus.Loading)
        {
            await _interruptionController.StopAsync(CancelReason.UserStop, cancellationToken).ConfigureAwait(false);
        }

        await _speechInputService.StartListeningAsync(cancellationToken).ConfigureAwait(false);

        // Press-to-interrupt above is real; recognition is not. Say so once
        // per press rather than letting the student talk to nothing.
        if (_speechInputService is IRecognitionAvailability { IsRecognitionAvailable: false })
        {
            _onRecognitionUnavailable?.Invoke();
        }
    }

    public void OnKeyUp()
    {
        if (!_isHeld)
        {
            return;
        }

        _isHeld = false;
        _speechInputService.StopListening();
    }
}
