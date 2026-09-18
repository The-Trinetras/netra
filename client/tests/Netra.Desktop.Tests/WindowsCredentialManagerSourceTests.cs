using System.Text;
using System.Threading;
using Netra.Desktop.Networking;
using Xunit;

namespace Netra.Desktop.Tests;

// The Credential Manager source. These tests never WRITE to the user's
// vault: the absent-target case calls the real CredReadW read-only, and the
// found case runs only when the tester created a throw-away credential
// themselves (see CredentialFactAttribute).
public sealed class WindowsCredentialManagerSourceTests
{
    [Fact]
    public void DecodesTheUtf16SecretCmdkeyStoresAndTrimsTrailingNuls()
    {
        Assert.Equal("tok-123", WindowsCredentialManagerSource.DecodeSecret(Encoding.Unicode.GetBytes("tok-123\0")));
        Assert.Null(WindowsCredentialManagerSource.DecodeSecret(Encoding.Unicode.GetBytes("   ")));
        Assert.Null(WindowsCredentialManagerSource.DecodeSecret(ReadOnlySpan<byte>.Empty));
    }

    [Fact]
    public async Task AnAbsentCredentialIsNoCredential()
    {
        var source = new WindowsCredentialManagerSource($"Netra:test-absent-{Guid.NewGuid():N}");

        Assert.Null(await source.GetBearerTokenAsync(CancellationToken.None));
    }

    [CredentialFact]
    public async Task ReadsAGenericCredentialTheTesterStoredWithCmdkey()
    {
        var target = Environment.GetEnvironmentVariable(CredentialFactAttribute.TargetVariable)!;
        var expected = Environment.GetEnvironmentVariable(CredentialFactAttribute.SecretVariable)!;

        Assert.Equal(expected, await new WindowsCredentialManagerSource(target).GetBearerTokenAsync(CancellationToken.None));
    }
}

// Opt-in: `cmdkey /generic:Netra:test-read /user:netra /pass:not-a-real-token`,
// then set NETRA_TEST_CREDENTIAL_TARGET=Netra:test-read and
// NETRA_TEST_CREDENTIAL_SECRET=not-a-real-token; remove it afterwards with
// `cmdkey /delete:Netra:test-read`. Use a throw-away value, never a real token.
public sealed class CredentialFactAttribute : FactAttribute
{
    public const string TargetVariable = "NETRA_TEST_CREDENTIAL_TARGET";
    public const string SecretVariable = "NETRA_TEST_CREDENTIAL_SECRET";

    public CredentialFactAttribute()
    {
        if (string.IsNullOrEmpty(Environment.GetEnvironmentVariable(TargetVariable))
            || string.IsNullOrEmpty(Environment.GetEnvironmentVariable(SecretVariable)))
        {
            Skip = $"{TargetVariable}/{SecretVariable} not set (needs a throw-away credential stored by the tester)";
        }
    }
}
