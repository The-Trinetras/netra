using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Input;
using System.Windows.Interop;

namespace Netra.Desktop.Accessibility;

// Registers a system-wide activation hotkey via the Win32 RegisterHotKey
// API (user32.dll) — no NuGet package, this is plain P/Invoke against a
// Windows API already implicitly available to a net10.0-windows WPF app.
// A global hotkey is required because "activation focuses Netra" must work
// even when Netra is not the foreground window (AgentSpec §8: "the
// activation hotkey focuses Netra without opening the microphone").
//
// Push-to-talk is intentionally NOT registered here (see PushToTalkController):
// RegisterHotKey delivers only a single WM_HOTKEY on key-down, with no
// reliable key-up notification, and push-to-talk must stop capture exactly
// when the key is released. A global key-up-aware hook needs
// SetWindowsHookEx, a materially larger-scope native hook; scoping
// push-to-talk to in-window PreviewKeyDown/PreviewKeyUp instead keeps this
// pass's Win32 surface to the one API actually required.
public sealed class GlobalHotKeyService : IDisposable
{
    private const int WmHotKey = 0x0312;

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool RegisterHotKey(IntPtr hWnd, int id, uint fsModifiers, uint vk);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool UnregisterHotKey(IntPtr hWnd, int id);

    private readonly Window _window;
    private readonly int _hotKeyId;
    private HwndSource? _source;
    private bool _registered;

    public GlobalHotKeyService(Window window, int hotKeyId)
    {
        _window = window;
        _hotKeyId = hotKeyId;
    }

    // Raised when the registered hotkey is pressed anywhere in the system.
    public event EventHandler? HotKeyPressed;

    // False means registration failed (most commonly: another running
    // application already claimed this exact combination). This is not
    // treated as fatal — the app remains fully usable by keyboard/mouse
    // inside its own window — but it IS a real gap that must be surfaced,
    // not silently swallowed. See docs/team/handoffs/M5.md Gap 5: exact
    // shortcuts still need a live-Windows conflict test.
    public bool TryRegister(ModifierKeys modifiers, Key key)
    {
        var helper = new WindowInteropHelper(_window);
        var handle = helper.EnsureHandle();

        // One hook, however many combinations are tried: a second hook would
        // raise HotKeyPressed twice per press.
        if (_source is null)
        {
            _source = HwndSource.FromHwnd(handle);
            _source?.AddHook(WndProc);
        }

        var vk = (uint)KeyInterop.VirtualKeyFromKey(key);
        var fsModifiers = ToWin32Modifiers(modifiers);

        _registered = RegisterHotKey(handle, _hotKeyId, fsModifiers, vk);
        return _registered;
    }

    private static uint ToWin32Modifiers(ModifierKeys modifiers)
    {
        // Win32 MOD_* values; WPF's ModifierKeys flags map 1:1 by name but
        // not by numeric value, so translate explicitly rather than cast.
        uint result = 0;
        if (modifiers.HasFlag(ModifierKeys.Alt)) result |= 0x0001;
        if (modifiers.HasFlag(ModifierKeys.Control)) result |= 0x0002;
        if (modifiers.HasFlag(ModifierKeys.Shift)) result |= 0x0004;
        if (modifiers.HasFlag(ModifierKeys.Windows)) result |= 0x0008;
        return result;
    }

    private IntPtr WndProc(IntPtr hwnd, int msg, IntPtr wParam, IntPtr lParam, ref bool handled)
    {
        if (msg == WmHotKey && wParam.ToInt32() == _hotKeyId)
        {
            HotKeyPressed?.Invoke(this, EventArgs.Empty);
            handled = true;
        }

        return IntPtr.Zero;
    }

    public void Dispose()
    {
        if (_registered)
        {
            var helper = new WindowInteropHelper(_window);
            UnregisterHotKey(helper.Handle, _hotKeyId);
        }

        _source?.RemoveHook(WndProc);
    }
}
