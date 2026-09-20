using Netra.Desktop.Audio;
using Netra.Desktop.Protocol.Dto;
using Netra.Desktop.Speech;
using Netra.Desktop.Video;

namespace Netra.Desktop.Accessibility;

// Push-to-talk / press-to-interrupt, scoped to the app window (see
// GlobalHotKeyService for why this is not a system-wide hook). A view's
// code-behind wires PreviewKeyDown/PreviewKeyUp for the configured key to
// OnKeyDown/OnKeyUp, and window deactivation to OnFocusLost; this class
// contains no WPF Window/UIElement dependency so it is unit-testable
// without a live window.
//
// client.md: "Support push-to-talk, press-to-interrupt and immediate local
// STOP... Do not silently capture or retain microphone audio outside the
// selected mode." Capture starts only on key-down and ends on key-up (send)
// or focus loss (discard) — there is no timer-based or VAD-based
// continuation. Whether voice actually works is reported by the speech
// input service itself, once the server has accepted or refused a capture.
public sealed class PushToTalkController
{
    private readonly IPlaybackController _playbackController;
    private readonly InterruptionController _interruptionController;
    private readonly ISpeechInputService _speechInputService;
    private readonly ILecturePause? _lecture;

    // Guards against a key-repeat WM_KEYDOWN storm (held keys generate
    // repeated events) re-triggering interrupt/StartListening every few
    // milliseconds while already listening.
    private bool _isHeld;

    public PushToTalkController(
        IPlaybackController playbackController,
        InterruptionController interruptionController,
        ISpeechInputService speechInputService,
        ILecturePause? lecture = null)
    {
        _playbackController = playbackController;
        _interruptionController = interruptionController;
        _speechInputService = speechInputService;
        _lecture = lecture;
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
        // speaking. user_stop is the reviewed cancel reason for it (M1/M5,
        // handoff Gap 6). Local silence happens inside StopAsync before any
        // network call; if the cancel then cannot be sent (disconnected),
        // the generation is already fenced locally and the student can
        // still speak.
        if (_playbackController.CurrentSnapshot.Status is State.PlaybackStatus.Playing or State.PlaybackStatus.Loading)
        {
            try
            {
                await _interruptionController.StopAsync(CancelReason.UserStop, cancellationToken).ConfigureAwait(false);
            }
            catch (Exception) when (!cancellationToken.IsCancellationRequested)
            {
            }
        }

        // A playing lecture is paused before the microphone opens (the pause
        // command is sent synchronously), so its sound is not recorded and
        // the question is about where it stopped. Its time report is awaited
        // only after capture has started, so no first words are lost.
        var lecturePause = _lecture?.PauseForQuestionAsync(cancellationToken);

        // The key may already be up again (a quick tap while the cancel was
        // being sent); then there is nothing to capture.
        if (_isHeld)
        {
            await _speechInputService.StartListeningAsync(cancellationToken).ConfigureAwait(false);
        }

        if (lecturePause is not null)
        {
            await lecturePause.ConfigureAwait(false);
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

    // Netra lost focus while the key was held: the key-up will go to another
    // window, so the capture would otherwise run on. Discard it rather than
    // send speech the student may not have meant for Netra.
    public void OnFocusLost()
    {
        if (!_isHeld)
        {
            return;
        }

        _isHeld = false;
        _speechInputService.AbortListening();
    }
}
