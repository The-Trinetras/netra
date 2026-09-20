using Netra.Desktop.Study;
using Netra.Desktop.ViewModels;
using Netra.Desktop.Library;
using Netra.Desktop.Protocol.Dto;
using Xunit;

namespace Netra.Desktop.Tests;

public sealed class StudyViewModelTests
{
    [Fact]
    public void LiveStudyShowsDeliveredSourceTextAndClearsItOnSourceChangeAndSignOut()
    {
        var viewModel = new StudyViewModel(isLive: true);
        Assert.Empty(viewModel.DetectedObjects);
        Assert.Empty(viewModel.ReadingLines);
        viewModel.OpenSource(new CatalogSource("source", "Operating systems", "version", 1));
        var segment = new ResponseSegmentPayload
        {
            GenerationId = "generation", SegmentId = "block", SentenceId = "sentence",
            Text = "The kernel manages system resources.", Final = true,
        };
        viewModel.ShowReading(segment);
        viewModel.ShowReading(segment);
        Assert.Equal(segment.Text, Assert.Single(viewModel.ReadingLines));
        Assert.Equal("Operating systems", viewModel.SourceTitle);

        viewModel.OpenSource(new CatalogSource("other", "Four kitchens", "other-version", 1));
        Assert.Empty(viewModel.ReadingLines);
        viewModel.ClearForSignOut();
        Assert.Equal("Study", viewModel.SourceTitle);
        viewModel.ShowReading(segment);
        Assert.Empty(viewModel.ReadingLines);
    }

    // client.md: "preserve exact return to reading." Exploring an object
    // and returning must land on exactly the same reading position that was
    // current before exploring — never advanced, re-derived or approximated.
    [Fact]
    public void ExploreObject_ThenReturn_RestoresExactPriorFocus()
    {
        var viewModel = new StudyViewModel();
        var originalPosition = viewModel.ReadingPositionSummary;
        var figure = Assert.Single(viewModel.DetectedObjects, o => o.Kind == DetectedObjectKind.Figure);

        viewModel.ExploreCommand.Execute(figure);

        Assert.True(viewModel.IsExploring);
        Assert.Same(figure, viewModel.FocusedObject);
        Assert.Contains(figure.AccessibleSummary, viewModel.StatusMessage);

        viewModel.ReturnToReadingCommand.Execute(null);

        Assert.False(viewModel.IsExploring);
        Assert.Null(viewModel.FocusedObject);
        Assert.Equal(originalPosition, viewModel.ReadingPositionSummary);
        Assert.Contains(originalPosition, viewModel.StatusMessage);
    }

    [Fact]
    public void ReturnToReadingCommand_CannotExecuteWhileNotExploring()
    {
        var viewModel = new StudyViewModel();

        Assert.False(viewModel.ReturnToReadingCommand.CanExecute(null));
    }

    // client.md: "Do not regenerate authoritative figure labels or equation
    // structure in the client." The fixture summaries must describe actual
    // structure (axes, headers, grouping), not a placeholder.
    [Fact]
    public void DetectedObjects_IncludeAxesHeadersAndEquationStructure()
    {
        var viewModel = new StudyViewModel();

        var figure = Assert.Single(viewModel.DetectedObjects, o => o.Kind == DetectedObjectKind.Figure);
        var table = Assert.Single(viewModel.DetectedObjects, o => o.Kind == DetectedObjectKind.Table);
        var equation = Assert.Single(viewModel.DetectedObjects, o => o.Kind == DetectedObjectKind.Equation);

        Assert.Contains("x axis", figure.AccessibleSummary);
        Assert.Contains("y axis", figure.AccessibleSummary);
        Assert.Contains("Current", table.AccessibleSummary);
        Assert.Contains("Voltage", table.AccessibleSummary);
        Assert.Equal("V equals I times R.", equation.AccessibleSummary);
    }
}
