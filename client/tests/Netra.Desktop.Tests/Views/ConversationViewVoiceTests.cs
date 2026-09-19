using System.Windows.Automation;
using System.Windows.Controls;
using Netra.Desktop.Accessibility;
using Netra.Desktop.Audio;
using Netra.Desktop.Networking;
using Netra.Desktop.Speech;
using Netra.Desktop.State;
using Netra.Desktop.Threading;
using Netra.Desktop.ViewModels;
using Netra.Desktop.Views;
using Xunit;

namespace Netra.Desktop.Tests.Views;

// The real ConversationView (compiled XAML): captions and voice status are
// shown but never pushed to the screen reader, because they change while the
// microphone is open; announced statuses still reach the live region.
public sealed class ConversationViewVoiceTests
{
    [Fact]
    public void CaptionsAndVoiceStatusAreShownButNeverAnnounced()
    {
        WpfTestHost.RunOnSta(() =>
        {
            var speech = new ScriptedSpeechInput();
            var announcer = new RecordingAnnouncer();
            var view = new ConversationView(BuildViewModel(speech), announcer);
            var window = WpfTestHost.Show(view);
            try
            {
                speech.Status(new VoiceInputStatus(VoiceInputState.Listening, "Listening.", Announce: false));
                speech.Interim("what is ohm");
                WpfTestHost.Pump();

                Assert.Empty(announcer.Messages);
                Assert.Equal(AutomationLiveSetting.Off, AutomationProperties.GetLiveSetting((TextBlock)view.FindName("InterimRegion")));
                Assert.Equal(AutomationLiveSetting.Off, AutomationProperties.GetLiveSetting((TextBlock)view.FindName("VoiceStatusText")));
                Assert.Equal("Voice input status", AutomationProperties.GetName((TextBlock)view.FindName("VoiceStatusText")));

                speech.Status(new VoiceInputStatus(VoiceInputState.Unavailable, "Voice input needs a connection to Netra. Type your question instead.", Announce: true));
                WpfTestHost.Pump();

                Assert.Equal(new[] { "Voice input needs a connection to Netra. Type your question instead." }, announcer.Messages);
            }
            finally
            {
                window.Close();
            }
        });
    }

    private static ConversationViewModel BuildViewModel(ISpeechInputService speech)
    {
        var state = new ClientSessionState();
        state.Initialize(Guid.NewGuid(), 1);
        var connection = new ConnectionManager(new CapturingSocket(), state);
        var player = new ScriptedPlayer();
        var interruption = new InterruptionController(player, connection);
        return new ConversationViewModel(
            state, connection, player, interruption, new BinaryAudioFrameProcessor(interruption), speech, new SynchronousUiDispatcher());
    }

    private sealed class RecordingAnnouncer : ILiveRegionAnnouncer
    {
        public List<string> Messages { get; } = new();

        public void Announce(TextBlock liveRegionHost, string message, AutomationLiveSetting politeness = AutomationLiveSetting.Polite) =>
            Messages.Add(message);
    }

    private sealed class ScriptedSpeechInput : ISpeechInputService
    {
        public bool IsListening => false;

        public event EventHandler<TranscriptReceivedEventArgs>? TranscriptReceived;
        public event EventHandler<VoiceInputStatus>? StatusChanged;

        public void Status(VoiceInputStatus status) => StatusChanged?.Invoke(this, status);

        public void Interim(string text) =>
            TranscriptReceived?.Invoke(this, new TranscriptReceivedEventArgs { Text = text, IsFinal = false, TranscriptId = Guid.NewGuid() });

        public Task StartListeningAsync(CancellationToken cancellationToken) => Task.CompletedTask;

        public void StopListening()
        {
        }

        public void AbortListening()
        {
        }

        public void Dispose()
        {
        }
    }
}
