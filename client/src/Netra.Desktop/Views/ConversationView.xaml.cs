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
                if (IsVisible)
                {
                    _liveRegionAnnouncer.Announce(StatusRegion, viewModel.StatusMessage);
                }
                break;

            // InterimTranscript and VoiceStatus are deliberately not
            // announced: they change while the microphone is open, and NVDA
            // speaking them would be recorded into the question and compete
            // with the student (client.md: no competing Netra/NVDA speech).
        }
    }
}
