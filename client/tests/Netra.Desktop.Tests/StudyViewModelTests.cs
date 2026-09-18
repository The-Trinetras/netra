using Netra.Desktop.Study;
using Netra.Desktop.ViewModels;
using Xunit;

namespace Netra.Desktop.Tests;

public sealed class StudyViewModelTests
{
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
