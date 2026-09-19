using System.Threading;

namespace Netra.Desktop.Networking;

// The app's credential source: Windows Credential Manager, plus a session
// fallback for the one case where Windows refuses to save a credential the
// student just received. A one-time code cannot be exchanged twice, so
// losing that credential would leave the student unable to sign in at all;
// it is kept in memory for this run only, and the student is told.
public sealed class SignedInCredentials : ICredentialStore
{
    private readonly ICredentialStore _store;
    private string? _sessionOnly;

    public SignedInCredentials(ICredentialStore store)
    {
        _store = store;
    }

    public bool HasCredential => Volatile.Read(ref _sessionOnly) is not null || _store.HasCredential;

    public async ValueTask<string?> GetBearerTokenAsync(CancellationToken cancellationToken) =>
        Volatile.Read(ref _sessionOnly) ?? await _store.GetBearerTokenAsync(cancellationToken).ConfigureAwait(false);

    // Throws CredentialStoreException after keeping the credential for this
    // session, so the caller can say it will not be remembered.
    public void Save(string token)
    {
        try
        {
            _store.Save(token);
            Volatile.Write(ref _sessionOnly, null);
        }
        catch (CredentialStoreException)
        {
            Volatile.Write(ref _sessionOnly, token);
            throw;
        }
    }

    public bool Delete()
    {
        var hadSession = Interlocked.Exchange(ref _sessionOnly, null) is not null;
        return _store.Delete() || hadSession;
    }
}
