using System.IO;
using System.Net.Http;
using System.Threading;
using System.Windows.Input;
using Netra.Desktop.Networking;
using Netra.Desktop.Protocol;
using Netra.Desktop.Protocol.Dto;

namespace Netra.Desktop.ViewModels;

// First-run sign-in (decision D-CRED): the student types the one-time access
// code their teacher gave them; the app exchanges it once for a device
// credential and keeps that in Windows Credential Manager. The code and the
// credential are never logged, never shown in a status and never put in a
// URL. Every outcome ends in one spoken status sentence.
public sealed class SignInViewModel : ViewModelBase
{
    public const string FirstRunIntro =
        "Enter the access code your teacher gave you. You only need to do this once on this computer.";

    private readonly IAccessCodeExchange _exchange;
    private readonly ICredentialStore _store;
    private string _accessCode = string.Empty;
    private string _statusMessage = string.Empty;
    private bool _isBusy;

    // reason: why sign-in is being asked for again (for example an expired
    // credential); null on first run.
    public SignInViewModel(IAccessCodeExchange exchange, ICredentialStore store, string? reason = null)
    {
        _exchange = exchange;
        _store = store;
        Intro = reason is null ? FirstRunIntro : $"{reason} Enter a new access code from your teacher.";
        SignInCommand = new RelayCommand(_ => _ = SignInAsync(CancellationToken.None), _ => !IsBusy);
    }

    public string Intro { get; }

    public string AccessCode
    {
        get => _accessCode;
        set => SetField(ref _accessCode, value);
    }

    public string StatusMessage
    {
        get => _statusMessage;
        private set => SetField(ref _statusMessage, value);
    }

    public bool IsBusy
    {
        get => _isBusy;
        private set => SetField(ref _isBusy, value);
    }

    // False after a sign-in whose credential Windows could not save.
    public bool RememberedOnThisComputer { get; private set; }

    public ICommand SignInCommand { get; }

    public event EventHandler? SignedIn;

    // Runs on the UI thread; awaits keep it there, so bound state is safe.
    public async Task SignInAsync(CancellationToken cancellationToken)
    {
        if (IsBusy)
        {
            return;
        }

        var code = AccessCode.Trim();
        if (code.Length == 0)
        {
            StatusMessage = "Type your access code first.";
            return;
        }

        IsBusy = true;
        StatusMessage = "Signing in.";
        try
        {
            var credential = await _exchange.ExchangeAsync(Guid.NewGuid(), code, cancellationToken);
            AccessCode = string.Empty;
            try
            {
                _store.Save(credential.Token);
                RememberedOnThisComputer = true;
                StatusMessage = "Signed in. Netra will remember this computer.";
            }
            catch (CredentialStoreException)
            {
                RememberedOnThisComputer = false;
                StatusMessage = "Signed in for now, but Windows could not save the sign-in. You will need a new access code next time.";
            }

            SignedIn?.Invoke(this, EventArgs.Empty);
        }
        catch (Exception ex)
        {
            // The typed code stays in the box so a typing mistake can be
            // found and fixed.
            StatusMessage = Describe(ex, cancellationToken);
        }
        finally
        {
            IsBusy = false;
        }
    }

    private static string Describe(Exception ex, CancellationToken cancellationToken) => ex switch
    {
        OperationCanceledException when cancellationToken.IsCancellationRequested => "Sign-in cancelled.",
        AccessCodeExchangeUnavailableException =>
            "This Netra server does not accept access codes yet. Ask your teacher for help.",
        ApiErrorException { Error.Code: ErrorCode.AuthorizationDenied or ErrorCode.AuthRequired or ErrorCode.StaleRequest }
            or ApiErrorException { StatusCode: 401 or 403 } =>
            "That access code was not accepted. It may be mistyped, already used or expired. Check it and try again, or ask your teacher for a new code.",
        ApiErrorException { Error.Retryable: true } => "Netra could not sign you in right now. Try again shortly.",
        OperationCanceledException or TimeoutException =>
            "Timed out reaching Netra. Check your internet connection and try again.",
        HttpRequestException or IOException => "Could not reach the Netra server. Check your internet connection and try again.",
        ProtocolException => "Netra's answer could not be read. Try again, or ask your teacher for help.",
        _ => "Netra could not sign you in. Try again, or ask your teacher for help.",
    };
}
