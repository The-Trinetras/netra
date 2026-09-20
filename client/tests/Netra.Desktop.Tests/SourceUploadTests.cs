using System.IO;
using System.Net.Http;
using Netra.Desktop.Library;
using Netra.Desktop.Networking;
using Netra.Desktop.Protocol.Dto;
using Netra.Desktop.State;
using Xunit;

namespace Netra.Desktop.Tests;

// The real upload: what reaches the server, what the student is told, and what
// must never be claimed. Replaces the fixture's unconditional "Ready".
public sealed class SourceUploadTests : IDisposable
{
    private readonly string _file = Path.Combine(Path.GetTempPath(), $"netra-upload-{Guid.NewGuid():N}.pdf");

    public SourceUploadTests() => File.WriteAllBytes(_file, "%PDF-1.7 test"u8.ToArray());

    public void Dispose() => File.Delete(_file);

    private LibraryEntry Entry() => new()
    {
        EntryId = Guid.NewGuid(),
        FileName = Path.GetFileName(_file),
        FullPath = _file,
    };

    private static (UploadFakeApi Api, ClientSessionState State) Build()
    {
        var state = new ClientSessionState();
        state.Initialize(Guid.NewGuid(), 1);
        return (new UploadFakeApi(), state);
    }

    private static HttpSourcePreparationService Service(UploadFakeApi api, ClientSessionState state, TimeSpan? timeout = null) =>
        new(api, state, pollInterval: TimeSpan.Zero, timeout: timeout,
            delay: (_, _) => Task.CompletedTask);

    [Fact]
    public async Task AReadyJobMakesTheEntryReadyAndNotAFixtureResult()
    {
        var (api, state) = Build();
        api.Upload = new ApiJob(Guid.NewGuid(), "ready");
        var entry = Entry();

        var status = await Service(api, state).PrepareAsync(entry, CancellationToken.None);

        Assert.Equal(LibrarySourceStatus.Ready, status);
        Assert.False(entry.IsFixtureSourced);
    }

    [Fact]
    public async Task TheUploadCarriesTheSessionTheTitleAndTheFile()
    {
        var (api, state) = Build();
        api.Upload = new ApiJob(Guid.NewGuid(), "ready");
        var entry = Entry();

        await Service(api, state).PrepareAsync(entry, CancellationToken.None);

        var sent = Assert.Single(api.Uploads);
        Assert.Equal(state.SessionId, sent.SessionId);
        Assert.Equal(Path.GetFileNameWithoutExtension(entry.FileName), sent.Title);
        Assert.Equal(entry.FileName, sent.FileName);
        Assert.Equal("%PDF-1.7 test", sent.Content);
    }

    [Fact]
    public async Task ItPollsUntilTheServerLeavesProcessing()
    {
        var (api, state) = Build();
        var jobId = Guid.NewGuid();
        api.Upload = new ApiJob(jobId, "processing", Stage: "queued", PollAfterMs: 1);
        api.Polls.Enqueue(new ApiJob(jobId, "processing", Stage: "reading"));
        api.Polls.Enqueue(new ApiJob(jobId, "processing", Stage: "indexing"));
        api.Polls.Enqueue(new ApiJob(jobId, "ready"));

        var status = await Service(api, state).PrepareAsync(Entry(), CancellationToken.None);

        Assert.Equal(LibrarySourceStatus.Ready, status);
        Assert.Equal(3, api.PollCount);
        Assert.All(api.PolledJobIds, id => Assert.Equal(jobId, id));
    }

    [Fact]
    public async Task AFailedJobIsFailedAndNeverReady()
    {
        var (api, state) = Build();
        var jobId = Guid.NewGuid();
        api.Upload = new ApiJob(jobId, "processing", PollAfterMs: 1);
        api.Polls.Enqueue(new ApiJob(jobId, "failed", Failure: new ApiJobFailure("processing_failed")));

        var status = await Service(api, state).PrepareAsync(Entry(), CancellationToken.None);

        Assert.Equal(LibrarySourceStatus.Failed, status);
    }

    [Fact]
    public async Task ARejectedUploadFailsWithoutClaimingAnythingWasStored()
    {
        var (api, state) = Build();
        api.UploadFailure = new ApiErrorException(413, null);
        var entry = Entry();

        var status = await Service(api, state).PrepareAsync(entry, CancellationToken.None);

        Assert.Equal(LibrarySourceStatus.Failed, status);
        Assert.False(entry.IsFixtureSourced);
        Assert.Empty(api.Polls);
    }

