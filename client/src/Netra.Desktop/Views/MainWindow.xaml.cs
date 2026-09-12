using System.ComponentModel;
using System.Windows;
using System.Windows.Input;
using Netra.Desktop.Accessibility;
using Netra.Desktop.Protocol.Dto;
using Netra.Desktop.ViewModels;

namespace Netra.Desktop.Views;

public partial class MainWindow : Window
{
    private readonly ILiveRegionAnnouncer _liveRegionAnnouncer;

    public MainWindow(MainViewModel viewModel, ILiveRegionAnnouncer liveRegionAnnouncer)
    {
        InitializeComponent();

        _liveRegionAnnouncer = liveRegionAnnouncer;
        DataContext = viewModel;
        viewModel.PropertyChanged += OnViewModelPropertyChanged;
    }

    private void OnViewModelPropertyChanged(object? sender, PropertyChangedEventArgs e)
    {
        var viewModel = (MainViewModel)DataContext;
        switch (e.PropertyName)
        {
            case nameof(MainViewModel.StatusMessage):
                _liveRegionAnnouncer.Announce(StatusRegion, viewModel.StatusMessage);
                break;

            case nameof(MainViewModel.InterimTranscript):
                _liveRegionAnnouncer.Announce(InterimRegion, viewModel.InterimTranscript);
                break;
        }
    }

    private void OnStopCommandExecuted(object sender, ExecutedRoutedEventArgs e) =>
        ((MainViewModel)DataContext).StopCommand.Execute(null);

    private void OnRepeatCommandExecuted(object sender, ExecutedRoutedEventArgs e) =>
        ((MainViewModel)DataContext).NavigationCommandRequest.Execute(NavigationCommandType.Repeat);

    private void OnNextCommandExecuted(object sender, ExecutedRoutedEventArgs e) =>
        ((MainViewModel)DataContext).NavigationCommandRequest.Execute(NavigationCommandType.Next);

    private void OnPreviousCommandExecuted(object sender, ExecutedRoutedEventArgs e) =>
        ((MainViewModel)DataContext).NavigationCommandRequest.Execute(NavigationCommandType.Previous);
}
