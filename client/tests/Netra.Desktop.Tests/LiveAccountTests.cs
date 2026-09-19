using System.Net;
using System.Net.Sockets;
using System.Net.WebSockets;
using Netra.Desktop.Networking;
using Netra.Desktop.Protocol.Dto;
using Netra.Desktop.State;
using Netra.Desktop.ViewModels;
using Xunit;

namespace Netra.Desktop.Tests;

// Signing a computer in and out (D-CRED): order of steps, what the student
// hears, and that nothing of one student's session reaches the next.
public sealed class LiveAccountTests
{
    [Fact]
    public async Task SigningInClearsTheScreenEndsTheOldSessionThenStartsANewOne()
    {
        var rig = new Rig { DialogOutcome = "Signed in. Netra will remember this computer." };

        await rig.Account.SignInAsync();

        Assert.Equal(new[] { "dialog:", "clear", "end", "report:Signed in. Netra will remember this computer.", "start" }, rig.Steps);
    }

    [Fact]
    public async Task AReasonIsPassedToTheDialog()
    {
        var rig = new Rig();

        await rig.Account.SignInAsync("Netra did not accept this computer's sign-in. It may have expired.");

        Assert.Equal("dialog:Netra did not accept this computer's sign-in. It may have expired.", rig.Steps[0]);
    }

    [Fact]
    public async Task CancellingChangesNothingAndSaysWhetherTheOldSignInIsKept()
    {
        var notSignedIn = new Rig();
        await notSignedIn.Account.SignInAsync();
        Assert.Equal(new[] { "dialog:", "report:Not signed in. When you have your access code, choose Sign in under Preferences and status." }, notSignedIn.Steps);

        var signedIn = new Rig();
        signedIn.Store.Save("existing");
        await signedIn.Account.SignInAsync();
        Assert.Equal(new[] { "dialog:", "report:Sign-in cancelled. This computer's existing sign-in is kept." }, signedIn.Steps);
        Assert.Equal("existing", signedIn.Store.Saved);
    }

    [Fact]
    public async Task SigningOutClearsTheScreenDisconnectsAndForgetsTheCredential()
    {
        var rig = new Rig();
        rig.Store.Save("device-credential");

        await rig.Account.SignOutAsync();

        Assert.Equal(new[] { "clear", "end", "report:Signed out of Netra on this computer. A new access code is needed to sign in again." }, rig.Steps);
        Assert.Null(rig.Store.Saved);
        Assert.False(rig.Account.IsSignedIn);
    }

    [Fact]
    public async Task ADisconnectThatFailsDoesNotStopTheSignOut()
    {
        var rig = new Rig { EndFails = true };
        rig.Store.Save("device-credential");

        await rig.Account.SignOutAsync();

        Assert.Null(rig.Store.Saved);
        Assert.StartsWith("report:Signed out", rig.Steps.Last());
    }

    [Fact]
    public async Task IfWindowsCannotRemoveTheCredentialTheStudentIsTold()
    {
        var rig = new Rig();
        rig.Store.Save("device-credential");
        rig.Store.FailDelete = true;

        await rig.Account.SignOutAsync();

        Assert.Equal("report:Netra is disconnected, but Windows could not remove the saved sign-in. Try Sign out again.", rig.Steps.Last());
    }

    [Fact]
    public async Task PreferencesShowTheSignInStateAndRefreshItAfterEachAction()
    {
        var rig = new Rig { DialogOutcome = "Signed in.", OnDialog = store => store.Save("new") };
        var preferences = new PreferencesViewModel(new ClientSessionState(), account: rig.Account);
        Assert.Equal("This computer is not signed in to Netra.", preferences.AccountStatus);

        preferences.SignInCommand.Execute(null);
        await Eventually.TrueAsync(() => preferences.AccountStatus == "This computer is signed in to Netra.", "the status refreshes");
        await preferences.SignOutAsync();

        Assert.Equal("This computer is not signed in to Netra.", preferences.AccountStatus);
        Assert.True(preferences.HasAccount);
        Assert.False(new PreferencesViewModel(new ClientSessionState()).HasAccount);
    }

    [Fact]
    public void ResettingTheSessionStateKeepsNothingOfThePreviousStudent()
    {
        var state = new ClientSessionState
        {
            ActiveSourceVersionId = "v-1",
            CurrentBlockId = "b-1",
            CurrentSentenceId = "s-1",
            LastAcknowledgedSentenceId = "s-0",
            InteractionMode = SessionInteractionMode.Quiz,
            PendingQuestionId = "q-1",
            LastResultSetId = Guid.NewGuid(),
            CurrentGenerationId = "g-1",
        };
        state.Initialize(Guid.NewGuid(), 42);
        state.TryMarkApplied(Guid.Empty);

        state.Reset();

        Assert.Equal(Guid.Empty, state.SessionId);
        Assert.Equal(0, state.SessionVersion);
        Assert.Null(state.ActiveSourceVersionId);
        Assert.Null(state.CurrentSentenceId);
        Assert.Null(state.LastAcknowledgedSentenceId);
        Assert.Null(state.PendingQuestionId);
        Assert.Null(state.LastResultSetId);
        Assert.Null(state.CurrentGenerationId);
        Assert.Equal(SessionInteractionMode.Idle, state.InteractionMode);
        Assert.True(state.TryMarkApplied(Guid.Empty));
    }

