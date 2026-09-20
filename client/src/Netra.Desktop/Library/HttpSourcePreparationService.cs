using System.IO;
using System.Net.Http;
using System.Threading;
using Netra.Desktop.Networking;
using Netra.Desktop.State;

namespace Netra.Desktop.Library;

// The real upload: POST the file to the session's uploads route, then poll the
// job it returns until the server says ready or failed (C5 upload/job
// contract, api/src/netra_api/transport/http/uploads.py).
//
// Replaces FixtureSourcePreparationService, which returned Ready after 50 ms
// without contacting anything — a false readiness for a file the server had
// never seen. Every status this returns comes from an authorized server
// response, so entries prepared here are never IsFixtureSourced.
//
// Nothing here blocks the UI thread and nothing retries an upload that may
// already have committed: a transport failure is retried once under the SAME
// request id, which the server replays onto the same source and job rather
// than ingesting the document twice.
public sealed class HttpSourcePreparationService : ISourcePreparationService
{
    // Client-local bounds, not product policy: how long to keep asking, and
    // what to wait when the server does not say. Ingestion of a real lecture
    // PDF took about 90 seconds on a local worker.
    public static readonly TimeSpan DefaultPollInterval = TimeSpan.FromSeconds(2);
    public static readonly TimeSpan DefaultTimeout = TimeSpan.FromMinutes(10);

    private const int MaxTitleChars = 200;

    private readonly INetraApi _api;
    private readonly ClientSessionState _sessionState;
    private readonly TimeSpan _pollInterval;
    private readonly TimeSpan _timeout;
    private readonly Func<TimeSpan, CancellationToken, Task> _delay;
    private readonly Func<DateTimeOffset> _now;

    public HttpSourcePreparationService(
        INetraApi api,
        ClientSessionState sessionState,
        TimeSpan? pollInterval = null,
        TimeSpan? timeout = null,
        Func<TimeSpan, CancellationToken, Task>? delay = null,
        Func<DateTimeOffset>? now = null)
    {
        _api = api;
        _sessionState = sessionState;
        _pollInterval = pollInterval ?? DefaultPollInterval;
        _timeout = timeout ?? DefaultTimeout;
        _delay = delay ?? Task.Delay;
        _now = now ?? (() => DateTimeOffset.UtcNow);
    }

    public async Task<LibrarySourceStatus> PrepareAsync(LibraryEntry entry, CancellationToken cancellationToken)
    {
        // Whatever happens from here is a real server outcome, not a double.
        entry.IsFixtureSourced = false;

        var requestId = Guid.NewGuid();
        ApiJob job;
        try
        {
            job = await UploadAsync(entry, requestId, cancellationToken).ConfigureAwait(false);
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            throw;
        }
        catch (Exception ex) when (ex is HttpRequestException or ApiErrorException or IOException
                                       or UnauthorizedAccessException or CredentialUnavailableException
                                       or TaskCanceledException)
        {
            // The student is told it could not be prepared; the reason stays
            // out of the UI because a server message is untrusted text.
            return LibrarySourceStatus.Failed;
        }

        return await AwaitCompletionAsync(job, cancellationToken).ConfigureAwait(false);
    }

    private async Task<ApiJob> UploadAsync(LibraryEntry entry, Guid requestId, CancellationToken cancellationToken)
    {
        try
        {
            return await PostAsync(entry, requestId, cancellationToken).ConfigureAwait(false);
        }
        catch (Exception ex) when (ex is HttpRequestException
                                   || (ex is TaskCanceledException && !cancellationToken.IsCancellationRequested))
        {
            // No response seen, so the upload may or may not have committed.
            // The same request id makes the retry a replay, never a second
            // ingestion of the same document.
            return await PostAsync(entry, requestId, cancellationToken).ConfigureAwait(false);
        }
    }

    private async Task<ApiJob> PostAsync(LibraryEntry entry, Guid requestId, CancellationToken cancellationToken)
    {
        await using var content = File.OpenRead(entry.FullPath);
        return await _api
            .UploadAsync(_sessionState.SessionId, requestId, TitleFor(entry), entry.FileName, content, cancellationToken)
            .ConfigureAwait(false);
    }

    // Poll until the server reaches a terminal state. Running out of time is
    // reported as still processing, not as failure: the job keeps running on
    // the server and the source appears under "Your sources" when it is done.
    private async Task<LibrarySourceStatus> AwaitCompletionAsync(ApiJob job, CancellationToken cancellationToken)
    {
        var deadline = _now() + _timeout;
        while (job.IsProcessing)
        {
            if (_now() >= deadline)
            {
                return LibrarySourceStatus.Processing;
            }

            var wait = job.PollAfterMs is { } ms && ms > 0 ? TimeSpan.FromMilliseconds(ms) : _pollInterval;
            await _delay(wait, cancellationToken).ConfigureAwait(false);

            try
            {
                job = await _api.GetJobAsync(_sessionState.SessionId, job.JobId, cancellationToken).ConfigureAwait(false);
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                throw;
            }
            catch (Exception ex) when (ex is HttpRequestException or ApiErrorException or TaskCanceledException)
            {
                // The upload itself was accepted, so a lost poll is not a
                // failed ingestion: say it is still processing.
                return LibrarySourceStatus.Processing;
            }
        }

        return job.IsReady ? LibrarySourceStatus.Ready : LibrarySourceStatus.Failed;
    }

    // The server requires 1..200 characters. The file name without its
    // extension is what the student recognises in their library.
    private static string TitleFor(LibraryEntry entry)
    {
        var title = Path.GetFileNameWithoutExtension(entry.FileName);
        if (string.IsNullOrWhiteSpace(title))
        {
            title = entry.FileName;
        }

        return title.Length > MaxTitleChars ? title[..MaxTitleChars] : title;
    }
}
