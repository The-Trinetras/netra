using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using Netra.Desktop.Networking;
using Netra.Desktop.ViewModels;
using Xunit;

namespace Netra.Desktop.Tests;

// First-run sign-in (D-CRED; the exchange route C2 is pending): the exact
// request the client sends, how every outcome is told to the student, and
// that neither the code nor the credential ever appears in a status.
public sealed class AccessCodeSignInTests
{
    private const string Code = "PLUM-4821-KITE";
    private const string Token = "device-credential-not-real";
    private static readonly Uri Base = new("https://netra.test/");

    [Fact]
    public async Task TheExchangeSendsTheCodeOnlyInTheBodyWithNoCredentialHeader()
    {
        var handler = new ScriptedHandler();
        handler.Respond(HttpStatusCode.OK, $$"""{"device_credential":"{{Token}}","expires_at":null}""");
        using var exchange = new HttpAccessCodeExchange(Base, handler);
        var requestId = Guid.NewGuid();

        var credential = await exchange.ExchangeAsync(requestId, Code, CancellationToken.None);

        Assert.Equal(Token, credential.Token);
        Assert.Null(credential.ExpiresAt);
        var request = Assert.Single(handler.Requests);
        Assert.Equal(HttpMethod.Post, request.Method);
        Assert.Equal("https://netra.test/v1/access-codes/exchange", request.Uri.ToString());
        Assert.Null(request.Authorization);
        Assert.DoesNotContain(Code, request.Uri.ToString());
        using var body = JsonDocument.Parse(request.Body!);
        Assert.Equal(requestId, body.RootElement.GetProperty("request_id").GetGuid());
        Assert.Equal(Code, body.RootElement.GetProperty("access_code").GetString());
        Assert.Equal(2, body.RootElement.EnumerateObject().Count());
    }

    [Fact]
    public async Task ALostResponseIsRetriedOnceUnderTheSameRequestId()
    {
        var handler = new ScriptedHandler();
        handler.Fail(new HttpRequestException("connection reset"));
        handler.Respond(HttpStatusCode.OK, $$"""{"device_credential":"{{Token}}"}""");
        using var exchange = new HttpAccessCodeExchange(Base, handler);

        await exchange.ExchangeAsync(Guid.NewGuid(), Code, CancellationToken.None);

        Assert.Equal(2, handler.Requests.Count);
        Assert.Equal(handler.Requests[0].Body, handler.Requests[1].Body);
    }

    [Fact]
    public async Task ARefusalIsATypedErrorAndIsNotRetried()
    {
        var handler = new ScriptedHandler();
        handler.Respond(HttpStatusCode.Forbidden, """{"error":{"code":"AUTHORIZATION_DENIED","message":"Not accepted.","retryable":false}}""");
        using var exchange = new HttpAccessCodeExchange(Base, handler);

        var error = await Assert.ThrowsAsync<ApiErrorException>(() => exchange.ExchangeAsync(Guid.NewGuid(), Code, CancellationToken.None));

        Assert.Equal(403, error.StatusCode);
        Assert.Equal(Protocol.Dto.ErrorCode.AuthorizationDenied, error.Error!.Code);
        Assert.Single(handler.Requests);
    }

    [Fact]
    public async Task AServerWithoutTheRouteSaysSo()
    {
        var handler = new ScriptedHandler();
        handler.Respond(HttpStatusCode.NotFound, """{"detail":"Not Found"}""");
        using var exchange = new HttpAccessCodeExchange(Base, handler);

        await Assert.ThrowsAsync<AccessCodeExchangeUnavailableException>(() => exchange.ExchangeAsync(Guid.NewGuid(), Code, CancellationToken.None));
    }

    [Fact]
    public async Task AResponseWithoutACredentialIsRefused()
    {
        var handler = new ScriptedHandler();
        handler.Respond(HttpStatusCode.OK, """{"device_credential":"  "}""");
        using var exchange = new HttpAccessCodeExchange(Base, handler);

        await Assert.ThrowsAsync<Protocol.ProtocolException>(() => exchange.ExchangeAsync(Guid.NewGuid(), Code, CancellationToken.None));
    }

    [Fact]
    public async Task ASuccessfulSignInSavesTheCredentialClearsTheCodeAndSaysSo()
    {
        var store = new MemoryStore();
        var exchange = new ScriptedExchange { Result = new DeviceCredential(Token, null) };
        var viewModel = new SignInViewModel(exchange, store) { AccessCode = $"  {Code} " };
        var signedIn = 0;
        viewModel.SignedIn += (_, _) => signedIn++;

        await viewModel.SignInAsync(CancellationToken.None);

        Assert.Equal(Code, Assert.Single(exchange.Codes));
        Assert.Equal(Token, store.Saved);
        Assert.Equal(1, signedIn);
        Assert.Equal(string.Empty, viewModel.AccessCode);
        Assert.True(viewModel.RememberedOnThisComputer);
        Assert.Equal("Signed in. Netra will remember this computer.", viewModel.StatusMessage);
        Assert.False(viewModel.IsBusy);
    }