    // D-open-1: a close is a real close handshake, reported once.
    [Fact]
    public async Task ClosingSendsANormalCloseAndReportsOneDisconnect()
    {
        var port = FreePort();
        using var listener = new HttpListener();
        listener.Prefixes.Add($"http://localhost:{port}/v1/ws/");
        listener.Start();
        WebSocketCloseStatus? seen = null;
        var server = Task.Run(async () =>
        {
            var ws = (await (await listener.GetContextAsync()).AcceptWebSocketAsync(null)).WebSocket;
            var result = await ws.ReceiveAsync(new byte[1024], CancellationToken.None);
            seen = result.CloseStatus;
            await ws.CloseOutputAsync(WebSocketCloseStatus.NormalClosure, null, CancellationToken.None);
        });
        await using var client = new NetraWebSocketClient(new NetraApiClientTests.FixedCredentials("test-token-not-real"));
        var disconnects = 0;
        var faults = 0;
        client.Disconnected += (_, _) => Interlocked.Increment(ref disconnects);
        client.ConnectionFaulted += (_, _) => Interlocked.Increment(ref faults);
        await client.ConnectAsync(new Uri($"ws://localhost:{port}/v1/ws/"), CancellationToken.None);

        await client.CloseAsync(CancellationToken.None);
        await server.WaitAsync(TimeSpan.FromSeconds(10));
        await Eventually.SettleAsync();

        Assert.Equal(WebSocketCloseStatus.NormalClosure, seen);
        Assert.Equal(1, disconnects);
        Assert.Equal(0, faults);
        Assert.False(client.IsConnected);
    }

    [Fact]
    public async Task ADeliberateDisconnectIsNotReconnectedUntilTheNextConnect()
    {
        var state = new ClientSessionState();
        state.Initialize(Guid.NewGuid(), 1);
        var socket = new CountingSocket();
        var connection = new ConnectionManager(socket, state);
        var delays = 0;
        await using var reconnect = new ReconnectCoordinator(
            socket, connection, state, new Uri("ws://127.0.0.1:1/v1/ws"),
            delay: (_, _) => { Interlocked.Increment(ref delays); return Task.CompletedTask; });
        await reconnect.ConnectAsync(CancellationToken.None);

        await reconnect.DisconnectAsync(CancellationToken.None);
        await Eventually.SettleAsync();

        Assert.Equal(0, delays);
        Assert.Equal(1, socket.Connects);
        Assert.Equal(ConnectionState.Disconnected, state.ConnectionState);
        Assert.Null(reconnect.CurrentReconnectLoop);

        await reconnect.ConnectAsync(CancellationToken.None);
        socket.Drop();
        await Eventually.TrueAsync(() => socket.Connects == 3, "an ordinary drop reconnects again");
    }

    private static int FreePort()
    {
        var probe = new TcpListener(IPAddress.Loopback, 0);
        probe.Start();
        var port = ((IPEndPoint)probe.LocalEndpoint).Port;
        probe.Stop();
        return port;
    }

    private sealed class Rig
    {
        public Rig()
        {
            Account = new LiveAccount(
                Store,
                reason =>
                {
                    Steps.Add("dialog:" + reason);
                    OnDialog?.Invoke(Store);
                    return DialogOutcome;
                },
                () => { Steps.Add("start"); return Task.CompletedTask; },
                () =>
                {
                    Steps.Add("end");
                    return EndFails ? Task.FromException(new NotConnectedException()) : Task.CompletedTask;
                },
                () => Steps.Add("clear"),
                message => Steps.Add("report:" + message));
        }

        public AccessCodeSignInTests.MemoryStore Store { get; } = new();
        public List<string> Steps { get; } = new();
        public string? DialogOutcome { get; init; }
        public Action<ICredentialStore>? OnDialog { get; init; }
        public bool EndFails { get; init; }
        public LiveAccount Account { get; }
    }

    private sealed class CountingSocket : INetraWebSocketClient
    {
        private int _connects;

        public int Connects => Volatile.Read(ref _connects);
        public bool IsConnected { get; private set; }

        public event EventHandler<string>? TextMessageReceived;
        public event EventHandler<ReadOnlyMemory<byte>>? BinaryMessageReceived;
        public event EventHandler<Exception>? ConnectionFaulted;
        public event EventHandler? Disconnected;

        public void Drop()
        {
            IsConnected = false;
            Disconnected?.Invoke(this, EventArgs.Empty);
        }

        public Task ConnectAsync(Uri endpoint, CancellationToken cancellationToken)
        {
            Interlocked.Increment(ref _connects);
            IsConnected = true;
            return Task.CompletedTask;
        }

        public Task SendTextAsync(string message, CancellationToken cancellationToken) => Task.CompletedTask;

        public Task SendBinaryAsync(ReadOnlyMemory<byte> message, CancellationToken cancellationToken) => Task.CompletedTask;

        public Task CloseAsync(CancellationToken cancellationToken)
        {
            Drop();
            return Task.CompletedTask;
        }

        public ValueTask DisposeAsync() => ValueTask.CompletedTask;
    }
}
