using Netra.Desktop.Networking;

namespace Netra.Desktop.ViewModels;

// Signing this computer in and out in live mode (decision D-CRED). The
// composition root supplies the dialog and the live-session steps; this
// class decides their order, so that a sign-in or sign-out never mixes one
// student's session, conversation or sources with the next student's.
public sealed class LiveAccount : IAccountActions
{
    private readonly ICredentialStore _credentials;
    private readonly Func<string?, string?> _showSignIn;
    private readonly Func<Task> _startLive;
    private readonly Func<Task> _endLive;
    private readonly Action _clearScreen;
    private readonly Action<string> _report;

    // showSignIn: shows the sign-in dialog with an optional reason; returns
    //   its final status sentence after a sign-in, or null when cancelled.
    // startLive:  creates a session and connects (LiveSession).
    // endLive:    closes the connection on purpose and forgets the session.
    // clearScreen: removes conversation and source list from view.
    public LiveAccount(
        ICredentialStore credentials,
        Func<string?, string?> showSignIn,
        Func<Task> startLive,
        Func<Task> endLive,
        Action clearScreen,
        Action<string> report)
    {
        _credentials = credentials;
        _showSignIn = showSignIn;
        _startLive = startLive;
        _endLive = endLive;
        _clearScreen = clearScreen;
        _report = report;
    }

    public bool IsSignedIn => _credentials.HasCredential;

    public Task SignInAsync() => SignInAsync(reason: null);

    public async Task SignInAsync(string? reason)
    {
        var outcome = _showSignIn(reason);
        if (outcome is null)
        {
            _report(IsSignedIn
                ? "Sign-in cancelled. This computer's existing sign-in is kept."
                : "Not signed in. When you have your access code, choose Sign in under Preferences and status.");
            return;
        }

        // A new credential may belong to a different student: nothing of the
        // previous session carries over.
        _clearScreen();
        await EndLiveQuietlyAsync();
        _report(outcome);
        await _startLive();
    }

    public async Task SignOutAsync()
    {
        _clearScreen();
        await EndLiveQuietlyAsync();
        try
        {
            _credentials.Delete();
            _report("Signed out of Netra on this computer. A new access code is needed to sign in again.");
        }
        catch (CredentialStoreException)
        {
            _report("Netra is disconnected, but Windows could not remove the saved sign-in. Try Sign out again.");
        }
    }

    private async Task EndLiveQuietlyAsync()
    {
        try
        {
            await _endLive();
        }
        catch (Exception)
        {
            // Already disconnected; there is nothing further to close.
        }
    }
}
