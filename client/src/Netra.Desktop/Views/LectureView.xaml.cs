using System.ComponentModel;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using Netra.Desktop.Accessibility;
using Netra.Desktop.Video;
using Netra.Desktop.ViewModels;

namespace Netra.Desktop.Views;

public partial class LectureView : UserControl
{
    private readonly ILiveRegionAnnouncer _announcer;
    private LectureViewModel? _viewModel;

    public LectureView(ILiveRegionAnnouncer announcer)
    {
        InitializeComponent();
        _announcer = announcer;
        DataContextChanged += OnDataContextChanged;
        PreviewKeyDown += OnPreviewKeyDown;
    }

    // The player surface lives in this view's WebView2.
    public IPlayerSurface CreatePlayerSurface() => new WebView2PlayerSurface(PlayerView);

    private void OnDataContextChanged(object sender, DependencyPropertyChangedEventArgs e)
    {
        if (_viewModel is not null)
        {
            _viewModel.PropertyChanged -= OnViewModelPropertyChanged;
        }

        _viewModel = e.NewValue as LectureViewModel;
        if (_viewModel is not null)
        {
            _viewModel.PropertyChanged += OnViewModelPropertyChanged;
        }
    }

    private void OnViewModelPropertyChanged(object? sender, PropertyChangedEventArgs e)
    {
        if (e.PropertyName == nameof(LectureViewModel.StatusMessage) && _viewModel is { StatusMessage.Length: > 0 } viewModel)
        {
            _announcer.Announce(LectureStatusRegion, viewModel.StatusMessage);
        }
    }

    // Letter shortcuts anywhere in the tab, except while typing a link.
    private void OnPreviewKeyDown(object sender, KeyEventArgs e)
    {
        if (_viewModel is null || e.OriginalSource is TextBox || Keyboard.Modifiers != ModifierKeys.None)
        {
            return;
        }

        var command = e.Key switch
        {
            Key.K => _viewModel.PlayPauseCommand,
            Key.J => _viewModel.BackCommand,
            Key.L => _viewModel.ForwardCommand,
            Key.T => _viewModel.WhereAmICommand,
            Key.C => _viewModel.ContinueCommand,
            Key.D => _viewModel.DescribeCommand,
            _ => null,
        };
        if (command is null)
        {
            return;
        }

        e.Handled = true;
        command.Execute(null);
    }
}
