using System.IO;
using System.Windows;
using System.Windows.Controls;
using Microsoft.Win32;
using Netra.Desktop.ViewModels;

namespace Netra.Desktop.Views;

public partial class PreferencesView : UserControl
{
    public PreferencesView()
    {
        InitializeComponent();
    }

    // The standard accessible Windows save dialog; cancelling writes nothing.
    private void OnSaveMeasurementsClick(object sender, RoutedEventArgs e)
    {
        if (DataContext is not PreferencesViewModel viewModel)
        {
            return;
        }

        var dialog = new SaveFileDialog
        {
            Title = "Save playback measurements",
            Filter = "JSON measurement file (*.json)|*.json",
            FileName = $"netra-playback-{DateTime.Now:yyyyMMdd-HHmmss}.json",
            AddExtension = true,
        };

        if (dialog.ShowDialog(Window.GetWindow(this)) != true)
        {
            return;
        }

        var json = viewModel.ExportMeasurements("Exported from the Preferences view.");
        if (json is null)
        {
            return;
        }

        try
        {
            File.WriteAllText(dialog.FileName, json);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            viewModel.ReportMeasurementFailure("The measurements could not be saved to that location.");
        }
    }

    private void OnClearMeasurementsClick(object sender, RoutedEventArgs e)
    {
        if (DataContext is PreferencesViewModel viewModel)
        {
            viewModel.ClearMeasurements();
        }
    }
}
