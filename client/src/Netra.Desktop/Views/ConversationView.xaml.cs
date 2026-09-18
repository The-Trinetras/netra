using System.ComponentModel;
using System.Windows.Controls;
using Netra.Desktop.Accessibility;
using Netra.Desktop.ViewModels;

namespace Netra.Desktop.Views;

public partial class ConversationView : UserControl
{
    private readonly ILiveRegionAnnouncer _liveRegionAnnouncer;

    public ConversationView(ConversationViewModel viewModel, ILiveRegionAnnouncer liveRegionAnnouncer)
    {
        InitializeComponent();

        _liveRegionAnnouncer = liveRegionAnnouncer;
        DataContext = viewModel;
        viewModel.PropertyChanged += OnViewModelPropertyChanged;
    }

    private void OnViewModelPropertyChanged(object? sender, PropertyChangedEventArgs e)
    {
        var viewModel = (ConversationViewModel)DataContext;
        switch (e.PropertyName)
        {
            case nameof(ConversationViewModel.StatusMessage):
                _liveRegionAnnouncer.Announce(StatusRegion, viewModel.StatusMessage);
                break;

            case nameof(ConversationViewModel.InterimTranscript):
                // Interim captioning must never steal focus or interrupt
                // NVDA's current utterance — a polite live-region update is
                // the correct channel (client.md: "Streaming must not steal
                // focus or cause competing Netra/NVDA speech").
                _liveRegionAnnouncer.Announce(InterimRegion, viewModel.InterimTranscript);
                break;
        }
    }
}
