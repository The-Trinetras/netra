using System.Windows.Automation;
using System.Windows;
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
    public void VoiceResultAndFailureStayVisibleOutsideConversationWithoutDuplicateAnnouncements()
    {
        WpfTestHost.RunOnSta(() =>
        {
            var speech = new ScriptedSpeechInput();
            var announcer = new RecordingAnnouncer();
            using var viewModel = BuildViewModel(speech);
            var conversation = new ConversationView(viewModel, announcer) { Visibility = Visibility.Collapsed };
            var voice = new VoiceStatusView(announcer) { DataContext = viewModel };
            var panel = new StackPanel();
            panel.Children.Add(conversation);
            panel.Children.Add(voice);
            var window = WpfTestHost.Show(panel);
            try
            {
                speech.Status(new VoiceInputStatus(VoiceInputState.Listening, "Listening.", Announce: false));
                speech.Interim("explain the triangle");
                WpfTestHost.Pump();
                Assert.Equal("explain the triangle", ((TextBlock)voice.FindName("VoiceCaption")).Text);
                Assert.Empty(announcer.Messages);
                Assert.Equal(AutomationLiveSetting.Off, AutomationProperties.GetLiveSetting((TextBlock)voice.FindName("VoiceCaption")));

                speech.Final("Explain the triangle.");
                WpfTestHost.Pump();
                Assert.Equal("Heard: Explain the triangle.", ((TextBlock)voice.FindName("VoiceCaption")).Text);
                Assert.Single(announcer.Messages, "Heard: Explain the triangle.");
                viewModel.ReportStatus("Netra is answering.");
                WpfTestHost.Pump();
                Assert.Equal("Heard: Explain the triangle.", ((TextBlock)voice.FindName("VoiceCaption")).Text);

                const string failure = "No transcript arrived. Try again, or type your question.";
                speech.Status(new VoiceInputStatus(VoiceInputState.Failed, failure, Announce: true));
                WpfTestHost.Pump();
                Assert.Equal(failure, ((TextBlock)voice.FindName("StatusRegion")).Text);
                Assert.Empty(((TextBlock)voice.FindName("VoiceCaption")).Text);
                Assert.Single(announcer.Messages, failure);

                voice.Visibility = Visibility.Collapsed;
                conversation.Visibility = Visibility.Visible;
                WpfTestHost.Pump();
                announcer.Messages.Clear();
                viewModel.ReportStatus("Ready to try again.");
                Assert.Equal(new[] { "Ready to try again." }, announcer.Messages);
            }
            finally
            {
                window.Close();
            }
        });
    }

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

        public void Final(string text) =>
            TranscriptReceived?.Invoke(this, new TranscriptReceivedEventArgs { Text = text, IsFinal = true, TranscriptId = Guid.NewGuid() });

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
