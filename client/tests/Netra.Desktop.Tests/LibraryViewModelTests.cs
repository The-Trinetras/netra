using System.Threading;
using Netra.Desktop.Library;
using Netra.Desktop.ViewModels;
using Xunit;

namespace Netra.Desktop.Tests;

public sealed class LibraryViewModelTests
{
    // client.md: "A cancelled selection uploads nothing." OnFileSelectedAsync
    // receiving null (what the view passes when OpenFileDialog.ShowDialog()
    // is not exactly true) must not create a library entry or invoke source
    // preparation.
    [Fact]
    public async Task OnFileSelectedAsync_CancelledSelection_AddsNothingAndPreparesNothing()
    {
        var preparationService = new RecordingSourcePreparationService();
        var viewModel = new LibraryViewModel(preparationService, new FixtureVideoDiscoveryService());

        await viewModel.OnFileSelectedAsync(null, CancellationToken.None);

        Assert.Empty(viewModel.Entries);
        Assert.Equal(0, preparationService.PrepareCallCount);
        Assert.Contains("cancelled", viewModel.StatusMessage, StringComparison.OrdinalIgnoreCase);
    }

    [WindowsOnlyFact]
    public async Task OnFileSelectedAsync_SelectedFile_AddsEntryAndReachesReadyThroughFixture()
    {
        var preparationService = new RecordingSourcePreparationService();
        var viewModel = new LibraryViewModel(preparationService, new FixtureVideoDiscoveryService());

        await viewModel.OnFileSelectedAsync(@"C:\study\ohm-v1.pdf", CancellationToken.None);

        var entry = Assert.Single(viewModel.Entries);
        Assert.Equal("ohm-v1.pdf", entry.FileName);
        Assert.Equal(LibrarySourceStatus.Ready, entry.Status);
        Assert.True(entry.IsFixtureSourced);
        Assert.Equal(1, preparationService.PrepareCallCount);
    }

    // client.md: "present numbered results and retain the exact selected
    // result."
    [Fact]
    public async Task SelectVideoResult_RetainsExactSelectedResult()
    {
        var viewModel = new LibraryViewModel(new RecordingSourcePreparationService(), new FixtureVideoDiscoveryService());
        viewModel.VideoSearchQuery = "ohm's law lecture";

        await viewModel.SearchVideosAsync();

        Assert.NotEmpty(viewModel.VideoResults);
        var secondResult = viewModel.VideoResults[1];

        viewModel.SelectVideoResultCommand.Execute(secondResult);

        Assert.Same(secondResult, viewModel.SelectedVideoResult);
    }

    private sealed class RecordingSourcePreparationService : ISourcePreparationService
    {
        public int PrepareCallCount { get; private set; }

        public Task<LibrarySourceStatus> PrepareAsync(LibraryEntry entry, CancellationToken cancellationToken)
        {
            PrepareCallCount++;
            entry.IsFixtureSourced = true;
            return Task.FromResult(LibrarySourceStatus.Ready);
        }
    }
}