    [Fact]
    public async Task AMissingFileFailsInsteadOfThrowingAtTheStudent()
    {
        var (api, state) = Build();
        var entry = new LibraryEntry
        {
            EntryId = Guid.NewGuid(),
            FileName = "gone.pdf",
            FullPath = Path.Combine(Path.GetTempPath(), $"absent-{Guid.NewGuid():N}.pdf"),
        };

        Assert.Equal(LibrarySourceStatus.Failed, await Service(api, state).PrepareAsync(entry, CancellationToken.None));
        Assert.Empty(api.Uploads);
    }

    [Fact]
    public async Task ATransportFailureIsRetriedOnceUnderTheSameRequestId()
    {
        var (api, state) = Build();
        api.UploadFailures.Enqueue(new HttpRequestException("connection reset"));
        api.Upload = new ApiJob(Guid.NewGuid(), "ready");

        var status = await Service(api, state).PrepareAsync(Entry(), CancellationToken.None);

        Assert.Equal(LibrarySourceStatus.Ready, status);
        Assert.Equal(2, api.Uploads.Count);
        // The replay identity must be identical, or the retry ingests a second copy.
        Assert.Equal(api.Uploads[0].RequestId, api.Uploads[1].RequestId);
    }

    [Fact]
    public async Task RunningOutOfTimeReportsStillProcessingNotFailed()
    {
        var (api, state) = Build();
        api.Upload = new ApiJob(Guid.NewGuid(), "processing", PollAfterMs: 1);
        api.AlwaysProcessing = true;

        var status = await Service(api, state, timeout: TimeSpan.Zero).PrepareAsync(Entry(), CancellationToken.None);

        Assert.Equal(LibrarySourceStatus.Processing, status);
    }

    [Fact]
    public async Task ALostPollReportsStillProcessingBecauseTheUploadWasAccepted()
    {
        var (api, state) = Build();
        api.Upload = new ApiJob(Guid.NewGuid(), "processing", PollAfterMs: 1);
        api.PollFailure = new HttpRequestException("connection reset");

        var status = await Service(api, state).PrepareAsync(Entry(), CancellationToken.None);

        Assert.Equal(LibrarySourceStatus.Processing, status);
    }

    [Fact]
    public void TheEntryAnnouncesItsStatusChange()
    {
        var entry = Entry();
        var seen = new List<string?>();
        entry.PropertyChanged += (_, e) => seen.Add(e.PropertyName);

        entry.Status = LibrarySourceStatus.Processing;
        entry.Status = LibrarySourceStatus.Processing;   // no change, no event
        entry.Status = LibrarySourceStatus.Ready;
        entry.IsFixtureSourced = false;

        Assert.Equal(new[] { "Status", "Status", "IsFixtureSourced" }, seen);
    }

    private sealed record SentUpload(Guid SessionId, Guid RequestId, string Title, string FileName, string Content);

    private sealed class UploadFakeApi : INetraApi
    {
        public List<SentUpload> Uploads { get; } = new();
        public Queue<Exception> UploadFailures { get; } = new();
        public Exception? UploadFailure { get; set; }
        public ApiJob Upload { get; set; } = new(Guid.NewGuid(), "ready");
        public Queue<ApiJob> Polls { get; } = new();
        public List<Guid> PolledJobIds { get; } = new();
        public int PollCount => PolledJobIds.Count;
        public bool AlwaysProcessing { get; set; }
        public Exception? PollFailure { get; set; }

        public Task<ApiSessionCreated> CreateSessionAsync(CancellationToken cancellationToken) =>
            throw new NotSupportedException();

        public Task<IReadOnlyList<ApiSource>> ListSourcesAsync(Guid sessionId, CancellationToken cancellationToken) =>
            throw new NotSupportedException();

        public Task<ApiSourceSelected> SelectSourceAsync(
            Guid sessionId, Guid requestId, string sourceVersionId, long expectedSessionVersion, CancellationToken cancellationToken) =>
            throw new NotSupportedException();

        public async Task<ApiJob> UploadAsync(
            Guid sessionId, Guid requestId, string title, string fileName, Stream content, CancellationToken cancellationToken)
        {
            using var reader = new StreamReader(content);
            var body = await reader.ReadToEndAsync(cancellationToken);
            Uploads.Add(new SentUpload(sessionId, requestId, title, fileName, body));
            if (UploadFailures.TryDequeue(out var transient))
            {
                throw transient;
            }

            return UploadFailure is { } failure ? throw failure : Upload;
        }

        public Task<ApiJob> GetJobAsync(Guid sessionId, Guid jobId, CancellationToken cancellationToken)
        {
            PolledJobIds.Add(jobId);
            if (PollFailure is { } failure)
            {
                return Task.FromException<ApiJob>(failure);
            }

            if (AlwaysProcessing)
            {
                return Task.FromResult(new ApiJob(jobId, "processing", PollAfterMs: 1));
            }

            return Task.FromResult(Polls.Count > 0 ? Polls.Dequeue() : new ApiJob(jobId, "ready"));
        }
    }
}
