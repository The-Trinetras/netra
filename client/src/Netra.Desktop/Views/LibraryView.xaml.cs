using System.Threading;
using System.Windows.Controls;
using Microsoft.Win32;
using Netra.Desktop.ViewModels;

namespace Netra.Desktop.Views;

public partial class LibraryView : UserControl
{
    public LibraryView()
    {
        InitializeComponent();
    }

    // Microsoft.Win32.OpenFileDialog is the standard accessible Windows
    // file picker — NVDA/Narrator announce it natively, with no custom
    // Automation Peer required (client.md: "Open PDF/video through a
    // screen-reader-accessible file picker"). ShowDialog() returning
    // anything other than true (including null, e.g. Escape or the Cancel
    // button) is treated uniformly as "cancelled": the view model must see
    // null and upload nothing.
    private async void OnSelectFileClick(object sender, System.Windows.RoutedEventArgs e)
    {
        if (DataContext is not LibraryViewModel viewModel)
        {
            return;
        }

        var dialog = new OpenFileDialog
        {
            Title = "Select a PDF or uploaded lecture",
            Filter = "Supported study material (*.pdf;*.mp4;*.mp3;*.wav)|*.pdf;*.mp4;*.mp3;*.wav|All files (*.*)|*.*",
            CheckFileExists = true,
        };

        var selected = dialog.ShowDialog() == true ? dialog.FileName : null;
        await viewModel.OnFileSelectedAsync(selected, CancellationToken.None).ConfigureAwait(true);
    }
}
