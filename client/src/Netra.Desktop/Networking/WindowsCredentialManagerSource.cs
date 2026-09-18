using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

namespace Netra.Desktop.Networking;

// ICredentialSource over the Windows Credential Manager (per-user vault,
// protected by Windows; M5's recommended storage, docs/team/handoffs/M5.md
// INT-10a). Reads one generic credential; no NuGet package, no file, no
// registry value and no environment variable holds the token.
//
// Issuance is still an open M1/M5 decision (system-browser PKCE sign-in in
// message-flow.md flow 1). Until it exists, an operator stores a token that
// was provisioned for this account with Windows' own tool, e.g.
//     cmdkey /generic:Netra:api /user:netra /pass:<token>
// and removes it with `cmdkey /delete:Netra:api`. The token is read only when
// a connection or request needs it and is never logged or placed in a URL.
public sealed class WindowsCredentialManagerSource : ICredentialSource
{
    public const string DefaultTarget = "Netra:api";

    private const int CredTypeGeneric = 1;
    private const int ErrorNotFound = 1168;

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
        if (!CredReadW(target, CredTypeGeneric, 0, out var pointer))
        {
            var error = Marshal.GetLastWin32Error();
            if (error == ErrorNotFound)
            {
                return null;
            }

            // Any other failure: behave as "no credential" rather than guess.
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
                // cmdkey and the Credential Manager UI store the secret as UTF-16LE.
                var token = Encoding.Unicode.GetString(bytes).TrimEnd('\0');
                return string.IsNullOrWhiteSpace(token) ? null : token;
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
