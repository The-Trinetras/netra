using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

namespace Netra.Desktop.Networking;

// ICredentialSource over the Windows Credential Manager (per-user vault,
// protected by Windows). No NuGet package, file, registry value or
// environment variable holds the token.
//
// NOT A DECISION: INT-10a leaves Windows credential storage open (DPAPI
// ProtectedData scoped to the user, or Credential Manager). This source was
// chosen for integration because an operator can manage it with Windows' own
// tool; M5/M1 still decide the production store, and issuance (system-browser
// PKCE sign-in, message-flow.md flow 1) does not exist yet. Until then an
// operator stores a token provisioned for the account, e.g.
//     cmdkey /generic:Netra:api /user:netra /pass:<token>
// and removes it with `cmdkey /delete:Netra:api`. The token is read only when
// a connection or request needs it and is never logged or placed in a URL.
public sealed class WindowsCredentialManagerSource : ICredentialSource
{
    public const string DefaultTarget = "Netra:api";

    private const int CredTypeGeneric = 1;

    private readonly string _target;

    public WindowsCredentialManagerSource(string target = DefaultTarget)
    {
        _target = target;
    }

    public ValueTask<string?> GetBearerTokenAsync(CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        return ValueTask.FromResult(Read(_target));
    }

    private static string? Read(string target)
    {
        // Not found (ERROR_NOT_FOUND) or any other failure: no credential.
        // Guessing past a failed read could only present the wrong identity.
        if (!CredReadW(target, CredTypeGeneric, 0, out var pointer))
        {
            return null;
        }

        try
        {
            var credential = Marshal.PtrToStructure<NativeCredential>(pointer);
            if (credential.CredentialBlob == IntPtr.Zero || credential.CredentialBlobSize <= 0)
            {
                return null;
            }

            var bytes = new byte[credential.CredentialBlobSize];
            Marshal.Copy(credential.CredentialBlob, bytes, 0, bytes.Length);
            try
            {
                return DecodeSecret(bytes);
            }
            finally
            {
                Array.Clear(bytes);
            }
        }
        finally
        {
            CredFree(pointer);
        }
    }

    // cmdkey and the Credential Manager UI store a generic credential's
    // secret as UTF-16LE. Public only so the decoding can be tested without
    // writing to the user's vault.
    public static string? DecodeSecret(ReadOnlySpan<byte> blob)
    {
        var token = Encoding.Unicode.GetString(blob).TrimEnd('\0');
        return string.IsNullOrWhiteSpace(token) ? null : token;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct NativeCredential
    {
        public int Flags;
        public int Type;
        public IntPtr TargetName;
        public IntPtr Comment;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
        public int CredentialBlobSize;
        public IntPtr CredentialBlob;
        public int Persist;
        public int AttributeCount;
        public IntPtr Attributes;
        public IntPtr TargetAlias;
        public IntPtr UserName;
    }

    [DllImport("advapi32.dll", EntryPoint = "CredReadW", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool CredReadW(string target, int type, int reservedFlag, out IntPtr credential);

    [DllImport("advapi32.dll", SetLastError = false)]
    private static extern void CredFree(IntPtr buffer);
}