    [Theory]
    [InlineData(403, "AUTHORIZATION_DENIED", "That access code was not accepted. It may be mistyped, already used or expired. Check it and try again, or ask your teacher for a new code.")]
    [InlineData(401, "AUTH_REQUIRED", "That access code was not accepted. It may be mistyped, already used or expired. Check it and try again, or ask your teacher for a new code.")]
    [InlineData(503, "PROVIDER_UNAVAILABLE", "Netra could not sign you in right now. Try again shortly.")]
    public async Task ARefusedCodeIsExplainedAndKeptForCorrection(int status, string code, string expected)
    {
        var store = new MemoryStore();
        var error = JsonSerializer.Deserialize<Protocol.Dto.ErrorPayload>(
            $$"""{"code":"{{code}}","message":"x","retryable":{{(status == 503 ? "true" : "false")}}}""",
            Protocol.NetraJsonSerialization.Options);
        var exchange = new ScriptedExchange { Failure = new ApiErrorException(status, error) };
        var viewModel = new SignInViewModel(exchange, store) { AccessCode = Code };
        var signedIn = false;
        viewModel.SignedIn += (_, _) => signedIn = true;

        await viewModel.SignInAsync(CancellationToken.None);

        Assert.Equal(expected, viewModel.StatusMessage);
        Assert.Equal(Code, viewModel.AccessCode);
        Assert.Null(store.Saved);
        Assert.False(signedIn);
    }

    [Theory]
    [MemberData(nameof(TransportFailures))]
    public async Task EveryOtherFailureIsToldPlainly(Exception failure, string expected)
    {
        var viewModel = new SignInViewModel(new ScriptedExchange { Failure = failure }, new MemoryStore()) { AccessCode = Code };

        await viewModel.SignInAsync(CancellationToken.None);

        Assert.Equal(expected, viewModel.StatusMessage);
        Assert.DoesNotContain(Code, viewModel.StatusMessage);
    }

    public static TheoryData<Exception, string> TransportFailures() => new()
    {
        { new AccessCodeExchangeUnavailableException(), "This Netra server does not accept access codes yet. Ask your teacher for help." },
        { new HttpRequestException("refused"), "Could not reach the Netra server. Check your internet connection and try again." },
        { new TaskCanceledException("client timeout"), "Timed out reaching Netra. Check your internet connection and try again." },
        { new Protocol.ProtocolException("bad"), "Netra's answer could not be read. Try again, or ask your teacher for help." },
    };

    [Fact]
    public async Task IfWindowsCannotSaveTheCredentialItIsKeptForThisRunAndTheStudentIsTold()
    {
        var windows = new MemoryStore { FailSave = true };
        var credentials = new SignedInCredentials(windows);
        var viewModel = new SignInViewModel(new ScriptedExchange { Result = new DeviceCredential(Token, null) }, credentials)
        {
            AccessCode = Code,
        };
        var signedIn = false;
        viewModel.SignedIn += (_, _) => signedIn = true;

        await viewModel.SignInAsync(CancellationToken.None);

        Assert.True(signedIn);
        Assert.False(viewModel.RememberedOnThisComputer);
        Assert.Equal("Signed in for now, but Windows could not save the sign-in. You will need a new access code next time.", viewModel.StatusMessage);
        Assert.Equal(Token, await credentials.GetBearerTokenAsync(CancellationToken.None));
        Assert.True(credentials.HasCredential);
        Assert.True(credentials.Delete());
        Assert.Null(await credentials.GetBearerTokenAsync(CancellationToken.None));
    }

    [Fact]
    public async Task AnEmptyCodeIsNotSent()
    {
        var exchange = new ScriptedExchange { Result = new DeviceCredential(Token, null) };
        var viewModel = new SignInViewModel(exchange, new MemoryStore()) { AccessCode = "   " };

        await viewModel.SignInAsync(CancellationToken.None);

        Assert.Empty(exchange.Codes);
        Assert.Equal("Type your access code first.", viewModel.StatusMessage);
    }

    [Fact]
    public async Task ASecondSubmitWhileSigningInSendsNothing()
    {
        var gate = new TaskCompletionSource<DeviceCredential>();
        var exchange = new ScriptedExchange { Pending = gate.Task };
        var viewModel = new SignInViewModel(exchange, new MemoryStore()) { AccessCode = Code };

        var first = viewModel.SignInAsync(CancellationToken.None);
        Assert.False(viewModel.SignInCommand.CanExecute(null));
        var second = viewModel.SignInAsync(CancellationToken.None);
        gate.SetResult(new DeviceCredential(Token, null));
        await Task.WhenAll(first, second).WaitAsync(TimeSpan.FromSeconds(5));

        Assert.Single(exchange.Codes);
    }

