using System.Threading;
using Netra.Desktop.Networking;
using Netra.Desktop.Protocol.Dto;
using Netra.Desktop.State;
using Xunit;

namespace Netra.Desktop.Tests;

// Reconnect restores through the existing protocol only: session.resume,
// then the pending mutation under its ORIGINAL request_id. Delays are
// injected, so no test sleeps.
public sealed class ReconnectCoordinatorTests
{
    private static readonly Uri Endpoint = new("ws://localhost:8000/v1/ws");

    private static (CapturingSocket Socket, ConnectionManager Connection, ClientSessionState State, ReconnectCoordinator Coordinator, List<TimeSpan> Delays) Build(int attempts = 3)
    {
        var socket = new CapturingSocket();
        var state = new ClientSessionState();
        state.Initialize(Guid.NewGuid(), sessionVersion: 7);
        state.LastAcknowledgedSentenceId = "sentence-42";
        var connection = new ConnectionManager(socket, state);
        var delays = new List<TimeSpan>();
        var coordinator = new ReconnectCoordinator(
            socket, connection, state, Endpoint,
            backoff: Enumerable.Repeat(TimeSpan.FromSeconds(1), attempts).ToArray(),
            delay: (d, _) => { delays.Add(d); return Task.CompletedTask; },
            jitter: new Random(1));
        return (socket, connection, state, coordinator, delays);
    }

    [Fact]
    public async Task ConnectSendsResumeWithTheLastKnownVersionAndAcknowledgedSentence()
    {
        var (socket, _, state, coordinator, _) = Build();

        await coordinator.ConnectAsync(CancellationToken.None);

        Assert.Equal(ConnectionState.Connected, state.ConnectionState);
        var resume = Assert.Single(socket.Sent);
        Assert.Contains("\"session.resume\"", resume);
        Assert.Contains("\"last_known_session_version\":7", resume);
        Assert.Contains("\"last_acknowledged_sentence_id\":\"sentence-42\"", resume);
    }

    [Fact]
    public async Task DropThenReconnectResendsThePendingTurnUnderItsOriginalRequestId()
    {
        var (socket, connection, state, coordinator, delays) = Build();
        await coordinator.ConnectAsync(CancellationToken.None);
        await connection.SendTurnSubmitAsync(
            new TurnSubmitPayload { Utterance = "why is it straight", InputMode = InputMode.Keyboard, ExpectedSessionVersion = 7 },
            CancellationToken.None);
        var originalRequestId = RequestIdOf(socket.Sent.Last());
        socket.FailNextConnects = 1;

        socket.Drop();
        await coordinator.CurrentReconnectLoop!;

        Assert.Equal(ConnectionState.Connected, state.ConnectionState);
        Assert.Equal(2, delays.Count); // one failed attempt, one success
        Assert.All(delays, d => Assert.InRange(d.TotalMilliseconds, 800, 1200)); // jitter stays bounded
        var sent = socket.Sent.ToList();
        var resumeIndex = sent.FindLastIndex(m => m.Contains("\"session.resume\""));
        var resent = sent.Last();
        Assert.True(resumeIndex < sent.Count - 1); // resume first, then the retry
        Assert.Contains("\"turn.submit\"", resent);
        Assert.Equal(originalRequestId, RequestIdOf(resent));
        await coordinator.DisposeAsync();
    }

    [Fact]
    public async Task GivesUpAfterTheBoundedAttemptsAndSaysSo()
    {
        var (socket, _, state, coordinator, delays) = Build(attempts: 3);
        var statuses = new List<string>();
        coordinator.StatusChanged += (_, m) => statuses.Add(m);
        await coordinator.ConnectAsync(CancellationToken.None);
        socket.FailNextConnects = 100;

        socket.Drop();
        await coordinator.CurrentReconnectLoop!;

        Assert.Equal(3, delays.Count);
        Assert.Equal(ConnectionState.Disconnected, state.ConnectionState);
        Assert.Contains("try again later", statuses.Last());
        await coordinator.DisposeAsync();
    }

    [Fact]
    public async Task MissingCredentialIsNotRetried()
    {
        var (socket, _, state, coordinator, delays) = Build(attempts: 5);
        await coordinator.ConnectAsync(CancellationToken.None);
        socket.FailNextConnects = 100;
        socket.ConnectFailure = new CredentialUnavailableException();

        socket.Drop();
        await coordinator.CurrentReconnectLoop!;

        Assert.Single(delays);
        Assert.Equal(ConnectionState.Disconnected, state.ConnectionState);
        await coordinator.DisposeAsync();
    }

    // Before: an expired/revoked token (403 on the upgrade) looked like a
    // network failure, was retried through every backoff step, and ended with
    // "try again later", which could never succeed.
    [Fact]
    public async Task ARejectedCredentialIsNotRetriedAndIsNamedAsTheCause()
    {
        var (socket, _, state, coordinator, delays) = Build(attempts: 5);
        var statuses = new List<string>();
        coordinator.StatusChanged += (_, message) => statuses.Add(message);
        await coordinator.ConnectAsync(CancellationToken.None);
        socket.FailNextConnects = 100;
        socket.ConnectFailure = new CredentialRejectedException();

        socket.Drop();
        await coordinator.CurrentReconnectLoop!;

        Assert.Single(delays);
        Assert.Equal(2, socket.ConnectCount);
        Assert.Equal(ConnectionState.Disconnected, state.ConnectionState);
        Assert.Equal("Cannot reconnect: Netra did not accept this computer's sign-in. It may have expired. Choose Sign in under Preferences and status to enter a new access code.", statuses[^1]);
        await coordinator.DisposeAsync();
    }

    [Fact]
    public async Task ShutdownDoesNotReconnect()
    {
        var (socket, _, _, coordinator, _) = Build();
        await coordinator.ConnectAsync(CancellationToken.None);
        var connects = socket.ConnectCount;

        await coordinator.DisposeAsync();
        socket.Drop();

        Assert.Equal(connects, socket.ConnectCount);
    }

    private static string RequestIdOf(string json)
    {
        using var document = System.Text.Json.JsonDocument.Parse(json);
        return document.RootElement.GetProperty("request_id").GetString()!;
    }
}
