using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

namespace Netra.Desktop.Networking;

// Where the device credential lives between launches (decision D-CRED: a
// one-time access code is exchanged once for a device credential kept in
// Windows Credential Manager).
public interface ICredentialStore : ICredentialSource
{
    bool HasCredential { get; }

    // Replaces any stored credential. Throws CredentialStoreException when
    // Windows refuses; the caller then keeps the credential for this session.
    void Save(string token);

    // Forgets this computer's sign-in. False when nothing was stored.
    bool Delete();
}

public sealed class CredentialStoreException : Exception
{
    public CredentialStoreException(int win32Error)
        : base($"Windows could not update the Netra sign-in (error {win32Error}).")
    {
    }
}

// ICredentialStore over the Windows Credential Manager (per-user vault,
// protected by Windows). No NuGet package, file, registry value or
// environment variable holds the token. The sign-in screen writes it after
// an access-code exchange (F2); an operator can still manage it with
// Windows' own tool:
//     cmdkey /generic:Netra:api /user:netra /pass:<token>
//     cmdkey /delete:Netra:api
// The token is read only when a connection or request needs it and is never
// logged or placed in a URL.
public sealed class WindowsCredentialManagerSource : ICredentialStore
{
    public const string DefaultTarget = "Netra:api";

    private const int CredTypeGeneric = 1;

    // This user on this computer only: never roams with a domain profile.
    private const int CredPersistLocalMachine = 2;
    private const int ErrorNotFound = 1168;
    private const string UserName = "netra";

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

    public bool HasCredential => Read(_target) is not null;

    public void Save(string token)
    {
        if (string.IsNullOrWhiteSpace(token))
        {
            throw new ArgumentException("A credential cannot be empty.", nameof(token));
        }

        var blob = EncodeSecret(token);
        var blobHandle = GCHandle.Alloc(blob, GCHandleType.Pinned);
        try
        {
            var credential = new NativeCredentialIn
            {
                Type = CredTypeGeneric,
                TargetName = _target,
                CredentialBlobSize = blob.Length,
                CredentialBlob = blobHandle.AddrOfPinnedObject(),
                Persist = CredPersistLocalMachine,
                UserName = UserName,
            };
            if (!CredWriteW(ref credential, 0))
            {
                throw new CredentialStoreException(Marshal.GetLastWin32Error());
            }
        }
        finally
        {
            Array.Clear(blob);
            blobHandle.Free();
        }
    }

    public bool Delete()
    {
        if (CredDeleteW(_target, CredTypeGeneric, 0))
        {
            return true;
        }

        var error = Marshal.GetLastWin32Error();
        return error == ErrorNotFound ? false : throw new CredentialStoreException(error);
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

    // The same UTF-16LE form cmdkey writes, so either can read the other.
    public static byte[] EncodeSecret(string token) => Encoding.Unicode.GetBytes(token);

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct NativeCredentialIn
    {
        public int Flags;
        public int Type;
        public string TargetName;
        public string? Comment;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
        public int CredentialBlobSize;
        public IntPtr CredentialBlob;
        public int Persist;
        public int AttributeCount;
        public IntPtr Attributes;
        public string? TargetAlias;
        public string UserName;
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

    [DllImport("advapi32.dll", EntryPoint = "CredWriteW", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool CredWriteW(ref NativeCredentialIn credential, int flags);

    [DllImport("advapi32.dll", EntryPoint = "CredDeleteW", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool CredDeleteW(string target, int type, int flags);
}