    [Fact]
    public void TheIntroSaysWhySignInIsAskedForAgain()
    {
        Assert.Equal(SignInViewModel.FirstRunIntro, new SignInViewModel(new ScriptedExchange(), new MemoryStore()).Intro);
        Assert.Equal(
            "Netra did not accept this computer's sign-in. It may have expired. Enter a new access code from your teacher.",
            new SignInViewModel(new ScriptedExchange(), new MemoryStore(), "Netra did not accept this computer's sign-in. It may have expired.").Intro);
    }

    [Fact]
    public void TheWindowsVaultFormIsTheUtf16FormCmdkeyWrites()
    {
        var blob = WindowsCredentialManagerSource.EncodeSecret(Token);

        Assert.Equal(Encoding.Unicode.GetBytes(Token), blob);
        Assert.Equal(Token, WindowsCredentialManagerSource.DecodeSecret(blob));
    }

    // Writes to the real vault, but only to a random throw-away target that
    // it deletes again; opt-in (NETRA_TEST_CREDENTIAL_WRITE=1) on Windows.
    [CredentialWriteFact]
    public async Task SaveReadAndDeleteRoundTripInTheRealVault()
    {
        var store = new WindowsCredentialManagerSource($"Netra:test-write-{Guid.NewGuid():N}");
        try
        {
            store.Save("throw-away-not-a-token");
            Assert.True(store.HasCredential);
            Assert.Equal("throw-away-not-a-token", await store.GetBearerTokenAsync(CancellationToken.None));
            store.Save("replaced-throw-away");
            Assert.Equal("replaced-throw-away", await store.GetBearerTokenAsync(CancellationToken.None));
        }
        finally
        {
            Assert.True(store.Delete());
        }

        Assert.False(store.HasCredential);
        Assert.False(store.Delete());
    }

    internal sealed class MemoryStore : ICredentialStore
    {
        public string? Saved { get; private set; }
        public bool FailSave { get; set; }
        public int Deletes { get; private set; }
        public bool FailDelete { get; set; }

        public bool HasCredential => Saved is not null;

        public ValueTask<string?> GetBearerTokenAsync(CancellationToken cancellationToken) => ValueTask.FromResult(Saved);

        public void Save(string token)
        {
            if (FailSave)
            {
                throw new CredentialStoreException(5);
            }

            Saved = token;
        }

        public bool Delete()
        {
            Deletes++;
            if (FailDelete)
            {
                throw new CredentialStoreException(5);
            }

            var had = Saved is not null;
            Saved = null;
            return had;
        }
    }

    private sealed class ScriptedExchange : IAccessCodeExchange
    {
        public List<string> Codes { get; } = new();
        public DeviceCredential? Result { get; init; }
        public Exception? Failure { get; init; }
        public Task<DeviceCredential>? Pending { get; init; }

        public Task<DeviceCredential> ExchangeAsync(Guid requestId, string accessCode, CancellationToken cancellationToken)
        {
            Codes.Add(accessCode);
            if (Pending is not null)
            {
                return Pending;
            }

            return Failure is not null ? Task.FromException<DeviceCredential>(Failure) : Task.FromResult(Result!);
        }
    }

    private sealed class ScriptedHandler : HttpMessageHandler
    {
        private readonly Queue<Func<HttpResponseMessage>> _responses = new();

        public List<(HttpMethod Method, Uri Uri, string? Authorization, string? Body)> Requests { get; } = new();

        public void Respond(HttpStatusCode status, string body) =>
            _responses.Enqueue(() => new HttpResponseMessage(status) { Content = new StringContent(body, Encoding.UTF8, "application/json") });

        public void Fail(Exception failure) => _responses.Enqueue(() => throw failure);

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            var body = request.Content is null ? null : await request.Content.ReadAsStringAsync(cancellationToken);
            Requests.Add((request.Method, request.RequestUri!, request.Headers.Authorization?.ToString(), body));
            return _responses.Dequeue()();
        }
    }
}

// Opt-in: set NETRA_TEST_CREDENTIAL_WRITE=1 on a Windows test machine.
public sealed class CredentialWriteFactAttribute : FactAttribute
{
    public const string Variable = "NETRA_TEST_CREDENTIAL_WRITE";

    public CredentialWriteFactAttribute()
    {
        if (!OperatingSystem.IsWindows())
        {
            Skip = "Needs Windows Credential Manager.";
        }
        else if (Environment.GetEnvironmentVariable(Variable) != "1")
        {
            Skip = $"{Variable}=1 not set (writes and deletes a throw-away vault entry)";
        }
    }
}
