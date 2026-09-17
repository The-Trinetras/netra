namespace Netra.Desktop.Library;

// Abstraction over "prepare this selected source for study." No HTTP
// contract for upload/ingestion exists in shared/contracts/ yet
// (docs/team/ownership.md: "Concrete ingestion/retrieval/persistence still
// needed" under M2's evidence/source boundary, and the jobs/v1 schema is
// empty) — see docs/team/handoffs/M5.md Gap 1. This interface lets
// LibraryViewModel depend on the eventual real implementation without
// knowing yet whether it is a multipart upload, a presigned-S3 flow, or a
// job-id poll.
public interface ISourcePreparationService
{
    Task<LibrarySourceStatus> PrepareAsync(LibraryEntry entry, CancellationToken cancellationToken);
}

// Explicit fixture double: always "succeeds" after a short simulated delay.
// Every entry prepared through this stays marked IsFixtureSourced = true, so
// nothing downstream can mistake it for a real ingestion result. This is
// scaffold-only and must be replaced, not extended, once M2's upload
// contract exists.
public sealed class FixtureSourcePreparationService : ISourcePreparationService
{
    public async Task<LibrarySourceStatus> PrepareAsync(LibraryEntry entry, CancellationToken cancellationToken)
    {
        entry.IsFixtureSourced = true;
        await Task.Delay(TimeSpan.FromMilliseconds(50), cancellationToken).ConfigureAwait(false);
        return LibrarySourceStatus.Ready;
    }
}
