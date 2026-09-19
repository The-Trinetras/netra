using Xunit;

namespace Netra.Desktop.Tests;

// For tests that exercise a Windows API or Windows path rules. They run in
// the canonical suite on Windows and are skipped, visibly, where the
// portable test project runs them on macOS or Linux.
public sealed class WindowsOnlyFactAttribute : FactAttribute
{
    public WindowsOnlyFactAttribute()
    {
        if (!OperatingSystem.IsWindows())
        {
            Skip = "Needs Windows (Windows API or Windows path rules).";
        }
    }
}
