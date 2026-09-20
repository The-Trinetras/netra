using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Input;
using Netra.Desktop.Accessibility;
using Netra.Desktop.Networking;
using Netra.Desktop.ViewModels;
using Netra.Desktop.Views;
using Xunit;

namespace Netra.Desktop.Tests.Views;

// The real sign-in dialog (compiled XAML): a keyboard-only student lands in
// the code box, Enter signs in, Escape cancels, and a refusal puts them back
// on their code, selected, to find the mistake. Binding and focus evidence
// only; what NVDA reads still needs a Windows/NVDA session.
public sealed class SignInWindowTests
{
    [Fact]
    public void FocusStartsInTheNamedCodeBoxWithEnterAndEscapeWired()
    {
        WpfTestHost.RunOnSta(() =>
        {
            var window = new SignInWindow(
                new SignInViewModel(new RefusingExchange(), new AccessCodeSignInTests.MemoryStore()), new LiveRegionAnnouncer())
            {
                Left = -10000,
                Top = -10000,
                ShowActivated = true,
            };
            window.Show();
            WpfTestHost.Pump();
            try
            {
                var box = (TextBox)window.FindName("AccessCodeBox");
                Assert.Same(box, FocusManager.GetFocusedElement(window));
                Assert.Equal("Access code", AutomationProperties.GetName(box));
                Assert.Same(window.FindName("AccessCodeLabel"), AutomationProperties.GetLabeledBy(box));
                Assert.Equal(SignInViewModel.FirstRunIntro, AutomationProperties.GetHelpText(box));
                Assert.True(((Button)window.FindName("SignInButton")).IsDefault);
                Assert.True(((Button)window.FindName("CancelButton")).IsCancel);
                Assert.Equal("Sign in to Netra", window.Title);
            }
            finally
            {
                window.Close();
            }
        });
    }

    [Fact]
    public void ARefusalReturnsFocusToTheSelectedCode()
    {
        WpfTestHost.RunOnSta(() =>
        {
            var viewModel = new SignInViewModel(new RefusingExchange(), new AccessCodeSignInTests.MemoryStore())
            {
                AccessCode = "PLUM-4821",
            };
            var window = new SignInWindow(viewModel, new LiveRegionAnnouncer()) { Left = -10000, Top = -10000 };
            window.Show();
            WpfTestHost.Pump();
            try
            {
                ((Button)window.FindName("CancelButton")).Focus();
                viewModel.SignInAsync(CancellationToken.None).GetAwaiter().GetResult();
                WpfTestHost.Pump();

                var box = (TextBox)window.FindName("AccessCodeBox");
                Assert.True(box.IsKeyboardFocused);
                Assert.Equal("PLUM-4821", box.SelectedText);
                Assert.StartsWith("That access code was not accepted.", ((TextBlock)window.FindName("SignInStatusRegion")).Text);
            }
            finally
            {
                window.Close();
            }
        });
    }

    private sealed class RefusingExchange : IAccessCodeExchange
    {
        public Task<DeviceCredential> ExchangeAsync(Guid requestId, string accessCode, CancellationToken cancellationToken) =>
            Task.FromException<DeviceCredential>(new ApiErrorException(403, null));
    }
}
