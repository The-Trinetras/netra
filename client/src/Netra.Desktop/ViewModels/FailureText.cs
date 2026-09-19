using System.IO;
using System.Net.Http;
using System.Net.WebSockets;
using System.Threading;
using Netra.Desktop.Networking;
using Netra.Desktop.Protocol.Dto;

namespace Netra.Desktop.ViewModels;

// Client-owned, accessible wording for a failed server action. Total: every
// exception yields a final status, so a status such as "Opening…" can never
// be left standing after a failure. Never echoes endpoint, credential, server
// or exception text. `action` is a short verb phrase, e.g. "load your sources".
public static class FailureText
{
    public static string Describe(Exception ex, string action, CancellationToken cancellationToken) => ex switch
    {
        OperationCanceledException when cancellationToken.IsCancellationRequested => "Cancelled.",
        // HttpClient reports its own timeout as a cancellation the caller did not request.
        OperationCanceledException or TimeoutException => $"Timed out trying to {action}. Try again.",
        CredentialUnavailableException => "This computer is not signed in to Netra.",
        NotConnectedException => $"Not connected to Netra, so Netra could not {action}. Your place is kept; try again when connected.",
        CredentialRejectedException => SignInNotAccepted,
        ApiErrorException { Error.Code: ErrorCode.AuthRequired } => SignInNotAccepted,
        ApiErrorException { Error.Code: ErrorCode.AuthorizationDenied } => $"You do not have access to {action}.",
        ApiErrorException { Error.Retryable: true } => $"Netra could not {action} right now. Try again shortly.",
        ApiErrorException => $"Netra could not {action}.",
        HttpRequestException or WebSocketException or IOException => $"Could not reach the Netra server to {action}.",
        _ => $"Netra could not {action}.",
    };

    private const string SignInNotAccepted = "Netra did not accept this computer's sign-in. It may have expired.";
}
