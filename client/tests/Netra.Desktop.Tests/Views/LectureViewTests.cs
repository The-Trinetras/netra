using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Input;
using Microsoft.Web.WebView2.Wpf;
using Netra.Desktop.Accessibility;
using Netra.Desktop.ViewModels;
using Netra.Desktop.Views;
using Xunit;

namespace Netra.Desktop.Tests.Views;

// The real Lecture view (compiled XAML): letter keys operate the player from
// WPF, typing a link is never taken over by them, and the web view stays out
// of the Tab order so focus never lands inside the YouTube page.
public sealed class LectureViewTests
{
    [Fact]
    public void LetterKeysDriveThePlayerButNotWhileTypingALink()
    {
        WpfTestHost.RunOnSta(() =>
        {
            var (player, page) = LecturePlayerControllerTests.ReadyAsync().GetAwaiter().GetResult();
            var viewModel = new LectureViewModel(player);
            var view = new LectureView(new LiveRegionAnnouncer()) { DataContext = viewModel };
            var window = WpfTestHost.Show(view);
            try
            {
                var playPause = (Button)view.FindName("PlayPauseButton");
                playPause.Focus();
                Press(playPause, Key.K);
                Press(playPause, Key.J);
                Assert.Single(page.Posted("play"));
                Assert.Single(page.Posted("seekBy"));

                var link = (TextBox)view.FindName("LinkBox");
                link.Focus();
                Press(link, Key.K);
                Assert.Single(page.Posted("play"));

                var video = (WebView2)view.FindName("PlayerView");
                Assert.False(KeyboardNavigation.GetIsTabStop(video));
                Assert.False(video.Focusable);
                Assert.Equal("Play or pause", AutomationProperties.GetName(playPause));
                Assert.Equal("K", AutomationProperties.GetAcceleratorKey(playPause));
                Assert.Equal("Playback readiness", AutomationProperties.GetName((TextBlock)view.FindName("PlaybackLine")));
                Assert.Equal("Analysis readiness", AutomationProperties.GetName((TextBlock)view.FindName("AnalysisLine")));
                Assert.Equal(AutomationLiveSetting.Polite, AutomationProperties.GetLiveSetting((TextBlock)view.FindName("LectureStatusRegion")));
            }
            finally
            {
                window.Close();
            }
        });
    }

    private static void Press(UIElement target, Key key)
    {
        var source = PresentationSource.FromVisual(target)!;
        target.RaiseEvent(new KeyEventArgs(Keyboard.PrimaryDevice, source, 0, key) { RoutedEvent = Keyboard.PreviewKeyDownEvent });
    }
}
