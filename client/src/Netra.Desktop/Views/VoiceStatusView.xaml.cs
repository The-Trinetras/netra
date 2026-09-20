using System.ComponentModel;
using System.Windows;
using System.Windows.Controls;
using Netra.Desktop.Accessibility;
using Netra.Desktop.ViewModels;

namespace Netra.Desktop.Views;

// The Conversation tab has its own transcript/status. Other tabs share this
// panel so releasing F9 never leaves the recognition result on a hidden tab.
public partial class VoiceStatusView : UserControl
{
    private readonly ILiveRegionAnnouncer _announcer;
    private ConversationViewModel? _viewModel;

    public VoiceStatusView() : this(new LiveRegionAnnouncer()) { }

    public VoiceStatusView(ILiveRegionAnnouncer announcer)
    {
        InitializeComponent();
        _announcer = announcer;
        DataContextChanged += OnDataContextChanged;
    }

    private void OnDataContextChanged(object sender, DependencyPropertyChangedEventArgs e)
    {
        if (_viewModel is not null)
            _viewModel.PropertyChanged -= OnViewModelPropertyChanged;
        _viewModel = e.NewValue as ConversationViewModel;
        if (_viewModel is not null)
            _viewModel.PropertyChanged += OnViewModelPropertyChanged;
    }

    private void OnViewModelPropertyChanged(object? sender, PropertyChangedEventArgs e)
    {
        // Captions and capture progress stay silent while the mic is open.
        // Only the visible status region announces a final result or failure.
        if (IsVisible && e.PropertyName == nameof(ConversationViewModel.StatusMessage)
            && _viewModel is { StatusMessage.Length: > 0 } viewModel)
            _announcer.Announce(StatusRegion, viewModel.StatusMessage);
    }
}
