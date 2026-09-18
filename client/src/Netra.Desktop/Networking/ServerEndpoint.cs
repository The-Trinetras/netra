using System.Net;
using System.Threading;

namespace Netra.Desktop.Networking;

// Supplies the bearer credential for the WebSocket upgrade. M1's server
// verifies `Authorization: Bearer <token>` BEFORE accepting the socket
// (transport/http/auth.py; M1 route proposal, reviewed by M5 — see
// docs/team/handoffs/M5.md). How a desktop obtains and protects the token
// (issuance route, Windows credential storage) is not decided, so no
// production source exists yet; without one the client does not connect.
public interface ICredentialSource
{
    // Returns null when no credential is available. The value must never be
    // logged, put in a URL, or kept longer than the connect call needs it.
    ValueTask<string?> GetBearerTokenAsync(CancellationToken cancellationToken);
}

public sealed class CredentialUnavailableException : Exception
{
    public CredentialUnavailableException()
        : base("No credential is available; the client does not connect anonymously.")
    {
    }
}

public sealed class InvalidServerEndpointException : Exception
{
    public InvalidServerEndpointException(string message)
        : base(message)
    {
    }
}

public static class ServerEndpoint
{
    // Validates a WebSocket endpoint before any connection attempt:
    // - wss required, except ws to a loopback host (local development/tests);
    // - no user info, query or fragment, so no credential or id can ride in
    //   the URL (client.md: "Never include tokens in URLs").
    public static Uri Validate(Uri endpoint)
    {
        if (!endpoint.IsAbsoluteUri)
        {
            throw new InvalidServerEndpointException("The server endpoint must be an absolute URI.");
        }

        var loopback = IsLoopback(endpoint);
        if (endpoint.Scheme != "wss" && !(endpoint.Scheme == "ws" && loopback))
        {
            throw new InvalidServerEndpointException("The server endpoint must use wss (ws only for a loopback host).");
        }

        if (!string.IsNullOrEmpty(endpoint.UserInfo) || !string.IsNullOrEmpty(endpoint.Query) || !string.IsNullOrEmpty(endpoint.Fragment))
        {
            throw new InvalidServerEndpointException("The server endpoint must not carry user info, a query string or a fragment.");
        }

        return endpoint;
    }

    private static bool IsLoopback(Uri endpoint) =>
        endpoint.IsLoopback
        || (IPAddress.TryParse(endpoint.Host.Trim('[', ']'), out var address) && IPAddress.IsLoopback(address));
}
