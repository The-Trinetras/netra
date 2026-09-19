using System.ComponentModel;
using System.Windows;
using Netra.Desktop.Accessibility;
using Netra.Desktop.ViewModels;

namespace Netra.Desktop.Views;

public partial class SignInWindow : Window
{
    private readonly SignInViewModel _viewModel;
    private readonly ILiveRegionAnnouncer _announcer;

    public SignInWindow(SignInViewModel viewModel, ILiveRegionAnnouncer announcer)
    {
        InitializeComponent();
        _viewModel = viewModel;
        _announcer = announcer;
        DataContext = viewModel;
        viewModel.PropertyChanged += OnViewModelPropertyChanged;
        viewModel.SignedIn += OnSignedIn;
        Closed += (_, _) =>
        {
            viewModel.PropertyChanged -= OnViewModelPropertyChanged;
            viewModel.SignedIn -= OnSignedIn;
        };
    }

    private void OnViewModelPropertyChanged(object? sender, PropertyChangedEventArgs e)
    {
        if (e.PropertyName == nameof(SignInViewModel.StatusMessage) && _viewModel.StatusMessage.Length > 0)
        {
            _announcer.Announce(SignInStatusRegion, _viewModel.StatusMessage);
        }

        // After a refusal, put the student back on the code, selected, so a
        // typing mistake can be read back and corrected.
        if (e.PropertyName == nameof(SignInViewModel.IsBusy) && !_viewModel.IsBusy && _viewModel.AccessCode.Length > 0)
        {
            AccessCodeBox.Focus();
            AccessCodeBox.SelectAll();
        }
    }

    // The main window announces the outcome: this dialog closes at once.
    private void OnSignedIn(object? sender, EventArgs e) => DialogResult = true;
}
